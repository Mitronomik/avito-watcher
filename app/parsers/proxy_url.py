"""Shared proxy URL parsing/validation helpers."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

_SUPPORTED_PROXY_SCHEMES = {"http", "https"}


@dataclass(frozen=True)
class ParsedProxy:
    raw_url: str
    scheme: str
    server: str
    hostport: str
    username: str | None
    password: str | None


def parse_proxy_url(proxy_url: str) -> ParsedProxy:
    parsed = urlsplit(proxy_url)
    scheme = parsed.scheme.lower()
    if scheme not in _SUPPORTED_PROXY_SCHEMES:
        raise ValueError(f"unsupported proxy scheme: {scheme or '<empty>'}")
    if not parsed.hostname or parsed.port is None:
        raise ValueError("proxy must include host and port")

    hostport = parsed.netloc.rsplit("@", 1)[-1]
    server = f"{scheme}://{hostport}"
    username = unquote(parsed.username) if parsed.username is not None else None
    password = unquote(parsed.password) if parsed.password is not None else None
    return ParsedProxy(
        raw_url=proxy_url,
        scheme=scheme,
        server=server,
        hostport=hostport,
        username=username,
        password=password,
    )


def validate_proxy_urls(proxy_urls: list[str]) -> list[str]:
    valid: list[str] = []
    for proxy_url in proxy_urls:
        parse_proxy_url(proxy_url)
        valid.append(proxy_url)
    return valid
