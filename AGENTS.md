# Avito Watcher - agent instructions

## Product goal

Build a personal Avito watcher for newly published real estate and commercial real estate listings.

The service must:
- monitor user-defined Avito search URLs;
- detect only new listings after baseline initialization;
- store listing history and price changes;
- evaluate listings with rule-based filters and optional LLM scoring;
- send relevant alerts to Telegram;
- avoid aggressive scraping, captcha bypassing, personal data harvesting, or hidden-data collection.
- use proxy rotation and stealth browser only to avoid IP-level blocks,
  not to bypass content access controls or rate-limit restrictions.

## Architecture rules

Do not make the LLM the core control loop.

Correct flow:
SearchJob -> Worker -> AvitoParser -> Deduplication -> Rule filters -> Optional LLM scoring -> Telegram alert -> Database history.

FastAPI must be API/admin only.
Worker must run monitoring.
Do not run monitoring twice from both API and worker.

## Safety and anti-spam rules

- Never send alerts during first baseline initialization.
- Do not scrape too frequently.
- Do not harvest seller phone numbers or private personal data.
- Do not open every listing page — only the search/listing-feed URLs.
- Proxy rotation and stealth browser configuration are ALLOWED and REQUIRED
  because Avito blocks datacenter IPs at the network level; without mobile Russian
  proxies the service cannot function. The feature must be opt-in via the PROXY_URLS
  environment variable and must default to no-proxy mode when the variable is unset.
- Fingerprint-neutral browser launch (nodriver / camoufox) is ALLOWED as a
  replacement for the current plain Playwright launch. It does not bypass any
  specific CAPTCHA challenge — it only makes the browser appear as a normal user.
- Explicit CAPTCHA-solving services (2captcha, CapSolver, etc.) remain PROHIBITED.

## Coding rules

- Make small focused changes.
- Prefer migrations over direct schema assumptions.
- Keep Docker Compose working.
- Use PYTHONPATH=/app for Docker commands when needed.
- Do not commit .env, cookies, sessions, storage_state, tokens, logs, or local databases.
- Add or update tests for parser, baseline, deduplication, and filtering logic.

## Required local checks

Before completing a task, run when possible:
python3 -m compileall app

For Docker-related changes:
docker compose -f deploy/docker-compose.yml config
