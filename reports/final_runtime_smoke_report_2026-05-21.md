# Final runtime smoke report

Date: 2026-05-21

## Result

PASS

## Verified layers

- Authenticated HTTP proxy: PASS
- Avito browser fetch through proxy: PASS_WITH_FALLBACK
- Nodriver timeout handling: PASS
- Camoufox fallback: PASS
- PostgreSQL monitor pipeline: PASS
- Baseline/update flow: PASS
- JSONL alert delivery: PASS
- Ollama API: PASS
- Ollama model qwen2.5:7b-instruct: PASS
- ListingScorer direct test: PASS
- SCORING_ENABLED=false smoke mode: PASS
- JSONL llm_summary mapping: PASS

## Notes

- Nodriver still times out on Avito warmup through the tested proxy, but now returns a controlled timeout instead of hanging.
- Parser falls back after nodriver timeout.
- MonitorService completes successfully.
- JSONL `llm_summary` is now correctly populated from payload `summary`.
- For proxy/browser-only smoke, use `SCORING_ENABLED=false`.
- For full scoring smoke, ensure Ollama has `qwen2.5:7b-instruct` installed.

## Conclusion

The runtime pipeline is operational and safe enough for the next staged run.
