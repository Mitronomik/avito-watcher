# Proxy smoke report template

Date (UTC): YYYY-MM-DD

## Environment
- PROXY_URLS: `http://user:pass@host:port`
- SCRAPE_HEADLESS: `true`
- ALERT_CHANNELS: `jsonl`
- SCORING_ENABLED: `false` (optional, to isolate browser/proxy from Ollama/model)

## Command
`PROXY_URLS=http://user:pass@host:port SCRAPE_HEADLESS=true ALERT_CHANNELS=jsonl SCORING_ENABLED=false python3 -m app.cli run-once`

## Expected behavior
- No `407 Proxy Authentication Required` in logs.
- No proxy-provider landing page/html instead of Avito content.
- Avito warmup to `https://www.avito.ru/` succeeds,
  or a controlled fallback occurs (`possible_captcha_or_block` / handled exception path without crashes).

## Result
- Status: PASS | FAIL | BLOCKED
- Notes:
  - warmup:
  - listing fetch:
  - fallback (if any):

## Known failure modes to capture in notes
- Nodriver proxy navigation timeout (warmup or target) reported as `error_type=timeout`.
- Known non-blocking limitation (macOS + Python 3.12): isolated `fetch_with_nodriver` smoke can print finalizer warnings after a controlled timeout, e.g. `Exception ignored in: <function BaseSubprocessTransport.__del__ ...>` and `RuntimeError: Event loop is closed`.
- Reproduce isolated warning with a minimal timeout smoke command, for example:
  `SCRAPE_TIMEOUT_MS=30000 python3 - <<'PY'\nimport asyncio\nimport os\nfrom app.parsers.browser_engine import fetch_with_nodriver\n\nasync def main():\n    proxy_url = os.environ.get('PROXY_URLS') or None\n    result = await fetch_with_nodriver('https://www.avito.ru/', proxy_url)\n    print(result)\n\nasyncio.run(main())\nPY`
- Production acceptance criterion is **MonitorService run-once completion** (successful one-pass monitoring with parser fallback/alerts/DB flow), not absence of the isolated nodriver finalizer warning.
- Camoufox `Page.goto` timeout on Avito warmup/target reported as `error_type=timeout`.
- macOS limitation: Camoufox virtual display is Linux-only.
  Local smoke on macOS may require `SCRAPE_HEADLESS=false` unless platform-aware headless handling is available.
