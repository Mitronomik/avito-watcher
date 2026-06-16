# Deterministic investment profiles v0

PR13 adds deterministic investment analysis profiles only:

- `commercial_sale_investment`
- `flat_sale_investment`

The profiles use parsed listing data plus explicit manual assumptions from `search_jobs.filters_json`. They do not use comps, market research, LLMs, RAG, AgentTasks, live detail fetches, listing enrichments, or automatic market assumptions. The output is not an appraisal, not a market valuation, and not a buy/sell recommendation. Human approval is required.

## Purchase price safety

`investment_purchase_price` is the safe purchase price input. `listing.price` is not used as purchase price by default because it may represent rent in rental searches. `listing.price` can be used only when both flags are explicit:

```json
{
  "investment_allow_listing_price_as_purchase_price": true,
  "investment_price_basis": "listing_price_as_purchase_price"
}
```

When this fallback is used, facts mark `purchase_price_source` as `listing.price`, set `purchase_price_requires_human_confirmation`, and risks include `purchase_price_source_requires_human_confirmation`.

## Manual assumptions

Supported assumptions include `investment_purchase_price`, `estimated_monthly_rent`, `opex_ratio`, `opex_monthly`, `vacancy_rate`, `capex_initial`, `min_gross_yield`, `min_noi_yield`, `max_payback_years`, `asset_type`, and `deal_type`. Missing assumptions add conservative flags and cap verdicts. Missing vacancy and CAPEX use an explicit `0.0` calculation fallback with warning flags. Missing opex allows gross metrics but prevents NOI and payback.

## Formulas

- `annual_gross_income = estimated_monthly_rent * 12`
- `vacancy_loss_annual = annual_gross_income * vacancy_rate_used`
- `effective_gross_income = annual_gross_income - vacancy_loss_annual`
- `opex_annual = opex_monthly * 12` when `opex_monthly` is supplied
- otherwise `opex_annual = effective_gross_income * opex_ratio` when `opex_ratio` is supplied
- `noi_annual = effective_gross_income - opex_annual`
- `total_initial_outlay = purchase_price + capex_initial_used`
- yields are stored both on purchase price and total initial outlay
- `payback_years = total_initial_outlay / noi_annual`

Thresholds `min_gross_yield` and `min_noi_yield` compare against total initial outlay by default. Facts also store price-based yield metrics.

## Output

Metrics are stored in `facts_json["investment_metrics"]`, including purchase price source, annual gross income, vacancy loss, effective gross income, opex, NOI, total outlay, gross yield, NOI yield, payback, assumptions, missing assumptions, and flags.

## Example commercial filters

```json
{
  "analysis_profile": "commercial_sale_investment",
  "asset_type": "commercial",
  "deal_type": "sale",
  "investment_purchase_price": 9500000,
  "estimated_monthly_rent": 120000,
  "opex_ratio": 0.25,
  "vacancy_rate": 0.08,
  "capex_initial": 500000,
  "min_gross_yield": 0.12,
  "min_noi_yield": 0.08,
  "max_payback_years": 12
}
```

## Example flat filters

```json
{
  "analysis_profile": "flat_sale_investment",
  "asset_type": "flat",
  "deal_type": "sale",
  "investment_purchase_price": 10500000,
  "estimated_monthly_rent": 55000,
  "opex_monthly": 5000,
  "vacancy_rate": 0.05,
  "capex_initial": 500000,
  "min_gross_yield": 0.055,
  "min_noi_yield": 0.045,
  "max_payback_years": 18
}
```

## Smoke commands

```bash
python3 -m compileall app
ruff check app tests
pytest -q tests/test_investment_analysis.py
```

## PR16: opt-in stored market comps for investment profiles

`commercial_sale_investment` and `flat_sale_investment` can optionally use stored SQL-backed market evidence as rent comps when `use_market_evidence=true` is set in `AnalysisConfig` / search `filters_json`. The feature is deterministic: scoring reads already-stored `market_evidence_items` only and does not call an LLM, `ResearchAgent`, embeddings/vector search, or the network during scoring.

Market evidence can estimate rent only. It cannot replace `investment_purchase_price`, does not add any purchase-price fallback flags, and does not silently use `listing.price` unless the pre-existing explicit PR13 listing-price fallback is configured. Manual `estimated_monthly_rent` remains primary; stored comps are used only for comparison, and weak or missing comps do not degrade manual-primary calculations. If manual rent is missing, enough reusable rent comps can fill the rent estimate; weak or insufficient evidence caps verdict only when market evidence is the rent source. A single comp cannot produce a strong result.

Selected evidence is resolved before `input_hash`, and the selected evidence fingerprint is included in `input_hash`. One explicit timezone-aware `as_of_datetime` is used for selection, max-age filtering, expiration filtering, fingerprinting, facts, and report content. PR16 reuses the existing `investment_metrics` schema and adds market-evidence details under it. Low/base/high scenarios remain future scope.

## PR16b: deterministic same-location-key bridge

PR16b keeps PR16 same-listing behavior as the effective default. `market_evidence_matching_policy` defaults to `None` in `AnalysisConfig`; when `use_market_evidence=true`, `None` resolves to `same_listing` so old configs avoid hash churn. If `use_market_evidence` is false or unset, no market-evidence policy or selected-evidence fingerprint is added to the hash.

The only opt-in cross-listing policy is `same_location_key`. It requires an explicit `market_evidence_location_key`; missing keys do not fall back to city-wide, inferred address, fuzzy, semantic, GIS/geocoding, radius, or vector matching. Selection remains deterministic over stored SQL evidence only and keeps strict filters for reusable rent `comparable_candidate` items, expected asset type, `deal_type=rent`, freshness, confidence, source URL, content hash, and rent metric.

The matching policy, configured location key, retrieval UTC date bucket, effective market-evidence config, and selected evidence fingerprint are included in `input_hash`. Cross-listing same-location-key evidence cannot produce a strong verdict in PR16b: when it is used as the rent source, conservative facts, risk flags, and human-review questions are added because PR24 comparable quality scoring and PR25 comparable selection policy v2 are still future work. Manual rent remains primary, and weak or missing cross-listing evidence does not degrade manual-primary calculations.

PR16b changes only deterministic selection policy. It does not change evidence storage, scoring formulas, alerts, Google Sheets schema, LLM behavior, `ResearchAgent`, live external research, automatic research ingestion, GIS/geocoding, fuzzy/semantic/radius matching, or market evidence mutation.

## PR24: deterministic comparable quality scoring

PR24 adds comparable quality scoring for already selected stored market evidence candidates. It is not PR25 selection policy v2 and does not add controlled reuse, broader matching, city-wide medians, semantic/fuzzy matching, or provider-side retrieval. It is not PR26 adjusted comps and does not calculate adjusted rents/prices, adjusted medians, or `comp_adjustment_flags`.

The deterministic model is `comparable_quality_model_version = "v0"`. The helper accepts an explicit timezone-aware `as_of` datetime from the analysis context; it does not call current-time functions, LLMs, agents, RAG, external APIs, or the network. Manual `estimated_monthly_rent` remains primary when provided; weak comps can add review/cap facts but do not overwrite manual assumptions or broadly cap the investment score/verdict.

V0 starts each comparable at 100 points on a 0..100 quality/similarity scale. Buckets are: `high >= 80`, `medium >= 60`, `low >= 35`, and `rejected` for hard rejects or scores below 35. Explicit soft penalties are: missing source URL `-30`, stale evidence older than 30 days `-25`, unknown area `-5`, area-band mismatch over 25% `-20`, unknown location `-5`, and location mismatch `-20`. Evidence older than 90 days is rejected as `stale_evidence`; known area mismatch over 50% is rejected as `area_band_mismatch`.

Stable rejection reasons include `asset_type_mismatch`, `deal_type_mismatch`, `missing_rent_metric`, `stale_evidence`, `area_band_mismatch`, and `insufficient_data`. Known critical mismatches reject the comp. Unknown optional values such as area/location are soft flags (`area_unknown`, `location_unknown`) rather than fake mismatches.

The evidence set summary records candidate counts, accepted/rejected counts, high/medium/low counts, best and median quality scores, evidence quality bucket, optional confidence cap, review reasons, and `force_review`. No accepted comps means no comp-derived estimate and review is forced. One accepted comp forces review and caps evidence confidence at `0.5`; only low-quality comps are indicative and capped at `0.35`. Hard-rejected comps are excluded only from comp-derived rent estimation and are not deleted or mutated.

Compact facts are stored under `investment_metrics.market_evidence.comparable_quality`, for example:

```json
{
  "comparable_quality_model_version": "v0",
  "comparables": [
    {"evidence_id": 123, "quality_score": 92, "quality_bucket": "high", "accepted": true, "quality_flags": ["fresh", "area_similar", "location_match"]},
    {"evidence_id": 456, "quality_score": 0, "quality_bucket": "rejected", "accepted": false, "rejection_reason": "deal_type_mismatch"}
  ],
  "summary": {"accepted_count": 1, "rejected_count": 1, "force_review": true, "review_reasons": ["single_comp_cannot_support_strong_estimate"]}
}
```

The market evidence fingerprint includes the selected evidence fields already used by the deterministic quality helper, the retrieval `as_of` datetime, and the comparable quality model version so reproducible analysis input changes when quality-relevant evidence inputs change. This is an evidence-discipline aid only, not a professional appraisal or valuation claim.

## PR25: comparable selection policy v2

PR25 adds deterministic comparable selection policy v2 before the existing PR24 comparable quality scoring. It answers which already persisted evidence may be selected or reused for an analysis run; PR24 still answers how good each selected comparable is. PR26 adjusted comparable modeling is not implemented: there is no adjusted rent, adjusted price, adjusted median, area normalization factor, rent correction factor, or adjustment flags.

The policy version is `comparable_selection_policy_version = "v2"`. Selection is per analysis run and is written only to the target analysis facts. Market evidence rows are not mutated with selected/rejected state, no migration was added, and no backfill is performed.

The explicit target context uses deterministic fields: target listing external id when available, analysis profile, estimate purpose, asset type, deal type, configured location key for cross-listing reuse, and target area when the caller has it. The helper accepts a timezone-aware deterministic `as_of`; freshness cutoffs derive from that value and the helper does not call current-time functions.

Selection runs in stages:

1. bounded SQL candidate retrieval from existing `market_evidence_items` using listing id or explicit location key plus asset type, deal type, evidence type, and a hard limit;
2. PR25 hard gates for supported comparable evidence, asset/deal compatibility, rent metric, source trace, freshness, location key, and area compatibility;
3. PR24 comparable quality scoring on selected candidates only;
4. comp-derived rent estimation uses only selected candidates accepted by PR24 quality.

Cross-listing reuse is allowed only for the existing `same_location_key` policy and requires the configured location key to match. There is no city-wide median, no profile/all-listing fallback, no progressive scope widening when too few comps survive, no semantic/fuzzy/embedding matching, and no geocoding or new location-equivalence taxonomy.

Stable rejection reasons include `asset_type_mismatch`, `deal_type_mismatch`, `location_key_mismatch`, `area_band_mismatch`, `stale_evidence`, `missing_source_trace`, `missing_rent_metric`, `unsupported_evidence_type`, `cross_listing_reuse_not_allowed`, and `insufficient_match_data`. Stable selection reasons include `same_listing_direct_evidence`, `same_location_key_reuse`, and `policy_selected_after_hard_gates`. Area compatibility is a hard gate only; it does not calculate adjusted values.

A source trace means a stable persisted origin reference such as an evidence id, source URL, content hash, or listing external id. Legacy SQL selection still keeps the existing source-URL discipline for stored reusable evidence, while the policy helper treats stable persisted references as source trace.

Compact facts are stored under `investment_metrics.market_evidence.comparable_selection_policy` with version, `as_of`, target context summary, limits, candidate/selected/rejected counts, truncation flags, selected refs, capped rejected refs, and review reasons. Rejected facts are capped and deterministically ordered by the policy's candidate ordering. The market evidence fingerprint includes the selection policy version, deterministic `as_of`, target context, candidate limits, selected items, selected/rejected decision facts, source-trace fields, and the PR24 quality model version. Manual rent/manual assumptions remain primary and are not overwritten or degraded by selected or rejected comps.

This feature is deterministic evidence discipline only. It is not a professional appraisal or valuation claim and it performs no LLM, agent, RAG, external API, parser, alert delivery, or admin write action.
