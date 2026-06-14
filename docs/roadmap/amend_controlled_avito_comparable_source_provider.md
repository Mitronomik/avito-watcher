# Roadmap Amend — Controlled Avito SERP Comparable Source Provider

```yaml
status: accepted roadmap amend draft
project: avito-watcher
scope: controlled market evidence source collection
recommended_file: docs/roadmap/amend_controlled_avito_comparable_source_provider.md
date: 2026-06-14
```

## 1. Purpose

This amend adds a controlled Avito comparable search source provider to the roadmap.

The feature is not a crawler and not an autonomous agent.

Correct positioning:

```text
Controlled Avito comparable search is a bounded source provider for manual market_research tasks.
It collects limited Avito SERP listing cards as source-backed comparable evidence.
It does not run in monitor cycle.
It does not mutate score/verdict/alerts/searches/filters.
It does not replace professional valuation data.
```

Target pipeline:

```text
manual market_research AgentTask
-> ResearchAgent
-> AvitoComparableSearchProvider
-> allowlisted Avito SERP request builder
-> AvitoParser.parse_search(...)
-> ListingCard[]
-> deterministic pre-LLM comparable filters
-> ResearchSource[]
-> source-backed ResearchAgent provider
-> strict result validation
-> MarketEvidenceIngestionService
-> market_research_runs / market_evidence_items
-> later deterministic investment reanalysis through existing evidence selection/input_hash path
```

Important wording:

```text
Avito comparable evidence is marketplace asking evidence.
It is not confirmed transaction evidence.
It is not achieved rent.
It is not a certified market value source.
```

## 2. Roadmap placement

This amend should be inserted into Phase C:

```text
Phase C — comparable quality and market data discipline
```

Recommended sequence:

```text
PR17  - Weekly StrategyAgent with system memory RAG
PR18  - Human decision logging + outcome tracking
PR19  - Admin UI for searches, analyses, agents, evidence and reviews
PR20  - Alert delivery retry dashboard / outbox v1
PR21  - Production health dashboard and run observability
PR22  - Backup / restore / retention policy
PR23  - Access control and audit log

PR24a - Controlled Avito SERP comparable source provider
PR24b - Comparable quality scoring
PR25  - Comparable selection policy v2
PR26  - Adjusted comparable model v0
PR27  - Source quality and evidence verification
```

Rationale:

```text
PR24a supplies bounded source-backed comparable evidence.
PR24b scores quality of comparable evidence.
PR25 expands controlled selection/reuse policy.
PR26 adjusts comps.
PR27 separates asking listings from verified/confirmed evidence more formally.
```

PR24a may be done after PR17 if investment analytics is the priority, but it does not replace production hardening PR18-PR23.

## 3. New roadmap item

## PR24a — Controlled Avito SERP comparable source provider

### Goal

Allow manual `market_research` tasks to collect bounded Avito SERP comparable sources for one target listing.

The provider should:

```text
build 1-3 safe Avito SERP requests from operator-provided base URLs
parse only search results pages
take first N cards
filter cards deterministically before LLM/provider call
convert cards to ResearchSource objects
send only bounded source package to ResearchAgent provider
persist only validated evidence through existing market evidence ingestion path
```

### Not a crawler

PR24a must not become a crawler.

Not allowed:

```text
no monitor-cycle integration
no automatic research task creation
no autonomous Avito crawling
no unbounded pagination
no item detail pages by default
no contacts
no phone extraction
no login
no captcha solving
no anti-bot bypass
no proxy scaling for scraping
no broad city-wide collection
no search_jobs mutation
no filters_json mutation
no listings/snapshots/search_matches creation from comparable sources
no score/verdict mutation
no alert creation/suppression
```

### Manual and opt-in only

PR24a must run only inside manually created `market_research` AgentTasks.

Required defaults:

```env
RESEARCH_AVITO_COMPS_ENABLED=false
RESEARCH_AVITO_COMPS_SOURCE_COMPLIANCE_ACK=false
RESEARCH_AVITO_COMPS_MAX_QUERIES=2
RESEARCH_AVITO_COMPS_MAX_PAGES=1
RESEARCH_AVITO_COMPS_MAX_CARDS=10
RESEARCH_AVITO_COMPS_MAX_SOURCES=10
RESEARCH_AVITO_COMPS_AREA_TOLERANCE=0.35
RESEARCH_AVITO_COMPS_MAX_AGE_HOURS=168
RESEARCH_AVITO_COMPS_REQUIRE_PUBLISHED_AT=true
RESEARCH_AVITO_COMPS_DELAY_MS=1500
RESEARCH_AVITO_COMPS_JITTER_MS=1000
RESEARCH_AVITO_COMPS_STOP_ON_BLOCK=true
RESEARCH_AVITO_COMPS_DEBUG=false
```

Provider must fail closed if:

```text
RESEARCH_AVITO_COMPS_ENABLED=false
RESEARCH_AVITO_COMPS_SOURCE_COMPLIANCE_ACK=false
base URL is missing
host is not allowed
parser reports captcha/block
limits are exceeded
```

## 4. Source compliance gate

Before enabling PR24a in production, source compliance must be reviewed and documented.

Required document:

```text
docs/source_compliance/avito.md
```

It must record:

```text
who reviewed source compliance
date of review
which source pages/terms were checked
allowed data scope
forbidden data scope
rate-limit policy
debug retention policy
stop-on-block policy
operator acknowledgement
```

Safe policy:

```text
public SERP cards only
no contacts
no phones
no login-only data
no private/user account paths
no captcha bypass
small manual limits
stop on block/captcha
cache/dedupe source URLs
source compliance note retained in docs
```

PR24a must not require real Avito external calls in default smoke.

## 5. URL policy

The source provider must not let LLM build final Avito URLs.

Allowed:

```text
operator-provided Avito SERP base URL
strict host allowlist
deterministic validation
optional bounded query normalization only if safe
post-parse deterministic filtering
```

Not allowed:

```text
LLM-generated arbitrary URL
non-Avito host
login/account/private paths
unknown URL expansion
unbounded pagination
city-wide broad median source
```

Recommended config in manual task payload or filters:

```json
{
  "research_source": "avito_comparable_search",
  "avito_comps_base_urls": [
    "https://www.avito.ru/sankt-peterburg/kommercheskaya_nedvizhimost/sdam-ASgBAgICAUSwCNRW"
  ],
  "avito_comps_mode": "rent",
  "avito_comps_max_pages": 1,
  "avito_comps_max_cards": 10,
  "avito_comps_area_tolerance": 0.35,
  "avito_comps_max_age_hours": 168,
  "avito_comps_require_published_at": true
}
```

Do not hardcode fragile Avito query parameters unless they are already safely supported and tested.

Prefer:

```text
operator copies correct category/city/deal URL from Avito UI
provider validates and bounds it
parser extracts cards
deterministic filters remove bad comps
```

## 6. ResearchSource schema

Add a bounded source schema for source-backed research context.

Recommended shape:

```python
@dataclass(frozen=True)
class ResearchSource:
    source_index: int
    source_type: str
    url: str
    title: str | None
    publisher: str
    published_at: str | None
    retrieved_at: str
    content_hash: str
    extracted: dict
```

For Avito SERP comps:

```json
{
  "source_index": 0,
  "source_type": "avito_serp_listing",
  "url": "https://www.avito.ru/...",
  "title": "Помещение свободного назначения, 62 м²",
  "publisher": "avito",
  "published_at": "2026-06-14T10:00:00+00:00",
  "retrieved_at": "2026-06-14T12:00:00+00:00",
  "content_hash": "...",
  "extracted": {
    "asset_type": "commercial",
    "deal_type": "rent",
    "area_m2": 62,
    "rent_rub_per_month": 130000,
    "rent_per_m2_rub": 2096,
    "location_text": "Санкт-Петербург, ..."
  }
}
```

Do not pass raw HTML to the provider.

Do not pass unbounded descriptions.

Do not include phone/contact fields.

## 7. Deterministic pre-provider filtering

Avito comps must be filtered before source-backed LLM analysis.

Minimum filters:

```text
same expected deal_type
same expected asset_type
area_m2 exists
area within configured tolerance
rent/price metric exists
rent_per_m2/price_per_m2 within sane range
published_at exists if require_published_at=true
not same external_id as target listing
not storage/garage/parking object
not duplicate normalized URL
not blocked/captcha result
```

Commercial rent defaults:

```text
rent_per_m2_rub >= 300
rent_per_m2_rub <= configured max, default 5000
```

If target area is missing:

```text
do not use area-band SERP comps as reusable evidence
return limitation: target_area_missing_for_comparable_search
```

## 8. Provider-agnostic ResearchAgent integration

Do not name DeepSeek as an architectural dependency.

Correct wording:

```text
source-backed ResearchAgent provider
openai-compatible provider when configured
DeepSeek may be one configured provider, not a core architecture component
```

Provider receives:

```text
target listing facts
latest deterministic analysis summary if available
bounded ResearchSource[]
source indexes
limitations
compliance/source constraints
```

Provider must not receive:

```text
secrets
raw HTML
contacts
phone data
unbounded debug payload
private account data
```

## 9. Strict post-provider validation

After provider response, validate strictly.

Required:

```text
all comparable candidates must cite valid source_indexes
source_indexes must refer to provided ResearchSource items
source URL host must be allowed
source_type must be avito_serp_listing
rent comp cannot be sale comp
sale comp cannot be rent comp
asset_type must be allowed enum
deal_type must be allowed enum
numeric fields must be int/float/null
no bool/string/dict/list numeric fields
no negative values
confidence must be bounded
```

For Avito rent comp:

```text
area_m2 required
rent_rub_per_month or rent_per_m2_rub required
source_url required
```

Reusable policy:

```text
confidence < 0.7 -> is_reusable=false
missing required source/rent/area -> is_reusable=false or reject
asking listing evidence -> evidence_json.is_confirmed_deal=false
```

## 10. Evidence ingestion

AvitoComparableSearchProvider must not write market evidence directly.

Correct split:

```text
AvitoComparableSearchProvider returns ResearchSource[]
ResearchAgent returns validated structured result
MarketEvidenceIngestionService persists successful validated evidence
```

Persisted evidence should include:

```text
source_type = avito_serp_listing
evidence_origin = marketplace_asking_listing
publisher = avito
is_confirmed_deal = false
source_url
source_url_normalized
content_hash
checked_at
expires_at
confidence
is_reusable
reuse_block_reason if not reusable
evidence_json with source details
```

If current schema lacks explicit fields, store these in `evidence_json` and document that PR27 may promote them to first-class fields.

## 11. Relation to investment scoring

PR24a must not change investment scoring.

It only creates source-backed evidence that later deterministic analysis may consume through existing market evidence selection/fingerprint.

Correct:

```text
market_research creates/ingests evidence
investment analysis later reads selected reusable evidence
selected evidence fingerprint enters input_hash
provider does not query Avito during scoring
```

Incorrect:

```text
InvestmentAnalysisProvider calls Avito
AnalysisProvider starts ResearchAgent
ResearchAgent directly changes score/verdict
Avito comps immediately override score
```

## 12. User-facing wording

When showing this later in Sheets/admin/memos:

```text
Avito comps found: 5
usable comps: 3
excluded comps: 2
observed asking rent range: 1 900-2 300 ₽/м²/мес
base asking-rent estimate: 2 100 ₽/м²/мес
confidence: medium
limitations: Avito SERP only; asking listings; not confirmed deals
```

Preferred wording:

```text
observed asking rent from comparable public listings
```

Forbidden wording:

```text
confirmed market rent
actual achieved rent
certified valuation evidence
guaranteed market rate
```

## 13. Tests required before merge

Minimum tests:

```text
1. Provider disabled path does not call AvitoParser.
2. Compliance ack false prevents parser call.
3. URL builder accepts only allowlisted Avito hosts.
4. Non-Avito host is rejected.
5. Login/account/private paths are rejected.
6. Missing base URL fails closed.
7. LLM/provider cannot inject arbitrary URL.
8. max_queries/max_pages/max_cards/max_sources enforced.
9. Parser captcha/block diagnostic stops remaining requests.
10. No listings are created.
11. No listing_snapshots are created.
12. No listing_search_matches are created.
13. No search_jobs/filters are mutated.
14. Same external_id as target is excluded.
15. Missing area is excluded.
16. Wrong deal_type is excluded.
17. Area outside tolerance is excluded.
18. Bad rent_per_m2 is excluded.
19. Storage/garage/parking objects are excluded.
20. Duplicate normalized source URLs dedupe before provider.
21. Cards convert to ResearchSource with stable source_index.
22. ResearchSource never contains contact/phone fields.
23. Source package respects max input chars.
24. Provider response without source index fails.
25. Provider response with invalid source index fails.
26. Provider response with invalid numeric fields fails.
27. Low-confidence comp ingests as is_reusable=false.
28. Successful validated result can be ingested into market_evidence_items.
29. Evidence is marked marketplace_asking_listing and is_confirmed_deal=false.
30. No score/verdict/alerts/search_jobs/filter mutation.
```

## 14. Suggested production smoke

Default production smoke must be offline/safe:

```text
RESEARCH_AVITO_COMPS_ENABLED=false
RESEARCH_AVITO_COMPS_SOURCE_COMPLIANCE_ACK=false
```

Smoke checks:

```text
handler/service registered
disabled provider returns skipped/failed with clear error
compliance ack false prevents parser call
no external call
no alerts
no listings/snapshots/search_matches
no listing_analyses mutation
no market evidence mutation
no knowledge_notes mutation
worker logs clean
```

Real Avito provider smoke is optional and must be separate:

```text
manual-only
one target listing
one allowlisted base URL
max_queries=1
max_pages=1
max_cards=5
stop_on_block=true
no contacts
cleanup after smoke
source compliance doc committed before enabling
```

## 15. Acceptance criteria

PR24a is acceptable only if:

```text
manual market_research only
default disabled
source compliance ack required
SERP-only
bounded URLs
bounded cards
bounded sources
no captcha bypass
no contacts
no detail pages by default
no monitor integration
no scoring changes
no alert changes
no search/filter mutation
no listings/snapshots/search_matches writes
pre-provider deterministic filtering
strict post-provider validation
market evidence marked as asking listing, not confirmed deal
source trace preserved
TTL/confidence/content_hash preserved
tests cover disabled/compliance/url/filter/validation/no-side-effects
docs explain compliance, limitations and roadmap boundary
```

## 16. Roadmap non-goals

PR24a does not implement:

```text
PR24b comparable quality scoring
PR25 full comparable selection policy v2
PR26 adjusted comps
PR27 full source quality model
sale comps
cap-rate evidence
DCF/scenario modeling
financing/tax layer
admin UI
alert delivery outbox
health dashboard
automatic research
automatic reanalysis
automatic filter mutation
automatic code mutation
```

## 17. Update to production readiness gates

Gate 3 should be amended from:

```text
PR24 comparable quality
PR25 selection policy v2
PR26 adjusted comps
PR28 cap-rate/sale evidence
PR29 scenarios
PR35 backtesting
```

to:

```text
PR24a controlled Avito SERP comparable source provider
PR24b comparable quality
PR25 selection policy v2
PR26 adjusted comps
PR28 cap-rate/sale evidence
PR29 scenarios
PR35 backtesting
```

Rationale:

```text
Investment analytics production needs not only selection/scoring of existing evidence,
but also a controlled source collection path for fresh comparable evidence.
```

## 18. Final principle

Controlled Avito comparable search is allowed only as:

```text
bounded
manual/opt-in
SERP-only
source-backed
strictly validated
evidence-producing
non-mutating
human-reviewed
```

It must never become:

```text
autonomous crawler
monitor-cycle scraper
captcha bypasser
source of direct score/verdict mutation
replacement for confirmed transaction evidence
replacement for professional valuation
```

