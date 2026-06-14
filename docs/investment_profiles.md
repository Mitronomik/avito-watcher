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
