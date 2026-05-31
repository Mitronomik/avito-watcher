from enum import Enum


class ParserErrorType(str, Enum):
    NAVIGATION_TIMEOUT = "navigation_timeout"
    EMPTY_RESULTS = "empty_results"
    POSSIBLE_CAPTCHA_OR_BLOCK = "possible_captcha_or_block"
    BROWSER_DRIVER_CRASH = "browser_driver_crash"
    PROXY_UNAVAILABLE = "proxy_unavailable"
    LAYOUT_CHANGED = "layout_changed"
    INVALID_URL = "invalid_url"


class ParserError(RuntimeError):
    def __init__(self, error_type: ParserErrorType, message: str) -> None:
        self.error_type = error_type
        super().__init__(message)

    def __str__(self) -> str:
        message = super().__str__()
        return f"{self.error_type.value}: {message}" if message else self.error_type.value
