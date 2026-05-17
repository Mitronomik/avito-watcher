import pytest

from app.parsers.browser_engine import _is_blocked, _nodriver_proxy_args


def test_nodriver_proxy_args_no_proxy():
    with pytest.raises(TypeError):
        _nodriver_proxy_args(None)


def test_nodriver_proxy_args_plain_proxy():
    url = "http://1.2.3.4:8080"

    args = _nodriver_proxy_args(url)

    assert args == ["--proxy-server=http://1.2.3.4:8080"]


def test_nodriver_proxy_args_proxy_with_at_sign_in_password():
    url = "http://user:p%40ssword@1.2.3.4:8080"

    _nodriver_proxy_args(url)


def test_is_blocked_on_captcha_html():
    html = "<html><body>captcha: verify you are human</body></html>"

    assert _is_blocked("", html) is True


def test_is_blocked_on_normal_html():
    html = "<html><body><div data-marker='item'>Normal listing</div></body></html>"

    assert _is_blocked("", html) is False
