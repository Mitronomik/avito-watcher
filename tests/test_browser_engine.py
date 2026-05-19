import pytest
import asyncio
import sys
from types import ModuleType, SimpleNamespace

from app.parsers.block_signals import looks_like_block_or_captcha
from app.parsers.browser_engine import _is_blocked, _nodriver_proxy_args, _parse_proxy_url
from app.parsers.browser_engine import fetch_with_nodriver, open_camoufox_session, open_nodriver_session


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


def test_nodriver_proxy_args_unsupported_scheme_logs_warning(caplog):
    url = "socks5://1.2.3.4:1080"

    args = _nodriver_proxy_args(url)

    assert args == []
    assert "unsupported proxy scheme" in caplog.text


def test_nodriver_proxy_args_proxy_with_at_sign_in_password():
    """URL-encoded @ in password must not confuse the host:port extraction."""
    url = "http://user:p%40ssword@1.2.3.4:8080"

    args = _nodriver_proxy_args(url)

    assert args == ["--proxy-server=http://1.2.3.4:8080"]


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
    assert result["password"] == "p%40ssword"


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
