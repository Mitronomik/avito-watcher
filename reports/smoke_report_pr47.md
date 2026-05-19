# Smoke report for PR #47 post-merge verification

Date (UTC): 2026-05-19

## Environment
- Repository: `/workspace/avito-watcher`
- Python: `3.12.13`
- Docker: not available in runtime (`docker: command not found`)
- `PROXY_URLS`: not set in environment

## Commands and results

1. `git pull --rebase origin main`
- Result: **failed**
- Error: `fatal: 'origin' does not appear to be a git repository`
- Warning: remote `origin` is not configured/accessible in current environment.

2. `python3 -m compileall app`
- Result: **passed**
- Notes: `app` package compiled successfully.

3. `ruff check app tests`
- Result: **passed**
- Notes: `All checks passed!`.

4. `pytest -q`
- Result: **passed**
- Notes: `131 passed in 2.44s`.

5. Smoke (no proxy): `ALERT_CHANNELS=jsonl python3 -m app.cli run-once`
- Result: **failed due to environment dependency**
- Error: PostgreSQL connection refused at `127.0.0.1:5432` (`psycopg.OperationalError`).
- Browser/parser runtime for one active SearchJob could not be validated because DB is unavailable.

6. Smoke (authenticated proxy): not executed
- Reason: `PROXY_URLS` is not available in environment; additionally no local DB runtime to execute `run-once`.

## Browser session reuse verification status
- Merge/runtime checks (compile, lint, tests): **green**.
- Local smoke for real runtime path (worker/CLI with one active search): **blocked by missing runtime dependencies**.
- No direct evidence of browser lifecycle errors was observed, but end-to-end runtime was not reached.

## Errors/warnings
- Missing git remote access for pull/rebase.
- Missing Docker binary in this environment.
- Missing running PostgreSQL on `127.0.0.1:5432`.
- `PROXY_URLS` not set.

## Recommended next fix
1. Configure/restore `origin` remote and rerun `git pull --rebase origin main`.
2. Start local runtime dependencies (at minimum PostgreSQL; preferably full compose stack).
3. Seed one active SearchJob and rerun:
   - `ALERT_CHANNELS=jsonl python3 -m app.cli run-once`
4. In an environment with proxy secrets, rerun with:
   - `PROXY_URLS=http://user:pass@host:port SCRAPE_HEADLESS=true ALERT_CHANNELS=jsonl python3 -m app.cli run-once`
5. Confirm logs include successful warmup to `avito.ru` and absence of `407 Proxy Authentication Required`.
