import io
import logging

from app.core.log_sanitizer import (
    RedactingFilter,
    RedactingFormatter,
    install_log_redaction,
    sanitize_log_text,
)

APP_SCRIPT_EXEC = "https://script.google.com/macros/s/fake-secret-deployment-id/exec"
APP_SCRIPT_DEV = "https://script.google.com/macros/s/fake-secret-deployment-id/dev?token=fake-token"
ECHO_URL = (
    "https://script.googleusercontent.com/macros/echo?"
    "user_content_key=fake-user-content-key&lib=fake-lib-id"
)


def assert_not_leaked(text: str, *secrets: str) -> None:
    for secret in secrets:
        assert secret not in text


def test_sanitizer_redacts_apps_script_deployment_urls_and_queries():
    sanitized = sanitize_log_text(f"exec={APP_SCRIPT_EXEC} dev={APP_SCRIPT_DEV}")

    assert "https://script.google.com/.../exec" in sanitized
    assert "https://script.google.com/.../dev" in sanitized
    assert_not_leaked(sanitized, "fake-secret-deployment-id", "fake-token")


def test_sanitizer_redacts_googleusercontent_macro_echo_query_values():
    sanitized = sanitize_log_text(ECHO_URL)

    assert "https://script.googleusercontent.com/macros/echo?" in sanitized
    assert_not_leaked(sanitized, "fake-user-content-key", "fake-lib-id")


def test_sanitizer_redacts_generic_sensitive_query_params():
    sanitized = sanitize_log_text("https://example.com/path?api_key=fake-api-key&foo=bar")

    assert "api_key=<redacted>" in sanitized
    assert "foo=bar" in sanitized
    assert "fake-api-key" not in sanitized


def test_sanitizer_redacts_authorization_fragments():
    sanitized = sanitize_log_text(
        "Bearer fake-bearer-token Authorization: Bearer fake-auth-token "
        "Authorization=Bearer fake-auth-equals-token X-API-Key: fake-x-api-key "
        "X-API-Key=fake-x-api-key-2"
    )

    assert "Bearer <redacted>" in sanitized
    assert "Authorization: Bearer <redacted>" in sanitized
    assert "Authorization=Bearer <redacted>" in sanitized
    assert "X-API-Key: <redacted>" in sanitized
    assert "X-API-Key=<redacted>" in sanitized
    assert_not_leaked(
        sanitized,
        "fake-bearer-token",
        "fake-auth-token",
        "fake-auth-equals-token",
        "fake-x-api-key",
        "fake-x-api-key-2",
    )


def test_sanitizer_redacts_key_value_and_dict_like_secrets():
    sanitized = sanitize_log_text(
        'api_key=plain-secret token: plain-token password=plain-password '
        'smtp_password=smtp-secret webhook=https://example.com/hook?token=fake-webhook-token '
        '{"api_key": "fake-json-api-key"} {\'token\': \'fake-dict-token\'}'
    )

    assert_not_leaked(
        sanitized,
        "plain-secret",
        "plain-token",
        "plain-password",
        "smtp-secret",
        "fake-webhook-token",
        "fake-json-api-key",
        "fake-dict-token",
    )


def test_sanitizer_preserves_non_sensitive_text_and_handles_non_strings():
    assert sanitize_log_text("worker cycle completed ok") == "worker cycle completed ok"
    assert "123" in sanitize_log_text(123)
    assert "safe" in sanitize_log_text({"safe": "value"})


def test_sanitizer_never_raises_and_is_idempotent_and_does_not_mutate_inputs():
    class Unprintable:
        def __str__(self):
            raise RuntimeError("boom")

    assert sanitize_log_text(Unprintable())
    text = f"url={APP_SCRIPT_DEV} header=Authorization: Bearer fake-bearer-token"
    assert sanitize_log_text(sanitize_log_text(text)) == sanitize_log_text(text)

    original = {"token": "fake-dict-token", "items": ["fake-list-token"]}
    before = {"token": "fake-dict-token", "items": ["fake-list-token"]}
    sanitize_log_text(original)
    assert original == before


def test_redacting_formatter_sanitizes_final_output_and_exception_traceback():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s")))
    logger = logging.getLogger("tests.redacting.exception")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    try:
        raise RuntimeError(f"failed request {APP_SCRIPT_DEV}")
    except RuntimeError:
        logger.exception("delivery failed token=%s", "fake-arg-token")

    output = stream.getvalue()
    assert "https://script.google.com/.../dev" in output
    assert_not_leaked(output, "fake-secret-deployment-id", "fake-token", "fake-arg-token")


def test_redacting_filter_is_noop_and_does_not_mutate_msg_or_args():
    secret_args = {"url": APP_SCRIPT_EXEC, "token": "fake-dict-token"}
    record = logging.LogRecord(
        "tests.redacting.filter",
        logging.INFO,
        __file__,
        1,
        "request %s %(token)s",
        secret_args,
        None,
    )

    RedactingFilter().filter(record)

    assert record.msg == "request %s %(token)s"
    assert record.args is secret_args
    assert secret_args["url"] == APP_SCRIPT_EXEC
    assert secret_args["token"] == "fake-dict-token"


def test_installed_redaction_does_not_break_percent_style_formatting(capsys):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level
    try:
        root.handlers = [handler]
        root.setLevel(logging.INFO)
        install_log_redaction()
        logging.getLogger("tests.redacting.regression").info(
            "monitor_service.cycle_summary proxy_failures=%s", 0
        )
    finally:
        root.handlers = old_handlers
        root.setLevel(old_level)

    output = stream.getvalue()
    captured = capsys.readouterr()
    assert "monitor_service.cycle_summary proxy_failures=0" in output
    combined = output + captured.out + captured.err
    assert "Message:" not in combined
    assert "Arguments:" not in combined
    assert "Logging error" not in combined


def test_sanitizer_preserves_operational_counters():
    text = (
        "proxy_success_count=1 proxy_failure_count=2 "
        "proxy_quarantine_on_failure_count=3 proxy_failures=0 "
        "searches_processed=4 sessions_opened=5"
    )

    assert sanitize_log_text(text) == text


def test_installed_redaction_wraps_brace_style_formatter():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("{levelname}:{name}:{message}", style="{"))
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level
    try:
        root.handlers = [handler]
        root.setLevel(logging.INFO)
        install_log_redaction()
        logging.getLogger("tests.redacting.brace").info("url=%s", APP_SCRIPT_EXEC)
    finally:
        root.handlers = old_handlers
        root.setLevel(old_level)

    output = stream.getvalue()
    assert "INFO:tests.redacting.brace:url=https://script.google.com/.../exec" in output
    assert "fake-secret-deployment-id" not in output


def test_installed_redaction_wraps_custom_formatter_behavior():
    class PrefixFormatter(logging.Formatter):
        def format(self, record):  # noqa: A003 - logging API name
            return f"CUSTOM::{record.levelname}::{record.getMessage()}"

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(PrefixFormatter())
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level
    try:
        root.handlers = [handler]
        root.setLevel(logging.INFO)
        install_log_redaction()
        logging.getLogger("tests.redacting.custom").info("echo=%s", ECHO_URL)
    finally:
        root.handlers = old_handlers
        root.setLevel(old_level)

    output = stream.getvalue()
    assert output.startswith("CUSTOM::INFO::echo=https://script.googleusercontent.com/macros/echo?")
    assert_not_leaked(output, "fake-user-content-key", "fake-lib-id")


def test_installed_redaction_covers_root_httpx_httpcore_and_worker_style_loggers():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(name)s:%(message)s"))
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    old_level = root.level
    try:
        root.handlers = [handler]
        root.setLevel(logging.INFO)
        install_log_redaction()
        logging.getLogger("httpx").info("GET %s", ECHO_URL)
        logging.getLogger("httpcore").info("connect %s", APP_SCRIPT_DEV)
        logging.getLogger("app.workers.monitor").info("worker url=%s", APP_SCRIPT_EXEC)
    finally:
        root.handlers = old_handlers
        root.setLevel(old_level)

    output = stream.getvalue()
    assert "httpx:" in output
    assert "httpcore:" in output
    assert "app.workers.monitor:" in output
    assert_not_leaked(
        output,
        "fake-user-content-key",
        "fake-lib-id",
        "fake-secret-deployment-id",
        "fake-token",
    )


def test_actual_configured_url_is_not_mutated_by_sanitizer_or_logging():
    configured_url = APP_SCRIPT_DEV
    request_target = {"url": configured_url}

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter(logging.Formatter("%(message)s")))
    logger = logging.getLogger("tests.redacting.request")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("posting to %s", request_target["url"])

    assert request_target["url"] == configured_url
    assert configured_url == APP_SCRIPT_DEV
    output = stream.getvalue()
    assert "https://script.google.com/.../dev" in output
    assert_not_leaked(output, "fake-secret-deployment-id", "fake-token")
