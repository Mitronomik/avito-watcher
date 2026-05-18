from app.parsers.browser_engine import _is_blocked, _nodriver_proxy_args, _parse_proxy_url


def test_nodriver_proxy_args_no_proxy():
    assert _nodriver_proxy_args(None) == []


def test_nodriver_proxy_args_plain_proxy():
    url = "http://1.2.3.4:8080"

    args = _nodriver_proxy_args(url)

    assert args == ["--proxy-server=http://1.2.3.4:8080"]


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
