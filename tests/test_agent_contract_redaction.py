from __future__ import annotations

from copy import deepcopy

from app.api.admin_v1.redaction import REDACTED, redact_api_response


def test_canonical_redaction_redacts_nested_secret_like_keys_and_patterns():
    original = {
        "nested": {
            "api_key": "abc",
            "token_value": "tok",
            "secret": "sec",
            "password": "pw",
            "authorization": "Bearer aaa.bbb",
            "cookie": "session=1",
            "set-cookie": "session=2",
            "webhook": "https://example.test/hook/secret",
            "smtp_password": "smtp",
            "telegram_bot_token": "tg",
            "openai_api_key": "openai",
            "database_url": "postgres://user:pass@host/db",
            "provider_raw_payload": {"x": "y"},
        },
        "headers": ["Authorization: Bearer abc.def", "Cookie: session=abc", "X-API-Key: abc", "Bearer abc.def"],
        "url": "https://example.test/path?token=abc&safe=1",
        "safe_limitations": [
            "not_investment_advice",
            "readiness_is_not_action_authorization",
            "recommendation_scope_internal_workflow",
            "not_certified_appraisal",
            "not_valuation_report",
        ],
    }
    before = deepcopy(original)
    redacted = redact_api_response(original)

    assert original == before
    nested = redacted["nested"]
    for value in nested.values():
        assert value == REDACTED
    assert redacted["headers"] == [REDACTED, REDACTED, REDACTED, REDACTED]
    assert "token=%5BREDACTED%5D" in redacted["url"]
    assert redacted["safe_limitations"] == original["safe_limitations"]
    assert redact_api_response(original) == redacted
