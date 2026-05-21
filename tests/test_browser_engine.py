import pytest
import asyncio
import sys
import subprocess
import logging
from types import ModuleType, SimpleNamespace

from app.parsers.block_signals import looks_like_block_or_captcha
from app.parsers.browser_engine import _is_blocked, _nodriver_proxy_args, _parse_proxy_url, _stop_browser_best_effort
from app.parsers.browser_engine import (
    _CamoufoxSession,
    _NodriverSession,
    fetch_with_camoufox,
    fetch_with_nodriver,
    open_camoufox_session,
    open_nodriver_session,
)


def test_nodriver_proxy_args_no_proxy():
    assert _nodriver_proxy_args(None) == []


def test_nodriver_proxy_args_plain_proxy():
    url = "http://1.2.3.4:8080"

    args = _nodriver_proxy_args(url)

    assert args == ["--proxy-server=http://1.2.3.4:8080"]


def test_nodriver_proxy_args_https_proxy():
    url = "https://1.2.3.4:8080"

    args = _nodriver_proxy_args(url)

    assert args == ["--proxy-server=https://1.2.3.4:8080"]


def test_nodriver_proxy_args_unsupported_scheme_raises():
    url = "socks5://1.2.3.4:1080"

    with pytest.raises(ValueError, match="unsupported proxy scheme"):
        _nodriver_proxy_args(url)


def test_nodriver_proxy_args_proxy_with_at_sign_in_password():
    """URL-encoded @ in password must not confuse the host:port extraction."""
    url = "http://user:p%40ssword@1.2.3.4:8080"

    args = _nodriver_proxy_args(url)

    assert args == ["--proxy-server=http://1.2.3.4:8080"]




def test_nodriver_proxy_args_ipv6_proxy():
    url = "http://[2001:db8::1]:8080"

    args = _nodriver_proxy_args(url)

    assert args == ["--proxy-server=http://[2001:db8::1]:8080"]


def test_nodriver_proxy_args_invalid_port_raises_predictably():
    url = "http://1.2.3.4:abc"

    with pytest.raises(ValueError, match="proxy must include valid port"):
        _nodriver_proxy_args(url)
def test_is_blocked_on_captcha_html():
    html = "<html><body>captcha: verify you are human</body></html>"

    assert _is_blocked("", html) is True


def test_is_blocked_on_normal_html():
    html = "<html><body><div data-marker='item'>Normal listing</div></body></html>"

    assert _is_blocked("", html) is False


def test_shared_block_signal_helper_detects_block_phrase():
    assert looks_like_block_or_captcha("", "verify you are human") is True

def test_parse_proxy_url_without_credentials():
    result = _parse_proxy_url("http://1.2.3.4:8080")

    assert result == {"server": "http://1.2.3.4:8080"}


def test_parse_proxy_url_with_credentials():
    result = _parse_proxy_url("http://user:secret@1.2.3.4:8080")

    assert result == {
        "server": "http://1.2.3.4:8080",
        "username": "user",
        "password": "secret",
    }


def test_parse_proxy_url_with_encoded_at_sign_in_password():
    """URL-encoded @ in password: only the rightmost @ is the user/host separator."""
    result = _parse_proxy_url("http://user:p%40ssword@1.2.3.4:8080")

    assert result["server"] == "http://1.2.3.4:8080"
    assert result["username"] == "user"
    assert result["password"] == "p@ssword"


def test_nodriver_proxy_auth_is_configured_before_first_avito_navigation(monkeypatch):
    events = []

    class FakeTab:
        async def send(self, command):
            if command[0] == "page.script":
                events.append("page.script")
            if command[0] == "fetch.enable":
                events.append("fetch.enable")
            return None

        def add_handler(self, event_type, _handler):
            events.append(f"handler:{event_type}")

    class FakePage:
        async def evaluate(self, script):
            if script == "document.title":
                return "Avito"
            if script == "document.body.innerText":
                return ""
            return 1

        async def get_content(self):
            return "<html></html>"

    class FakeBrowser:
        def __init__(self):
            self.main_tab = None
            self._tab = FakeTab()

        async def get(self, nav_url):
            if nav_url == "about:blank":
                self.main_tab = self._tab
                events.append("nav:about:blank")
            elif "avito.ru" in nav_url:
                events.append(f"nav:{nav_url}")
            return FakePage()

        def stop(self):
            return None

    fake_uc = ModuleType("nodriver")

    async def fake_start(*, headless, browser_args):
        assert isinstance(headless, bool)
        assert browser_args
        return FakeBrowser()

    fake_uc.start = fake_start
    fake_uc.cdp = SimpleNamespace(
        page=SimpleNamespace(add_script_to_evaluate_on_new_document=lambda source: ("page.script", source)),
        fetch=SimpleNamespace(
            enable=lambda **kwargs: ("fetch.enable", kwargs),
            continue_with_auth=lambda **kwargs: ("fetch.continue", kwargs),
            AuthRequired="AuthRequired",
            AuthChallengeResponse=lambda **kwargs: kwargs,
        ),
    )
    monkeypatch.setitem(sys.modules, "nodriver", fake_uc)
    async def _fast_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.parsers.browser_engine.asyncio.sleep", _fast_sleep)

    result = asyncio.run(
        fetch_with_nodriver("https://www.avito.ru/moskva/kvartiry", "http://user:pass@1.2.3.4:8080")
    )

    assert result["ok"] is True
    assert "page.script" in events
    assert "fetch.enable" in events
    assert events.index("page.script") < events.index("nav:https://www.avito.ru/")
    assert events.index("fetch.enable") < events.index("nav:https://www.avito.ru/")


def test_nodriver_stealth_setup_failure_is_non_fatal(monkeypatch):
    class FakeTab:
        async def send(self, command):
            if command[0] == "page.script":
                raise RuntimeError("stealth failed")
            return None

        def add_handler(self, _event_type, _handler):
            return None

    class FakeBrowser:
        def __init__(self):
            self.main_tab = FakeTab()

        async def get(self, _url):
            return None

        def stop(self):
            return None

    fake_uc = ModuleType("nodriver")

    async def fake_start(*, headless, browser_args):
        return FakeBrowser()

    fake_uc.start = fake_start
    fake_uc.cdp = SimpleNamespace(
        page=SimpleNamespace(add_script_to_evaluate_on_new_document=lambda source: ("page.script", source)),
        fetch=SimpleNamespace(AuthRequired="AuthRequired", enable=lambda **kwargs: ("fetch.enable", kwargs), AuthChallengeResponse=lambda **kwargs: kwargs, continue_with_auth=lambda **kwargs: kwargs),
    )
    monkeypatch.setitem(sys.modules, "nodriver", fake_uc)

    session = asyncio.run(open_nodriver_session(None))
    assert session is not None


def test_open_camoufox_session_sets_geolocation(monkeypatch):
    events = []

    class FakeContext:
        async def grant_permissions(self, perms):
            events.append(("grant", perms))

        async def set_geolocation(self, geo):
            events.append(("geo", geo))

    class FakePage:
        def __init__(self):
            self.context = FakeContext()

        async def add_init_script(self, _script):
            events.append("init")

    class FakeBrowser:
        async def new_page(self):
            return FakePage()

    class FakeAsyncCamoufox:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return FakeBrowser()

        async def __aexit__(self, *_args):
            events.append("exit")

    fake_mod = ModuleType("camoufox.async_api")
    fake_mod.AsyncCamoufox = FakeAsyncCamoufox
    monkeypatch.setitem(sys.modules, "camoufox.async_api", fake_mod)

    session = asyncio.run(open_camoufox_session(None))
    assert session is not None
    assert ("grant", ["geolocation"]) in events
    assert any(e[0] == "geo" for e in events if isinstance(e, tuple))


def test_open_camoufox_session_setup_failure_calls_aexit(monkeypatch):
    events = []

    class BrokenBrowser:
        async def new_page(self):
            raise RuntimeError("new_page failed")

    class FakeAsyncCamoufox:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return BrokenBrowser()

        async def __aexit__(self, *_args):
            events.append("exit")

    fake_mod = ModuleType("camoufox.async_api")
    fake_mod.AsyncCamoufox = FakeAsyncCamoufox
    monkeypatch.setitem(sys.modules, "camoufox.async_api", fake_mod)

    try:
        asyncio.run(open_camoufox_session(None))
    except RuntimeError:
        pass

    assert "exit" in events


def test_open_nodriver_session_stops_browser_when_setup_fails(monkeypatch):
    events = []

    class FakeBrowser:
        def __init__(self):
            self.main_tab = None

        async def get(self, nav_url):
            if nav_url == "about:blank":
                raise RuntimeError("about blank failed")
            return None

        def stop(self):
            events.append("stop")

    fake_uc = ModuleType("nodriver")

    async def fake_start(*, headless, browser_args):
        return FakeBrowser()

    fake_uc.start = fake_start
    fake_uc.cdp = SimpleNamespace(
        page=SimpleNamespace(add_script_to_evaluate_on_new_document=lambda source: ("page.script", source)),
        fetch=SimpleNamespace(AuthRequired="AuthRequired", enable=lambda **kwargs: ("fetch.enable", kwargs), AuthChallengeResponse=lambda **kwargs: kwargs, continue_with_auth=lambda **kwargs: kwargs),
    )
    monkeypatch.setitem(sys.modules, "nodriver", fake_uc)

    with pytest.raises(RuntimeError):
        asyncio.run(open_nodriver_session(None))

    assert "stop" in events


def test_open_nodriver_session_about_blank_timeout_is_cleaned_up(monkeypatch):
    events = []

    class FakeBrowser:
        def __init__(self):
            self.main_tab = None

        async def get(self, nav_url):
            if nav_url == "about:blank":
                raise asyncio.TimeoutError()
            return None

        def stop(self):
            events.append("stop")

    fake_uc = ModuleType("nodriver")

    async def fake_start(*, headless, browser_args):
        return FakeBrowser()

    fake_uc.start = fake_start
    fake_uc.cdp = SimpleNamespace(
        page=SimpleNamespace(add_script_to_evaluate_on_new_document=lambda source: ("page.script", source)),
        fetch=SimpleNamespace(AuthRequired="AuthRequired", enable=lambda **kwargs: ("fetch.enable", kwargs), AuthChallengeResponse=lambda **kwargs: kwargs, continue_with_auth=lambda **kwargs: kwargs),
    )
    monkeypatch.setitem(sys.modules, "nodriver", fake_uc)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(open_nodriver_session(None))

    assert "stop" in events


def test_nodriver_session_warmup_runs_once_per_session(monkeypatch):
    events = []

    class FakePage:
        async def evaluate(self, script):
            if script == "document.title":
                return "Avito"
            if script == "document.body.innerText":
                return ""
            if "querySelectorAll" in script:
                return 2
            return None

        async def get_content(self):
            return "<html></html>"

    class FakeBrowser:
        async def get(self, nav_url):
            events.append(nav_url)
            return FakePage()

        def stop(self):
            return None

    async def _fast_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.parsers.browser_engine.asyncio.sleep", _fast_sleep)
    session = _NodriverSession(uc_module=SimpleNamespace(), browser=FakeBrowser())

    first = asyncio.run(session.fetch("https://www.avito.ru/a"))
    second = asyncio.run(session.fetch("https://www.avito.ru/b"))

    assert first["ok"] is True
    assert second["ok"] is True
    assert events == [
        "https://www.avito.ru/",
        "https://www.avito.ru/a",
        "https://www.avito.ru/b",
    ]


def test_camoufox_session_warmup_runs_once_per_session(monkeypatch):
    events = []

    class FakeLocator:
        @property
        def first(self):
            return self

        async def text_content(self):
            return ""

        async def count(self):
            return 3

    class FakePage:
        async def goto(self, url, wait_until, timeout=None):
            events.append((url, wait_until))

        async def title(self):
            return "Avito"

        def locator(self, _selector):
            return FakeLocator()

        async def content(self):
            return "<html></html>"

    class FakeBrowser:
        async def __aexit__(self, *_args):
            return None

    async def _fast_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.parsers.browser_engine.asyncio.sleep", _fast_sleep)
    session = _CamoufoxSession(browser=FakeBrowser(), page=FakePage())

    first = asyncio.run(session.fetch("https://www.avito.ru/a"))
    second = asyncio.run(session.fetch("https://www.avito.ru/b"))

    assert first["ok"] is True
    assert second["ok"] is True
    assert events == [
        ("https://www.avito.ru/", "domcontentloaded"),
        ("https://www.avito.ru/a", "domcontentloaded"),
        ("https://www.avito.ru/b", "domcontentloaded"),
    ]


def test_nodriver_warmup_failure_returns_controlled_result(monkeypatch):
    class BrokenBrowser:
        async def get(self, url):
            if url == "https://www.avito.ru/":
                raise RuntimeError("warmup failed")
            return None

        def stop(self):
            return None

    session = _NodriverSession(uc_module=SimpleNamespace(), browser=BrokenBrowser())
    result = asyncio.run(session.fetch("https://www.avito.ru/a"))

    assert result["ok"] is False
    assert result["engine"] == "nodriver"
    assert result["error_type"] == "exception"
    assert "warmup failed" in result["error"]


def test_fetch_with_nodriver_closes_session_after_one_call(monkeypatch):
    events = []

    class FakeSession:
        async def fetch(self, _url):
            return {"ok": True, "engine": "nodriver", "html": "<html></html>", "cards_count": 1}

        async def close(self):
            events.append("closed")

    fake_uc = ModuleType("nodriver")
    monkeypatch.setitem(sys.modules, "nodriver", fake_uc)

    async def fake_open(_proxy):
        return FakeSession()

    monkeypatch.setattr("app.parsers.browser_engine.open_nodriver_session", fake_open)
    result = asyncio.run(fetch_with_nodriver("https://www.avito.ru/a", None))

    assert result["ok"] is True
    assert events == ["closed"]


def test_fetch_with_nodriver_returns_controlled_timeout_on_setup_timeout(monkeypatch):
    fake_uc = ModuleType("nodriver")
    monkeypatch.setitem(sys.modules, "nodriver", fake_uc)

    async def fake_open(_proxy):
        raise asyncio.TimeoutError()

    monkeypatch.setattr("app.parsers.browser_engine.open_nodriver_session", fake_open)
    result = asyncio.run(fetch_with_nodriver("https://www.avito.ru/a", None))
    assert result["ok"] is False
    assert result["error_type"] == "timeout"


def test_fetch_with_camoufox_closes_session_after_one_call(monkeypatch):
    events = []

    class FakeSession:
        async def fetch(self, _url):
            return {"ok": True, "engine": "camoufox", "html": "<html></html>", "cards_count": 1}

        async def close(self):
            events.append("closed")

    fake_mod = ModuleType("camoufox.async_api")
    fake_mod.AsyncCamoufox = object()
    monkeypatch.setitem(sys.modules, "camoufox.async_api", fake_mod)

    async def fake_open(_proxy):
        return FakeSession()

    monkeypatch.setattr("app.parsers.browser_engine.open_camoufox_session", fake_open)
    result = asyncio.run(fetch_with_camoufox("https://www.avito.ru/a", None))

    assert result["ok"] is True
    assert events == ["closed"]


def test_humanize_disabled_has_no_scroll_calls(monkeypatch):
    events = []

    class FakePage:
        async def evaluate(self, script):
            if script == "document.title":
                return "Avito"
            if script == "document.body.innerText":
                return ""
            if script == "document.querySelectorAll('[data-marker=\"item\"]').length":
                return 1
            if script.startswith("window.scrollBy"):
                events.append("scroll")
            return None

        async def get_content(self):
            return "<html></html>"

    class FakeBrowser:
        async def get(self, _url):
            return FakePage()

        def stop(self):
            return None

    async def _fast_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.parsers.browser_engine.settings.scrape_humanize", False)
    monkeypatch.setattr("app.parsers.browser_engine.asyncio.sleep", _fast_sleep)
    session = _NodriverSession(uc_module=SimpleNamespace(), browser=FakeBrowser())
    result = asyncio.run(session.fetch("https://www.avito.ru/a"))

    assert result["ok"] is True
    assert events == []


def test_humanize_enabled_scrolls_after_navigation_nodriver(monkeypatch):
    events = []

    class FakePage:
        async def evaluate(self, script):
            if script == "document.title":
                return "Avito"
            if script == "document.body.innerText":
                return ""
            if script == "document.querySelectorAll('[data-marker=\"item\"]').length":
                return 1
            if script.startswith("window.scrollBy"):
                events.append("scroll")
            return None

        async def get_content(self):
            return "<html></html>"

    class FakeBrowser:
        async def get(self, url):
            events.append(f"nav:{url}")
            return FakePage()

        def stop(self):
            return None

    async def _fast_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.parsers.browser_engine.settings.scrape_humanize", True)
    monkeypatch.setattr("app.parsers.browser_engine.asyncio.sleep", _fast_sleep)
    monkeypatch.setattr("app.parsers.browser_engine.random.randint", lambda _a, _b: 1)
    session = _NodriverSession(uc_module=SimpleNamespace(), browser=FakeBrowser())
    result = asyncio.run(session.fetch("https://www.avito.ru/a"))

    assert result["ok"] is True
    assert "scroll" in events
    assert events.index("nav:https://www.avito.ru/a") < events.index("scroll")


def test_humanize_exception_is_logged_and_non_fatal_camoufox(monkeypatch, caplog):
    class FakeMouse:
        async def wheel(self, _x, _y):
            raise RuntimeError("wheel failed")

    class FakeLocator:
        @property
        def first(self):
            return self

        async def text_content(self):
            return ""

        async def count(self):
            return 1

    class FakePage:
        def __init__(self):
            self.mouse = FakeMouse()

        async def goto(self, _url, wait_until, timeout=None):
            return wait_until

        async def title(self):
            return "Avito"

        def locator(self, _selector):
            return FakeLocator()

        async def content(self):
            return "<html></html>"

    class FakeBrowser:
        async def __aexit__(self, *_args):
            return None

    async def _fast_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.parsers.browser_engine.settings.scrape_humanize", True)
    monkeypatch.setattr("app.parsers.browser_engine.asyncio.sleep", _fast_sleep)
    monkeypatch.setattr("app.parsers.browser_engine.random.randint", lambda _a, _b: 1)
    session = _CamoufoxSession(browser=FakeBrowser(), page=FakePage())

    result = asyncio.run(session.fetch("https://www.avito.ru/a"))

    assert result["ok"] is True
    assert "camoufox humanize failed" in caplog.text


def test_stop_browser_best_effort_supports_sync_stop():
    events = []

    class SyncBrowser:
        def stop(self):
            events.append("stop")
            return None

    asyncio.run(_stop_browser_best_effort(SyncBrowser()))

    assert events == ["stop"]


def test_stop_browser_best_effort_awaits_async_stop_result():
    events = []

    class AsyncStopResult:
        def __await__(self):
            async def _inner():
                events.append("awaited")

            return _inner().__await__()

    class AsyncBrowser:
        def stop(self):
            events.append("stop")
            return AsyncStopResult()

    asyncio.run(_stop_browser_best_effort(AsyncBrowser()))

    assert events == ["stop", "awaited"]


def test_stop_browser_best_effort_performs_event_loop_drain(monkeypatch):
    events = []

    class Browser:
        def stop(self):
            events.append("stop")
            return None

    async def _sleep(delay, *_args, **_kwargs):
        events.append(f"sleep:{delay}")
        return None

    monkeypatch.setattr("app.parsers.browser_engine.asyncio.sleep", _sleep)
    asyncio.run(_stop_browser_best_effort(Browser()))

    assert events == ["stop", "sleep:0", "sleep:0.1"]


def test_stop_browser_best_effort_logs_failure_non_fatal(caplog):
    class BrokenBrowser:
        def stop(self):
            raise RuntimeError("stop failed")

    asyncio.run(_stop_browser_best_effort(BrokenBrowser()))

    assert "nodriver stop failed" in caplog.text


def test_stop_browser_best_effort_logs_timeout_non_fatal(monkeypatch, caplog):
    class NeverFinishes:
        def __await__(self):
            async def _inner():
                await asyncio.sleep(3600)

            return _inner().__await__()

    class Browser:
        def stop(self):
            return NeverFinishes()

    monkeypatch.setattr("app.parsers.browser_engine.settings.scrape_timeout_ms", 10)
    asyncio.run(_stop_browser_best_effort(Browser()))

    assert "nodriver stop timed out" in caplog.text


def test_stop_browser_best_effort_cleans_asyncio_process_owned_handle():
    events = []

    class FakeAsyncioProcess:
        def __init__(self):
            self.returncode = None
            self.wait_calls = 0

        def terminate(self):
            events.append("terminate")

        def kill(self):
            events.append("kill")
            self.returncode = 0

        async def wait(self):
            self.wait_calls += 1
            events.append(f"wait:{self.wait_calls}")
            if self.wait_calls == 1:
                await asyncio.sleep(3600)
            self.returncode = 0
            return 0

    class Browser:
        def __init__(self):
            self.proc = FakeAsyncioProcess()

        def stop(self):
            events.append("stop")
            return None

    asyncio_process_type = asyncio.subprocess.Process
    asyncio.subprocess.Process = FakeAsyncioProcess
    try:
        asyncio.run(_stop_browser_best_effort(Browser()))
    finally:
        asyncio.subprocess.Process = asyncio_process_type

    assert events == ["stop", "terminate", "wait:1", "kill", "wait:2"]


def test_stop_browser_best_effort_cleans_popen_owned_handle():
    events = []

    class FakePopen:
        def __init__(self):
            self.done = False

        def poll(self):
            return 0 if self.done else None

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout=None):
            events.append(f"wait:{timeout}")
            if not self.done:
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
            return 0

        def kill(self):
            events.append("kill")
            self.done = True

    class Browser:
        def __init__(self):
            self.popen = FakePopen()

        def stop(self):
            events.append("stop")
            return None

    popen_type = subprocess.Popen
    subprocess.Popen = FakePopen
    try:
        asyncio.run(_stop_browser_best_effort(Browser()))
    finally:
        subprocess.Popen = popen_type

    assert events == ["stop", "terminate", "wait:5.0", "kill", "wait:5.0"]


def test_stop_browser_best_effort_no_process_handle_path(caplog):
    class Browser:
        def stop(self):
            return None

    with caplog.at_level(logging.DEBUG):
        asyncio.run(_stop_browser_best_effort(Browser()))
    assert "no process-like attributes" in caplog.text


def test_stop_browser_best_effort_cleanup_failures_non_fatal(caplog):
    class FakePopen:
        def poll(self):
            return None

        def terminate(self):
            raise RuntimeError("boom")

        def wait(self, timeout=None):
            raise RuntimeError("boom")

        def kill(self):
            raise RuntimeError("boom")

    class Browser:
        def __init__(self):
            self.popen = FakePopen()

        def stop(self):
            return None

    popen_type = subprocess.Popen
    subprocess.Popen = FakePopen
    try:
        asyncio.run(_stop_browser_best_effort(Browser()))
    finally:
        subprocess.Popen = popen_type

    assert "nodriver popen terminate failed" in caplog.text


def test_open_nodriver_session_stop_failure_is_logged_non_fatal(monkeypatch, caplog):
    class FakeBrowser:
        def __init__(self):
            self.main_tab = None

        async def get(self, nav_url):
            if nav_url == "about:blank":
                raise RuntimeError("about blank failed")
            return None

        def stop(self):
            raise RuntimeError("stop failed")

    fake_uc = ModuleType("nodriver")

    async def fake_start(*, headless, browser_args):
        return FakeBrowser()

    fake_uc.start = fake_start
    fake_uc.cdp = SimpleNamespace(
        page=SimpleNamespace(add_script_to_evaluate_on_new_document=lambda source: ("page.script", source)),
        fetch=SimpleNamespace(AuthRequired="AuthRequired", enable=lambda **kwargs: ("fetch.enable", kwargs), AuthChallengeResponse=lambda **kwargs: kwargs, continue_with_auth=lambda **kwargs: kwargs),
    )
    monkeypatch.setitem(sys.modules, "nodriver", fake_uc)

    with pytest.raises(RuntimeError, match="about blank failed"):
        asyncio.run(open_nodriver_session(None))

    assert "nodriver stop failed" in caplog.text




def test_nodriver_session_close_is_idempotent():
    class Browser:
        def __init__(self):
            self.calls = 0

        def stop(self):
            self.calls += 1
            return None

    browser = Browser()
    session = _NodriverSession(uc_module=SimpleNamespace(), browser=browser)

    async def _run():
        await session.close()
        await session.close()

    asyncio.run(_run())
    assert browser.calls == 1


def test_nodriver_warmup_timeout_closes_session_before_return(monkeypatch):
    events = []

    class Browser:
        async def get(self, _url):
            raise asyncio.TimeoutError()

        def stop(self):
            events.append("stop")
            return None

    async def _fast_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.parsers.browser_engine.asyncio.sleep", _fast_sleep)
    session = _NodriverSession(uc_module=SimpleNamespace(), browser=Browser())
    result = asyncio.run(session.fetch("https://www.avito.ru/a"))

    assert result["ok"] is False
    assert result["error_type"] == "timeout"
    assert session.broken is True
    assert events == ["stop"]


def test_nodriver_target_timeout_closes_session_before_return(monkeypatch):
    events = []

    class Browser:
        def __init__(self):
            self.calls = 0

        async def get(self, _url):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace()
            raise asyncio.TimeoutError()

        def stop(self):
            events.append("stop")
            return None

    async def _fast_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.parsers.browser_engine.asyncio.sleep", _fast_sleep)
    session = _NodriverSession(uc_module=SimpleNamespace(), browser=Browser())
    result = asyncio.run(session.fetch("https://www.avito.ru/a"))

    assert result["ok"] is False
    assert result["error_type"] == "timeout"
    assert session.broken is True
    assert events == ["stop"]

def test_nodriver_warmup_navigation_timeout_returns_controlled_timeout():
    class BrokenBrowser:
        async def get(self, _url):
            raise asyncio.TimeoutError()

        def stop(self):
            return None

    session = _NodriverSession(uc_module=SimpleNamespace(), browser=BrokenBrowser())
    result = asyncio.run(session.fetch("https://www.avito.ru/a"))
    assert result["ok"] is False
    assert result["error_type"] == "timeout"


def test_nodriver_target_navigation_timeout_returns_controlled_timeout(monkeypatch):
    class Browser:
        def __init__(self):
            self.calls = 0

        async def get(self, _url):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace()
            raise asyncio.TimeoutError()

        def stop(self):
            return None

    async def _fast_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.parsers.browser_engine.asyncio.sleep", _fast_sleep)
    session = _NodriverSession(uc_module=SimpleNamespace(), browser=Browser())
    result = asyncio.run(session.fetch("https://www.avito.ru/a"))
    assert result["ok"] is False
    assert result["error_type"] == "timeout"


def test_camoufox_warmup_timeout_classified_as_timeout():
    class TimeoutPage:
        async def goto(self, *_args, **_kwargs):
            raise RuntimeError("Page.goto: Timeout 30000ms exceeded")

    session = _CamoufoxSession(browser=SimpleNamespace(), page=TimeoutPage())
    result = asyncio.run(session.fetch("https://www.avito.ru/a"))
    assert result["ok"] is False
    assert result["error_type"] == "timeout"


def test_camoufox_target_timeout_classified_as_timeout(monkeypatch):
    class FakePage:
        def __init__(self):
            self.calls = 0

        async def goto(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("Page.goto: Timeout 30000ms exceeded")

    async def _fast_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.parsers.browser_engine.asyncio.sleep", _fast_sleep)
    session = _CamoufoxSession(browser=SimpleNamespace(), page=FakePage())
    result = asyncio.run(session.fetch("https://www.avito.ru/a"))
    assert result["ok"] is False
    assert result["error_type"] == "timeout"


def test_open_camoufox_session_headless_virtual_only_linux_and_geoip_with_proxy(monkeypatch):
    captured = {}

    class FakeContext:
        async def grant_permissions(self, _perms):
            return None

        async def set_geolocation(self, _geo):
            return None

    class FakePage:
        def __init__(self):
            self.context = FakeContext()

        async def add_init_script(self, _script):
            return None

    class FakeBrowser:
        async def new_page(self):
            return FakePage()

    class FakeAsyncCamoufox:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return FakeBrowser()

        async def __aexit__(self, *_args):
            return None

    fake_mod = ModuleType("camoufox.async_api")
    fake_mod.AsyncCamoufox = FakeAsyncCamoufox
    monkeypatch.setitem(sys.modules, "camoufox.async_api", fake_mod)
    monkeypatch.setattr("app.parsers.browser_engine.settings.scrape_headless", True)
    monkeypatch.setattr("app.parsers.browser_engine.platform.system", lambda: "Darwin")

    session = asyncio.run(open_camoufox_session("http://user:pass@1.2.3.4:8080"))
    assert session is not None
    assert captured["headless"] is True
    assert captured["geoip"] is True


def test_open_camoufox_session_headless_virtual_on_linux_no_geoip_without_proxy(monkeypatch):
    captured = {}

    class FakeContext:
        async def grant_permissions(self, _perms):
            return None

        async def set_geolocation(self, _geo):
            return None

    class FakePage:
        def __init__(self):
            self.context = FakeContext()

        async def add_init_script(self, _script):
            return None

    class FakeBrowser:
        async def new_page(self):
            return FakePage()

    class FakeAsyncCamoufox:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return FakeBrowser()

        async def __aexit__(self, *_args):
            return None

    fake_mod = ModuleType("camoufox.async_api")
    fake_mod.AsyncCamoufox = FakeAsyncCamoufox
    monkeypatch.setitem(sys.modules, "camoufox.async_api", fake_mod)
    monkeypatch.setattr("app.parsers.browser_engine.settings.scrape_headless", True)
    monkeypatch.setattr("app.parsers.browser_engine.platform.system", lambda: "Linux")

    session = asyncio.run(open_camoufox_session(None))
    assert session is not None
    assert captured["headless"] == "virtual"
    assert "geoip" not in captured
