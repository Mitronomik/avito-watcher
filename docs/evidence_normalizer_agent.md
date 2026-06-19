# EvidenceNormalizerAgent v0

## Purpose

`EvidenceNormalizerAgent v0` implements `evidence_normalizer_future` for the controlled `listing_evidence_pipeline`. It reads PR41 `evidence_candidates` artifacts and writes deterministic internal `normalized_evidence` artifacts. It is an internal artifact step only, not a market evidence write model.

## Input artifact contract

The only accepted source artifact is:

- `artifact_type = evidence_candidates`
- `schema_version = evidence-candidates-v0`
- `payload_json.schema_version = evidence-candidates-v0`
- `payload_json.artifact_type = evidence_candidates`
- `payload_json.result_kind = evidence_candidates`
- `payload_json.items` is a list

The payload must use only PR39-safe envelope keys: `schema_version`, `artifact_type`, `result_kind`, `summary`, `items`, `limitations`, `confidence`, `notes`, and `metadata`.

## Output artifact contract

The normalizer writes through the PR39 artifact service helpers with:

- `artifact_type = normalized_evidence`
- `schema_version = normalized-evidence-candidates-v0`
- `result_kind = normalized_evidence`
- `metadata.normalization_kind = normalized_evidence_candidates`

`normalized_evidence_candidates` is not introduced as a new artifact type because PR39 already protects `agent_artifacts.artifact_type` with the existing `normalized_evidence` value. PR42 intentionally adds no migration.

## Normalization rules v0

The normalizer only copies or computes numeric values already present in source `observed_value`:

- `price` -> `price_rub`
- `area_m2` -> `area_m2`
- `price_per_m2` -> `price_per_m2_rub`
- if `price_per_m2` is missing and positive numeric `price` and `area_m2` are present, compute `round(price / area_m2, 2)`

It does not infer rents, sale prices, cap rates, yields, comparable values, expected profit, probabilities, verdicts, or risk scores.

## Source artifact lookup order

1. `task.payload_json["source_artifact_id"]`
2. `evidence_candidates` where `source_task_id == task.depends_on_task_id`
3. `evidence_candidates` where `source_task_id == task.parent_task_id`
4. same `listing_external_id`, `context_key`, and `orchestration_run_id`, only when unambiguous
5. same `listing_external_id` and `context_key`, only when unambiguous

The normalizer does not use `workflow_id` for artifact lookup because `agent_artifacts` has no `workflow_id` column.

## Source refs policy

Source refs are PR39-compatible structured objects. They use `source_kind`, `source_ref_id`, `note`, `agent_task_id`, `listing_external_id`, `listing_analysis_id`, and `search_job_id` as applicable. They do not use unsupported source-ref keys such as `artifact_id`, `source_artifact_id`, or `source_task_id`. The raw source artifact id is recorded in artifact payload metadata and task `result_json` instead.

## Skip policy

The task is skipped with no artifact when listing context is missing, the listing row is missing, source artifact lookup is missing or ambiguous, or source artifact validation fails. Valid empty source candidates produce a successful empty `normalized_evidence` artifact with `items = []`, zero counts, and `no_source_candidates` in limitations.

## Idempotency

Reruns first reuse an existing `task.result_json.artifact_id` when it points to a `normalized_evidence` artifact. Otherwise the handler computes PR39 input/content hashes, checks `find_duplicate_agent_artifact`, and creates at most one new append-only artifact.

## Side-effect boundaries and non-goals

PR42 does not write `market_evidence_items`, mutate listing analysis scores or verdicts, mutate workflow state or allowed actions, trigger alerts, call external HTTP, call LLMs, read or write RAG, trigger monitors, auto-advance workflows, or auto-enqueue the normalizer after the collector.

## Production smoke commands

Do not run these during PR implementation. In production smoke, use a rollback transaction or explicitly clean all synthetic rows.

1. Deploy/rebuild app and worker.
2. Verify Alembic head is unchanged with `alembic heads`.
3. Verify health endpoint.
4. Verify registry/meta shows collector and normalizer implemented and no external HTTP/LLM/RAG side effects.
5. In rollback/cleanup scope, create a collector source artifact and a normalizer task with `source_artifact_id` or dependency metadata.
6. Run the normalizer.
7. Assert output artifact has `artifact_type = normalized_evidence`, `schema_version = normalized-evidence-candidates-v0`, `result_kind = normalized_evidence`, `metadata.normalization_kind = normalized_evidence_candidates`, source artifact id in metadata/result_json, PR39-compatible source refs, and no forbidden sensitive markers.
8. Assert idempotency by rerunning the same task.
9. Roll back or clean up.
10. Assert persistent counts are unchanged for `agent_tasks`, `agent_artifacts`, `alerts_sent`, `listing_analyses`, `human_reviews`, and `market_evidence_items` after cleanup.
