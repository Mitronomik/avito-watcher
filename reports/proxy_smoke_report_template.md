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
- Isolated nodriver timeout cleanup should not print `Event loop is closed` / `BaseSubprocessTransport.__del__` warnings after `fetch_with_nodriver` returns a controlled timeout.
- If such warnings still appear due to nodriver/Python subprocess internals, record as known non-blocking limitation and include reproduction command, e.g. `python3 - <<'PY' ... asyncio.run(fetch_with_nodriver(...)) ... PY`.
- Camoufox `Page.goto` timeout on Avito warmup/target reported as `error_type=timeout`.
- macOS limitation: Camoufox virtual display is Linux-only.
  Local smoke on macOS may require `SCRAPE_HEADLESS=false` unless platform-aware headless handling is available.
