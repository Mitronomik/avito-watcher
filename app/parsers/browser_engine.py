"""Stealth browser fetch backends: nodriver (fast-path) and camoufox (fallback)."""
from __future__ import annotations

import asyncio
import gc
import logging
import inspect
import platform
import random
import subprocess
from typing import Optional

from app.core.config import settings
from app.parsers.block_signals import looks_like_block_or_captcha
from app.parsers.proxy_url import parse_proxy_url

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JS stealth init-script — patches navigator properties detectable by Avito UBA.
# Applied before any navigation so the very first request is already patched.
# ---------------------------------------------------------------------------
_STEALTH_INIT_SCRIPT = """
(() => {
  // 1. webdriver — undefined beats false (false itself is detectable)
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });

  // 2. plugins — headless has 0; spoof 3 common Chrome plugins
  const _plugins = [
    { name: 'PDF Viewer',         filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer',  filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    { name: 'Chromium PDF Viewer',filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
  ];
  Object.defineProperty(navigator, 'plugins', {
    get: () => Object.assign(_plugins, { item: i => _plugins[i], refresh: () => {} }),
    configurable: true,
  });

  // 3. languages — match proxy locale (Russian)
  Object.defineProperty(navigator, 'language',  { get: () => 'ru-RU', configurable: true });
  Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US'], configurable: true });

  // 4. window.chrome — required; headless Chrome has no chrome.runtime.id
  if (!window.chrome) {
    window.chrome = {
      app: { isInstalled: false },
      runtime: {
        id: undefined,
        connect:     () => {},
        sendMessage: () => {},
      },
      loadTimes: () => ({
        requestTime:   Date.now() / 1000,
        startLoadTime: Date.now() / 1000,
        commitLoadTime: Date.now() / 1000,
        finishLoadTime: 0,
        firstPaintTime: 0,
        navigationType: 'Other',
        wasNpnNegotiated: false,
      }),
      csi: () => ({
        startE:  Date.now(),
        onloadT: Date.now(),
        pageT:   3000 + Math.random() * 1000,
        tran:    15,
      }),
    };
  }

  // 5. permissions — 'notifications' must return real Notification.permission
  const _origQuery = window.navigator.permissions.query.bind(navigator.permissions);
  window.navigator.permissions.query = (p) =>
    p.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : _origQuery(p);

  // 6. WebGL — spoof Intel GPU strings (common Russian laptop)
  // Patches both WebGL contexts (v1 and v2)
  function _patchWebGL(ctx) {
    const _getParam = ctx.prototype.getParameter;
    ctx.prototype.getParameter = function (param) {
      if (param === 37445) return 'Intel Inc.';
      if (param === 37446) return 'Intel Iris OpenGL Engine';
      return _getParam.call(this, param);
    };
  }
  _patchWebGL(WebGLRenderingContext);
  if (typeof WebGL2RenderingContext !== 'undefined') _patchWebGL(WebGL2RenderingContext);
})();
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_blocked(title: str, body: str) -> bool:
    return looks_like_block_or_captcha(title, body, body_limit=3000)


def _parse_proxy_url(proxy_url: str) -> dict:
    """Parse proxy URL into camoufox proxy dict with decoded credentials."""
    parsed = parse_proxy_url(proxy_url)
    result = {"server": parsed.server}
    if parsed.username is not None and parsed.password is not None:
        result["username"] = parsed.username
        result["password"] = parsed.password
    return result


def _nodriver_proxy_args(proxy_url: str | None) -> list[str]:
    """Return --proxy-server arg list for nodriver launch."""
    if proxy_url is None:
        return []
    parsed = parse_proxy_url(proxy_url)
    return [f"--proxy-server={parsed.scheme}://{parsed.hostport}"]


def _is_humanize_enabled() -> bool:
    return settings.scrape_humanize


def _timeout_seconds() -> float:
    return max(settings.scrape_timeout_ms / 1000, 0.1)


def _timeout_result(engine: str, phase: str) -> dict:
    timeout_ms = settings.scrape_timeout_ms
    return {
        "ok": False,
        "engine": engine,
        "error_type": "timeout",
        "error": f"{phase} navigation timeout after {timeout_ms}ms",
    }


async def _humanize_nodriver_page(page) -> None:
    scrolls = random.randint(1, 3)
    for _ in range(scrolls):
        delta = random.randint(80, 260)
        await page.evaluate(f"window.scrollBy(0, {delta})")
        await asyncio.sleep(random.uniform(0.25, 0.9))


async def _humanize_camoufox_page(page) -> None:
    scrolls = random.randint(1, 3)
    for _ in range(scrolls):
        delta = random.randint(80, 260)
        await page.mouse.wheel(0, delta)
        await asyncio.sleep(random.uniform(0.25, 0.9))


async def _stop_browser_best_effort(browser) -> None:
    cleanup_timeout_seconds = min(_timeout_seconds(), 5.0)
    loop = asyncio.get_running_loop()
    _log_nodriver_browser_diagnostics("after uc.start", browser)
    owned_processes = _collect_owned_process_handles(browser)
    logger.debug("[browser_engine] nodriver owned-process handles found: %d", len(owned_processes))
    try:
        _log_nodriver_browser_diagnostics("before browser.stop", browser)
        stop_result = browser.stop()
        if inspect.isawaitable(stop_result):
            await asyncio.wait_for(stop_result, timeout=cleanup_timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning("[browser_engine] nodriver stop timed out after %.2fs", cleanup_timeout_seconds)
    except Exception as exc:
        logger.warning("[browser_engine] nodriver stop failed: %s", exc)
    finally:
        _log_nodriver_browser_diagnostics("after browser.stop", browser)
        await _cleanup_owned_process_handles(owned_processes, cleanup_timeout_seconds)
        owned_processes.clear()
        browser = None
        try:
            if not loop.is_closed():
                gc.collect()
        except Exception as exc:
            logger.debug("[browser_engine] nodriver gc.collect failed: %s", exc)
        # Give subprocess transports a chance to run close callbacks before loop shutdown.
        await asyncio.sleep(0)
        await asyncio.sleep(0.1)


def _collect_owned_process_handles(browser) -> list:
    owned = []
    candidate_names = {"process", "subprocess", "transport", "browser_process", "proc", "popen", "pid"}
    try:
        names = dir(browser)
    except Exception as exc:
        logger.debug("[browser_engine] nodriver diagnostics dir() failed: %s", exc)
        return owned
    for name in names:
        lowered = name.lower()
        if not any(token in lowered for token in candidate_names):
            continue
        try:
            value = getattr(browser, name)
        except Exception as exc:
            logger.debug("[browser_engine] nodriver diagnostics getattr(%s) failed: %s", name, exc)
            continue
        if isinstance(value, (asyncio.subprocess.Process, subprocess.Popen)):
            owned.append(value)
    return owned


def _log_nodriver_browser_diagnostics(stage: str, browser) -> None:
    safe = []
    try:
        names = dir(browser)
    except Exception as exc:
        logger.debug("[browser_engine] nodriver diagnostics (%s): dir() failed: %s", stage, exc)
        return
    for name in names:
        lowered = name.lower()
        if not any(token in lowered for token in ("process", "subprocess", "transport", "browser_process", "proc", "popen", "pid")):
            continue
        try:
            value = getattr(browser, name)
            safe.append(f"{name}={type(value).__name__}")
        except Exception as exc:
            safe.append(f"{name}=<getattr failed: {exc}>")
    logger.debug("[browser_engine] nodriver diagnostics (%s): %s", stage, ", ".join(safe) if safe else "no process-like attributes")


async def _cleanup_owned_process_handles(handles: list, cleanup_timeout_seconds: float) -> None:
    for handle in handles:
        try:
            if isinstance(handle, asyncio.subprocess.Process):
                await _cleanup_asyncio_process_handle(handle, cleanup_timeout_seconds)
            elif isinstance(handle, subprocess.Popen):
                _cleanup_popen_handle(handle, cleanup_timeout_seconds)
        except Exception as exc:
            logger.warning("[browser_engine] nodriver owned-process cleanup failed: %s", exc)


async def _cleanup_asyncio_process_handle(handle: asyncio.subprocess.Process, cleanup_timeout_seconds: float) -> None:
    if handle.returncode is not None:
        return
    try:
        handle.terminate()
    except Exception as exc:
        logger.warning("[browser_engine] nodriver asyncio-process terminate failed: %s", exc)
    try:
        await asyncio.wait_for(handle.wait(), timeout=cleanup_timeout_seconds)
        return
    except asyncio.TimeoutError:
        pass
    except Exception as exc:
        logger.warning("[browser_engine] nodriver asyncio-process wait failed: %s", exc)
    try:
        handle.kill()
    except Exception as exc:
        logger.warning("[browser_engine] nodriver asyncio-process kill failed: %s", exc)
    try:
        await asyncio.wait_for(handle.wait(), timeout=cleanup_timeout_seconds)
    except Exception as exc:
        logger.warning("[browser_engine] nodriver asyncio-process final wait failed: %s", exc)


def _cleanup_popen_handle(handle: subprocess.Popen, cleanup_timeout_seconds: float) -> None:
    if handle.poll() is not None:
        return
    try:
        handle.terminate()
    except Exception as exc:
        logger.warning("[browser_engine] nodriver popen terminate failed: %s", exc)
    try:
        handle.wait(timeout=cleanup_timeout_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    except Exception as exc:
        logger.warning("[browser_engine] nodriver popen wait failed: %s", exc)
    try:
        handle.kill()
    except Exception as exc:
        logger.warning("[browser_engine] nodriver popen kill failed: %s", exc)
    try:
        handle.wait(timeout=cleanup_timeout_seconds)
    except Exception as exc:
        logger.warning("[browser_engine] nodriver popen final wait failed: %s", exc)


class _NodriverSession:
    def __init__(self, uc_module, browser):
        self._uc = uc_module
        self._browser = browser
        self._warmed_up = False
        self._closed = False
        self._broken = False

    async def _ensure_warmup(self) -> dict | None:
        if self._closed:
            return {"ok": False, "engine": "nodriver", "error_type": "exception", "error": "nodriver session is closed"}
        if self._warmed_up:
            return None
        try:
            _ = await asyncio.wait_for(self._browser.get("https://www.avito.ru/"), timeout=_timeout_seconds())
            await asyncio.sleep(random.uniform(2.0, 4.0))
            self._warmed_up = True
            return None
        except asyncio.TimeoutError:
            self._broken = True
            await self.close()
            return _timeout_result("nodriver", "warmup")
        except Exception as exc:
            self._broken = True
            await self.close()
            return {"ok": False, "engine": "nodriver", "error_type": "exception", "error": str(exc)}

    @property
    def broken(self) -> bool:
        return self._broken

    async def fetch(self, url: str) -> dict:
        if self._closed:
            return {"ok": False, "engine": "nodriver", "error_type": "exception", "error": "nodriver session is closed"}
        try:
            warmup_result = await self._ensure_warmup()
            if warmup_result is not None:
                return warmup_result
            page = await asyncio.wait_for(self._browser.get(url), timeout=_timeout_seconds())
            if _is_humanize_enabled():
                try:
                    await _humanize_nodriver_page(page)
                except Exception as exc:
                    logger.warning("[browser_engine] nodriver humanize failed: %s", exc)
            await asyncio.sleep(random.uniform(3.0, 6.0))
            title: str = await page.evaluate("document.title") or ""
            body: str = await page.evaluate("document.body.innerText") or ""
            html: str = await page.get_content() or ""
            if _is_blocked(title, body):
                return {"ok": False, "engine": "nodriver", "error_type": "possible_captcha_or_block"}
            cards_count: int = await page.evaluate("document.querySelectorAll('[data-marker=\"item\"]').length")
            return {"ok": True, "engine": "nodriver", "html": html, "cards_count": cards_count}
        except asyncio.TimeoutError:
            self._broken = True
            await self.close()
            return _timeout_result("nodriver", "target")
        except Exception as exc:
            self._broken = True
            await self.close()
            return {"ok": False, "engine": "nodriver", "error_type": "exception", "error": str(exc)}

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        browser = self._browser
        await _stop_browser_best_effort(browser)
        self._browser = None
        self._uc = None


class _CamoufoxSession:
    def __init__(self, browser, page):
        self._browser = browser
        self._page = page
        self._warmed_up = False
        self._closed = False
        self._broken = False

    async def _ensure_warmup(self) -> dict | None:
        if self._closed:
            return {"ok": False, "engine": "camoufox", "error_type": "exception", "error": "camoufox session is closed"}
        if self._warmed_up:
            return None
        try:
            await self._page.goto(
                "https://www.avito.ru/",
                wait_until="domcontentloaded",
                timeout=settings.scrape_timeout_ms,
            )
            await asyncio.sleep(random.uniform(2.0, 4.0))
            self._warmed_up = True
            return None
        except asyncio.TimeoutError:
            return _timeout_result("camoufox", "warmup")
        except Exception as exc:
            if "Timeout" in str(exc):
                return _timeout_result("camoufox", "warmup")
            return {"ok": False, "engine": "camoufox", "error_type": "exception", "error": str(exc)}

    @property
    def broken(self) -> bool:
        return self._broken

    async def fetch(self, url: str) -> dict:
        if self._closed:
            return {"ok": False, "engine": "camoufox", "error_type": "exception", "error": "camoufox session is closed"}
        try:
            warmup_result = await self._ensure_warmup()
            if warmup_result is not None:
                return warmup_result
            await self._page.goto(url, wait_until="domcontentloaded", timeout=settings.scrape_timeout_ms)
            if _is_humanize_enabled():
                try:
                    await _humanize_camoufox_page(self._page)
                except Exception as exc:
                    logger.warning("[browser_engine] camoufox humanize failed: %s", exc)
            await asyncio.sleep(random.uniform(3.0, 6.0))
            title = await self._page.title() or ""
            body = (await self._page.locator("body").first.text_content()) or ""
            html = await self._page.content() or ""
            if _is_blocked(title, body):
                return {"ok": False, "engine": "camoufox", "error_type": "possible_captcha_or_block"}
            cards_count = await self._page.locator('[data-marker="item"]').count()
            return {"ok": True, "engine": "camoufox", "html": html, "cards_count": cards_count}
        except asyncio.TimeoutError:
            return _timeout_result("camoufox", "target")
        except Exception as exc:
            if "Timeout" in str(exc):
                return _timeout_result("camoufox", "target")
            return {"ok": False, "engine": "camoufox", "error_type": "exception", "error": str(exc)}

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._browser.__aexit__(None, None, None)


async def open_nodriver_session(proxy_url: Optional[str]):
    import nodriver as uc  # noqa: PLC0415
    args = ["--lang=ru-RU", "--window-size=1920,1080", "--disable-blink-features=AutomationControlled"]
    if proxy_url:
        args.extend(_nodriver_proxy_args(proxy_url))
    browser = await uc.start(headless=settings.scrape_headless, browser_args=args)

    try:
        tab = browser.main_tab
        if tab is None:
            _ = await asyncio.wait_for(browser.get("about:blank"), timeout=_timeout_seconds())
            tab = browser.main_tab

        if tab is not None:
            try:
                await tab.send(uc.cdp.page.add_script_to_evaluate_on_new_document(source=_STEALTH_INIT_SCRIPT))
            except Exception as _patch_exc:
                logger.debug("[browser_engine] nodriver: stealth init-script skipped: %s", _patch_exc)
        else:
            logger.warning("[browser_engine] nodriver: main_tab is None before warmup, stealth init-script was not injected")

        parsed_proxy = parse_proxy_url(proxy_url) if proxy_url else None
        if parsed_proxy and parsed_proxy.username is not None and parsed_proxy.password is not None:
            user = parsed_proxy.username
            password = parsed_proxy.password
            if tab is not None:
                try:
                    await tab.send(uc.cdp.fetch.enable(handle_auth_requests=True))

                    async def _auth_handler(event: uc.cdp.fetch.AuthRequired) -> None:  # type: ignore[name-defined]
                        await tab.send(
                            uc.cdp.fetch.continue_with_auth(
                                request_id=event.request_id,
                                auth_challenge_response=uc.cdp.fetch.AuthChallengeResponse(
                                    response="ProvideCredentials",
                                    username=user,
                                    password=password,
                                ),
                            )
                        )

                    tab.add_handler(uc.cdp.fetch.AuthRequired, _auth_handler)
                except Exception as _auth_exc:
                    logger.warning("[browser_engine] nodriver: proxy auth handler setup failed: %s", _auth_exc)
            else:
                logger.warning("[browser_engine] nodriver: main_tab is None before warmup, proxy auth credentials will not be injected")
        return _NodriverSession(uc, browser)
    except Exception:
        await _stop_browser_best_effort(browser)
        raise


async def open_camoufox_session(proxy_url: Optional[str]):
    from camoufox.async_api import AsyncCamoufox  # noqa: PLC0415

    proxy_cfg = _parse_proxy_url(proxy_url) if proxy_url else None
    system_name = platform.system()
    if settings.scrape_headless:
        # Camoufox virtual display is Linux-only; use native headless on macOS.
        _cf_headless = "virtual" if system_name == "Linux" else True
    else:
        _cf_headless = False
    camoufox_kwargs = {"headless": _cf_headless, "proxy": proxy_cfg}
    if proxy_cfg:
        camoufox_kwargs["geoip"] = True
    browser_cm = AsyncCamoufox(**camoufox_kwargs)
    browser = await browser_cm.__aenter__()
    try:
        page = await browser.new_page()
        await page.add_init_script(_STEALTH_INIT_SCRIPT)
        try:
            await page.context.grant_permissions(["geolocation"])
            await page.context.set_geolocation({"latitude": 59.9386, "longitude": 30.3141})
        except Exception as _geo_exc:
            logger.debug("[browser_engine] camoufox: geolocation setup skipped: %s", _geo_exc)
        return _CamoufoxSession(browser_cm, page)
    except Exception:
        import sys as _sys

        await browser_cm.__aexit__(*_sys.exc_info())
        raise

# ---------------------------------------------------------------------------
# nodriver backend
# ---------------------------------------------------------------------------

async def fetch_with_nodriver(url: str, proxy_url: Optional[str]) -> dict:
    """Fetch url with nodriver (Chrome/CDP, no WebDriver flag)."""
    try:
        import nodriver as uc  # noqa: PLC0415,F401
    except ImportError:
        return {"ok": False, "engine": "nodriver", "error_type": "import_error", "error": "nodriver not installed. Run: pip install nodriver"}

    session = None
    try:
        session = await open_nodriver_session(proxy_url)
        result = await session.fetch(url)
        if result.get("ok"):
            logger.info("[browser_engine] nodriver: ok, cards=%d", result.get("cards_count", 0))
        return result
    except Exception as exc:
        logger.warning("[browser_engine] nodriver exception: %s", exc)
        if isinstance(exc, asyncio.TimeoutError):
            return _timeout_result("nodriver", "setup")
        return {"ok": False, "engine": "nodriver", "error_type": "exception", "error": str(exc)}
    finally:
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass
            session = None
            gc.collect()
            await asyncio.sleep(0)
            await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# camoufox backend
# ---------------------------------------------------------------------------

async def fetch_with_camoufox(url: str, proxy_url: Optional[str]) -> dict:
    """Fetch url with camoufox (Firefox, engine-level stealth).

    Returns same schema as fetch_with_nodriver.
    """
    try:
        from camoufox.async_api import AsyncCamoufox  # noqa: PLC0415,F401
    except ImportError:
        return {"ok": False, "engine": "camoufox",
                "error_type": "import_error",
                "error": "camoufox not installed. Run: pip install 'camoufox[geoip]' && python -m camoufox fetch"}

    session = None
    try:
        session = await open_camoufox_session(proxy_url)

        result = await session.fetch(url)
        if result.get("ok"):
            logger.info("[browser_engine] camoufox: ok, cards=%d", result.get("cards_count", 0))
        return result

    except Exception as exc:
        logger.warning("[browser_engine] camoufox exception: %s", exc)
        return {"ok": False, "engine": "camoufox", "error_type": "exception", "error": str(exc)}
    finally:
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass
            session = None
            gc.collect()
            await asyncio.sleep(0)
            await asyncio.sleep(0.1)
