# Proxy smoke report template

Date (UTC): YYYY-MM-DD

## Environment
- PROXY_URLS: `http://user:pass@host:port`
- SCRAPE_HEADLESS: `true`
- ALERT_CHANNELS: `jsonl`

## Command
`PROXY_URLS=http://user:pass@host:port SCRAPE_HEADLESS=true ALERT_CHANNELS=jsonl python3 -m app.cli run-once`

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
