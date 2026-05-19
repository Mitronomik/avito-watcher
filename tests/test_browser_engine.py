import asyncio
import sys
from types import ModuleType, SimpleNamespace

from app.parsers.block_signals import looks_like_block_or_captcha
from app.parsers.browser_engine import _is_blocked, _nodriver_proxy_args, _parse_proxy_url
from app.parsers.browser_engine import fetch_with_nodriver


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
