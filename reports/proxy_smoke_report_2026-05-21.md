# Proxy/browser/Ollama smoke report

Date: 2026-05-21

## Result

PASS_WITH_NOTES

## Environment

- Proxy: authenticated HTTP proxy, redacted
- SCRAPE_HEADLESS=false
- SCRAPE_HUMANIZE=false
- SCRAPE_TIMEOUT_MS=30000
- ALERT_CHANNELS=jsonl
- OLLAMA_MODEL=qwen2.5:7b-instruct
- Database: PostgreSQL docker compose
- Search job: spb_proxy_smoke_2026_05_21

## Findings

### Browser/proxy

- curl through proxy to ipify: PASS
- curl through proxy to Avito: QRATOR 403, acceptable for raw curl
- nodriver through proxy: controlled timeout on warmup, no infinite hang
- parser fallback after nodriver timeout: PASS
- monitor run completed: PASS

### Monitor result

Observed result:

- created: 8
- alerted: 8
- scored: 8
- total_seen: 30
- baseline_initialized: true
- baseline_run: false
- fail_count: 0
- last_error: empty

### Ollama/scoring

- Ollama version: 0.24.0
- Installed model: qwen2.5:7b-instruct
- /api/chat: PASS
- ListingScorer direct test: PASS
- Monitor scoring: PASS

### JSONL

- JSONL alerts created.
- search_name is present.
- message includes LLM summary.
- Structured field llm_summary is null due to payload key mismatch.
- Follow-up PR needed: map payload["summary"] to JSONL llm_summary.

## Conclusion

The proxy/browser/fallback/monitor/scoring pipeline is operational.
Remaining issue is JSONL summary field mapping and optional scoring disable flag for cleaner smoke testing.
