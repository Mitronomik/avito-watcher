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
- Do not add captcha bypass, proxy rotation, fingerprint spoofing, or phone scraping.
- Do not collect seller phone numbers or private personal data.
- Do not open every listing page unless explicitly required by a task.

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
