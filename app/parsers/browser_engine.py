"""Stealth browser fetch backends: nodriver (fast-path) and camoufox (fallback)."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

logger = logging.getLogger(__name__)

# Block-detection signals (Russian + English Avito error pages)
BLOCK_SIGNALS = (
    "проблема с ip",
    "доступ ограничен",
    "captcha",
    "robot check",
    "access denied",
    "blocked",
    "временно недоступен",
)

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
  // Patches both WebGLRenderingContext (WebGL1) and WebGL2RenderingContext
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
    text = (title + " " + body[:3000]).lower()
    return any(sig in text for sig in BLOCK_SIGNALS)


def _parse_proxy_url(proxy_url: str) -> dict:
    """Parse http://user:pass@host:port into camoufox proxy dict."""
    proto, rest = proxy_url.split("://", 1)
    if "@" in rest:
        creds, hostport = rest.rsplit("@", 1)
        user, password = creds.split(":", 1)
        return {"server": f"{proto}://{hostport}", "username": user, "password": password}
    return {"server": proxy_url}


def _nodriver_proxy_args(proxy_url: str | None) -> list[str]:
    """Return --proxy-server arg list for nodriver launch."""
    if proxy_url is None:
        return []
    if "@" in proxy_url:
        # strip auth from URL for the flag; auth handled by CDP handler
        proto, rest = proxy_url.split("://", 1)
        hostport = rest.rsplit("@", 1)[1]
        return [f"--proxy-server=http://{hostport}"]
    else:
        proto, hostport = proxy_url.split("://", 1)
        return [f"--proxy-server=http://{hostport}"]


# ---------------------------------------------------------------------------
# nodriver backend
# ---------------------------------------------------------------------------

async def fetch_with_nodriver(url: str, proxy_url: Optional[str]) -> dict:
    """Fetch url with nodriver (Chrome/CDP, no WebDriver flag).

    Returns dict with keys: ok, engine, html (on success) or error_type, error (on failure).
    """
    try:
        import nodriver as uc  # noqa: PLC0415
    except ImportError:
        return {"ok": False, "engine": "nodriver",
                "error_type": "import_error",
                "error": "nodriver not installed. Run: pip install nodriver"}

    args = [
        "--lang=ru-RU",
        "--window-size=1920,1080",
        "--disable-blink-features=AutomationControlled",
    ]
    if proxy_url:
        args.extend(_nodriver_proxy_args(proxy_url))

    browser = None
    try:
        import os as _os
        _headless = _os.getenv("SCRAPE_HEADLESS", "false").lower() in ("true", "1")
        browser = await uc.start(headless=_headless, browser_args=args)

        # Inject stealth script so it runs on every new page before any navigation
        try:
            await browser.main_tab.send(
                uc.cdp.page.add_script_to_evaluate_on_new_document(source=_STEALTH_INIT_SCRIPT)
            )
        except Exception as _patch_exc:
            logger.debug("[browser_engine] nodriver: stealth init-script skipped: %s", _patch_exc)

        # Warmup: land on homepage first to build cookies.
        # NOTE: proxy auth handler must be installed AFTER the first navigation
        # because nodriver creates main_tab lazily on the first browser.get() call.
        # Installing it before that returns None and raises AttributeError.
        _ = await browser.get("https://www.avito.ru/")

        # Set up proxy auth handler if credentials present.
        # Tab is now guaranteed to exist after the get() call above.
        if proxy_url and "@" in proxy_url:
            creds_part = proxy_url.split("://", 1)[1].rsplit("@", 1)[0]
            user, password = creds_part.split(":", 1)
            tab = browser.main_tab
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
                    logger.warning(
                        "[browser_engine] nodriver: proxy auth handler setup failed: %s",
                        _auth_exc,
                    )
            else:
                logger.warning(
                    "[browser_engine] nodriver: main_tab is None after warmup, "
                    "proxy auth credentials will not be injected"
                )
        await asyncio.sleep(random.uniform(2.0, 4.0))

        # Target page
        page = await browser.get(url)
        await asyncio.sleep(random.uniform(3.0, 6.0))

        title: str = await page.evaluate("document.title") or ""
        body: str = await page.evaluate("document.body.innerText") or ""
        html: str = await page.get_content() or ""

        if _is_blocked(title, body):
            logger.warning("[browser_engine] nodriver: block detected title=%r", title[:80])
            return {"ok": False, "engine": "nodriver", "error_type": "possible_captcha_or_block"}

        cards_count: int = await page.evaluate(
            "document.querySelectorAll('[data-marker=\"item\"]').length"
        )
        logger.info("[browser_engine] nodriver: ok, cards=%d", cards_count)
        return {"ok": True, "engine": "nodriver", "html": html, "cards_count": cards_count}

    except Exception as exc:
        logger.warning("[browser_engine] nodriver exception: %s", exc)
        return {"ok": False, "engine": "nodriver", "error_type": "exception", "error": str(exc)}
    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# camoufox backend
# ---------------------------------------------------------------------------

async def fetch_with_camoufox(url: str, proxy_url: Optional[str]) -> dict:
    """Fetch url with camoufox (Firefox, engine-level stealth).

    Returns same schema as fetch_with_nodriver.
    """
    try:
        from camoufox.async_api import AsyncCamoufox  # noqa: PLC0415
    except ImportError:
        return {"ok": False, "engine": "camoufox",
                "error_type": "import_error",
                "error": "camoufox not installed. Run: pip install 'camoufox[geoip]' && python -m camoufox fetch"}

    proxy_cfg = _parse_proxy_url(proxy_url) if proxy_url else None

    try:
        async with AsyncCamoufox(headless="virtual", proxy=proxy_cfg) as browser:
            page = await browser.new_page()

            # Inject stealth patches before any navigation
            await page.add_init_script(_STEALTH_INIT_SCRIPT)

            # Set geolocation to Saint Petersburg — must match proxy IP region.
            # Uses standard Playwright BrowserContext API available in camoufox.
            try:
                await page.context.grant_permissions(["geolocation"])
                await page.context.set_geolocation({"latitude": 59.9386, "longitude": 30.3141})
            except Exception as _geo_exc:
                logger.debug("[browser_engine] camoufox: geolocation setup skipped: %s", _geo_exc)

            # Warmup
            await page.goto("https://www.avito.ru/", wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2.0, 4.0))

            # Target page
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(3.0, 6.0))

            title: str = await page.title() or ""
            body: str = (await page.locator("body").first.text_content()) or ""
            html: str = await page.content() or ""

            if _is_blocked(title, body):
                logger.warning("[browser_engine] camoufox: block detected title=%r", title[:80])
                return {"ok": False, "engine": "camoufox", "error_type": "possible_captcha_or_block"}

            cards_count: int = await page.locator('[data-marker="item"]').count()
            logger.info("[browser_engine] camoufox: ok, cards=%d", cards_count)
            return {"ok": True, "engine": "camoufox", "html": html, "cards_count": cards_count}

    except Exception as exc:
        logger.warning("[browser_engine] camoufox exception: %s", exc)
        return {"ok": False, "engine": "camoufox", "error_type": "exception", "error": str(exc)}
