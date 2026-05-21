# Proxy smoke report

Date (UTC): 2026-05-21

## Environment

- PROXY_URLS: `http://***:***@mproxy.site:11102`
- SCRAPE_HEADLESS: `true`
- SCRAPE_HUMANIZE: `false`
- ALERT_CHANNELS: `jsonl`
- DATABASE_URL: `sqlite:///tmp.db`
- Python: `python3`

## Proxy connectivity

### curl ipify

Result: PASS

Observed:
- Proxy CONNECT tunnel established.
- Proxy auth accepted.
- Public IP returned: `94.25.229.246`.
- No `407 Proxy Authentication Required`.

### curl Avito

Result: EXPECTED_CURL_BLOCK

Observed:
- Proxy CONNECT tunnel established.
- Avito/QRATOR returned `HTTP/2 403`.

Interpretation:
- This is acceptable for raw curl because Avito/QRATOR may block non-browser clients.
- Browser-based smoke is required for final validation.

## Browser/parser smoke

Command type:
- Direct `AvitoParser.fetch_search_cards(...)` smoke with `asyncio.wait_for(timeout=90)`.

Result: PASS

Observed:
- `ok: true`
- `total_cards: 30`
- First parsed cards included valid `external_id`, `title`, `price`, `url`.

## Conclusion

Authenticated proxy works with the current browser/parser stack.
Core parser path successfully loads and parses Avito search results through the proxy.

## Remaining notes

- Previous plain CLI dry-run was interrupted while nodriver was awaiting Avito warmup navigation.
- Recommended follow-up: add internal navigation timeout around browser warmup/target navigation so worker cannot hang indefinitely on slow browser navigation.
