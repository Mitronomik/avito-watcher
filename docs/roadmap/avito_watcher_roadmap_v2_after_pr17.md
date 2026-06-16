# Новый roadmap avito-watcher после PR16/17

## Целевое позиционирование

После PR16/17 `avito-watcher` становится не “оценщиком”, а сильной внутренней production-системой инвестиционного скрининга.

Правильная формулировка:

```text
avito-watcher is an internal investment screening and decision-support system.
It is not a professional appraisal / valuation report system.
```

Система должна уметь:

```text
найти объект
очистить мусор
проверить свежесть
посчитать deterministic score
подтянуть market evidence
посчитать первичную инвестиционную гипотезу
показать источники
объяснить риски
зафиксировать решение человека
улучшаться через backtesting
```

Система не должна автоматически:

```text
принимать инвестрешение
выдавать юридически значимый отчёт оценщика
заменять профессиональный appraisal
мутировать фильтры/код/score без человека
```

## Главный принцип развития

```text
Clean data first.
Deterministic gates second.
Deterministic scoring third.
Human-readable LLM explanation fourth.
RAG memory fifth.
External research sixth.
Investment scoring with comps seventh.
Human outcome feedback eighth.
Comparable quality ninth.
Adjusted comps tenth.
Scenario / DCF / financing later.
Strategy loop last.
```

Рабочая модель:

```text
Deterministic system decides.
Agents investigate and explain.
RAG provides context.
Research validates market assumptions.
Human approves action.
Outcomes calibrate the system.
```

## Статус после PR16/17

При условии, что PR16 и PR17 реализованы, покрыты тестами и прошли production smoke:

```text
Личный / внутренний production use: 8/10
Команда брокеров / аналитиков: 7/10
Автоматическое принятие инвестрешений: 4/10
Профессиональная appraisal/valuation-система: 3/10
```

Расшифровка:

```text
Да: investment screening, shortlist, evidence trail, human review.
Нет: appraisal report, valuation opinion, legal-grade valuation conclusion.
```

PR17 не делает систему production-grade сам по себе. PR17 — это strategy/audit loop: он помогает находить проблемы в searches, filters, parser и false positives. Production maturity закрывается отдельными operational PR.

---

# Phase A — закрыть PR16/17 без расползания scope

## PR16 — Investment scoring with market comps

Статус: следующий/текущий.

Цель:

```text
investment profiles v1 use stored SQL-backed market evidence as optional rent comps
```

Ключевые ограничения:

```text
market evidence opt-in
manual rent primary by default
selected evidence before input_hash
same evidence context passed to provider
no provider-side hidden retrieval
no cross-listing reuse
no LLM/research/external calls
no evidence mutation
no purchase price inference
```

Acceptance:

```text
no manual rent + enough comps -> market rent estimate
no manual rent + weak/no comps -> review
manual rent + weak comps -> manual calculation not degraded
single comp cannot be strong
input_hash includes selected evidence fingerprint
facts_json includes evidence ids/source urls/content hashes
```

## PR17 — Weekly StrategyAgent with system memory RAG

Цель:

```text
weekly audit task that reviews SQL stats, alerts, false positives, research quality, search quality and proposes improvements
```

Scope:

```text
weekly_strategy_agent task type
manual/scheduled opt-in only
reads SQL stats
reads system_memory / handoff notes
produces weekly report
proposes search/filter/parser/research improvements
```

Not allowed:

```text
no automatic filter mutation
no automatic code change
no score/verdict mutation
no alert suppression
no autonomous strategy execution
```

Acceptance:

```text
agent proposes, human approves
weekly report saved
all recommendations are text proposals
no automatic operational side effects
```

---

# Phase B — production operations hardening

Эта фаза нужна, чтобы система стала удобной и безопасной для внутренней команды, а не только для личного использования.

## PR18 — Human decision logging + outcome tracking

Почему PR18 раньше comp adjustments:

```text
без human outcomes невозможно понять, улучшают ли scoring/comps качество отбора
```

Добавить:

```text
human_reviews
human_review_actions
investment_decisions
```

Фиксировать:

```text
human_verdict
called_owner
sent_to_expert
rejected_reason
watchlist
confirmed_rent
confirmed_purchase_price
confirmed_area
confirmed_opex
confirmed_capex
deal_status
false_positive
false_negative
notes
reviewed_by
reviewed_at
```

Acceptance:

```text
можно открыть объект и зафиксировать решение человека
решение не меняет deterministic score/verdict
решение доступно для backtesting
история решений сохраняется
```

## PR19 — Admin UI for searches, analyses, agents, evidence and reviews

Цель:

```text
сделать систему управляемой без psql и ручных CLI-команд
```

UI должен показывать:

```text
search jobs
last run status
parser errors
latest listings
listing analyses
risk flags
market evidence
agent tasks
human review status
delivery status
```

Actions:

```text
pause/resume search
edit filters_json safely
run dry-run
trigger manual analysis
create manual market_research task
create manual review/data-quality task
record human decision
```

Not allowed:

```text
no automatic score override
no hidden filter mutation
no direct SQL-like admin power without audit
```

## PR20 — Alert delivery retry dashboard / outbox v1

Проблема:

```text
Google Sheets/email/telegram/jsonl failures must be observable and retryable
```

Добавить:

```text
alert_delivery_attempts
channel
payload_hash
status
attempt_count
last_error
next_retry_at
sent_at
```

Acceptance:

```text
failed channel delivery does not create false AlertSent
retry works for existing listings
dashboard shows failed deliveries
manual retry possible
JSONL remains audit trail
```

## PR21 — Production health dashboard and run observability

Добавить dashboard/endpoint:

```text
worker heartbeat
last monitor cycle
active searches
parser error rate
captcha/block/layout_changed rate
analysis success/failure/reuse stats
agent task queue stats
market research stats
alert delivery stats
DB migration/head status
disk usage
```

Acceptance:

```text
понятно, жив ли сервис
понятно, где сломался pipeline
нет silent failures
```

## PR22 — Backup / restore / retention policy

Добавить:

```text
daily PostgreSQL backup
backup verification
restore procedure
retention policy
data cleanup policy
large JSON cleanup
debug_html cleanup
market evidence expiry cleanup
```

Acceptance:

```text
есть documented restore drill
можно восстановить БД на тестовом окружении
старые/мусорные данные не растут бесконечно
```

## PR23 — Access control and audit log

Если системой пользуется команда:

```text
roles: admin / analyst / viewer
audit log for admin actions
API key rotation
read-only mode
sensitive config not exposed
```

Audit events:

```text
search edited
filters changed
manual task created
human decision recorded
evidence ingested
alert retried
```

Acceptance:

```text
видно кто что изменил
опасные действия требуют admin role
нет secrets в логах/payload
```

---

# Phase C — comparable quality and market data discipline

Эта фаза делает market evidence не просто “списком comps”, а контролируемым рыночным evidence layer.

## PR24 — Comparable quality scoring

Цель:

```text
оценивать качество каждого comparable, а не брать median blindly
```

Добавить поля/оценки:

```text
comp_quality_score
comp_similarity_score
comp_rejection_reason
comp_adjustment_flags
```

Критерии качества:

```text
same asset type
same deal type
same listing target / allowed scope
freshness
source quality
source url present
rent metric present
area band similarity
location_key match
floor / first line if available
condition if available
tenant/lease info if available
```

Acceptance:

```text
bad comps excluded or marked low quality
single weak comp cannot drive estimate
facts_json shows why comps were selected/excluded
```

## PR25 — Comparable selection policy v2

Цель:

```text
разрешить controlled cross-listing/location-level reuse, но только с явной matching policy
```

Scope:

```text
location_key based reuse
asset_type/deal_type strict match
area band
freshness
confidence threshold
quality threshold
source trace
max distance only if geocoding exists later
```

Not allowed:

```text
no semantic fuzzy matching yet
no broad city-wide median
no unbounded location search
no hidden comps in provider
```

Acceptance:

```text
evidence from other listings can be used only when matching policy approves it
selected evidence fingerprint still enters input_hash
```

## PR26 — Adjusted comparable model v0

Цель:

```text
не просто median, а controlled adjustments
```

Initial adjustments:

```text
area band adjustment
condition/capex flag
first-line flag
floor/access flag
asking-to-effective discount
source freshness confidence penalty
```

Rules:

```text
adjustments deterministic
all adjustments visible in facts_json
rent_per_m2-first additive deltas with single/total caps
manual rent remains primary
freshness changes confidence/review only, not rent value
asking discount applies only to explicit asking evidence
no hidden ML
no source verification PR27, sale/cap-rate PR28, scenario/DCF/financing
no professional appraisal/valuation claim
```

Acceptance:

```text
raw comp rent
adjusted comp rent
adjustment reasons
adjusted median
confidence cap
```

## PR27 — Source quality and evidence verification

Цель:

```text
разделить source-backed, weak, stale, duplicated and unverifiable evidence
```

Добавить:

```text
source_quality_score
source_type
is_primary_source
is_marketplace_listing
is_broker_note
is_confirmed_by_human
verification_status
verified_by
verified_at
```

Acceptance:

```text
asking listing evidence is not treated as confirmed deal evidence
human-confirmed evidence can be flagged separately
low-source-quality evidence cannot produce strong without review
```

---

# Phase D — investment methodology upgrade

Эта фаза приближает систему к underwriting, но всё ещё не делает юридический appraisal.

## PR28 — Cap rate / sale comps evidence

Цель:

```text
для commercial_sale_investment добавить не только rent comps, но и sale/cap-rate evidence
```

Evidence types:

```text
sale_comparable_candidate
cap_rate_observation
yield_observation
sale_price_per_m2
noi_proxy
```

Calculations:

```text
derived_value_by_income = stabilized_noi / market_cap_rate
asking_price_vs_derived_value
sale_price_per_m2_vs_market
```

Acceptance:

```text
rent comps and sale/cap-rate evidence separated
asking rent/listing price not treated as transaction truth
source trace preserved
confidence caps applied
```

## PR29 — Scenario engine low/base/high

Цель:

```text
дать не одну median estimate, а deterministic scenario range
```

Scenarios:

```text
conservative
base
optimistic
```

Inputs:

```text
rent low/base/high
vacancy low/base/high
opex low/base/high
capex low/base/high
exit yield/cap rate if available
```

Outputs:

```text
gross_yield_low/base/high
noi_yield_low/base/high
payback_low/base/high
risk flags
sensitivity table
```

Acceptance:

```text
scenario inputs visible
scenario assumptions traceable
no hidden model assumptions
```

## PR30 — DCF-lite / IRR / NPV engine

Цель:

```text
перейти от simple yield/payback к multi-year underwriting
```

Inputs:

```text
holding_period_years
rent_growth
opex_growth
vacancy_by_year
capex_schedule
exit_cap_rate
sale_costs
discount_rate
```

Outputs:

```text
annual cash flow
terminal value
NPV
IRR
equity multiple
sensitivity table
```

Not allowed:

```text
no professional valuation opinion
no final buy/sell automation
```

Acceptance:

```text
DCF is deterministic
assumptions visible
missing assumptions -> review
```

## PR31 — Financing and tax layer

Цель:

```text
рассчитать investor-level returns, not only asset-level attractiveness
```

Inputs:

```text
LTV
interest rate
loan term
amortization
debt service
tax regime
property tax
VAT handling
broker fee
legal/registration costs
insurance
management fee
maintenance reserve
```

Outputs:

```text
cash-on-cash return
DSCR
levered IRR
equity required
annual debt service
post-tax cash flow proxy
```

Acceptance:

```text
asset-level metrics remain separate from investor-level metrics
all financing/tax assumptions explicit
no default hidden tax model
```

---

# Phase E — SPb локальная модель и подтверждение данных

## PR32 — SPb submarket taxonomy

Цель:

```text
сделать локальную классификацию рынка СПб
```

Таксономия:

```text
district
metro zone
street class
CBD / near-CBD / residential
tourist corridor
business corridor
sleeping district
old fund
new-build commercial shell
street-retail
office
warehouse
ПСН
ГАБ
first-line
courtyard
basement/semi-basement
```

Acceptance:

```text
thresholds can depend on submarket segment
facts_json shows segment classification
classification is deterministic or human-approved
```

## PR33 — SPb object quality model

Цель:

```text
учесть микролокацию и физические характеристики помещения
```

Signals:

```text
first line
separate entrance
showcase windows
power_kw
wet point
ventilation
ceiling height
floor
loading access
signage visibility
pedestrian traffic proxy
car traffic proxy
parking
repair state
tenant fit
legal use constraints
```

Sources:

```text
listing details
LLM extraction with evidence
human confirmation
market research notes
```

Acceptance:

```text
quality signals do not appear without evidence
low-confidence signals cap verdict
human-confirmed signals separated from extracted signals
```

## PR34 — Confirmed data workflow

Цель:

```text
отделить marketplace hypothesis от confirmed facts
```

Confirmed fields:

```text
confirmed_rent
confirmed_price
confirmed_area
confirmed_opex
confirmed_capex
confirmed_lease_term
confirmed_vacancy
confirmed_utility_terms
confirmed_tax_terms
confirmed_legal_status
confirmed_source
confirmed_by
confirmed_at
```

Acceptance:

```text
confirmed facts can override hypotheses only with audit trail
original parsed/researched data remains preserved
```

---

# Phase F — backtesting, calibration and model governance

## PR35 — Backtesting dashboard v0

Цель:

```text
понять, насколько scoring реально работает
```

Metrics:

```text
precision@top10
precision@strong
strong_to_call_conversion
strong_to_expert_conversion
strong_to_deal_conversion
false_strong_rate
false_negative_rate
manual_rent_error
market_rent_error
median absolute percentage error
average time to review
research usefulness score
```

Acceptance:

```text
видно, какие профили дают мусор
видно, какие filters шумят
видно, где модель ошибается
```

## PR36 — Calibration loop

Цель:

```text
обновлять thresholds на базе outcomes, но только через human-approved PR/config change
```

Allowed:

```text
recommend threshold changes
recommend comp quality weights
recommend exclusion rules
recommend new SPb segments
```

Not allowed:

```text
automatic threshold mutation
automatic filter mutation
automatic score formula change
```

Acceptance:

```text
StrategyAgent proposes
human approves
change goes through PR/tests
```

## PR37 — Model/version governance

Добавить:

```text
analysis_model_version
investment_model_version
comp_selection_version
adjustment_model_version
scenario_model_version
calibration_version
```

Acceptance:

```text
любое изменение формулы версионируется
старые результаты воспроизводимы
можно сравнить model versions
```

---

# Phase G — investment memo / reporting

## PR38 — Human-reviewed investment memo v0

Цель:

```text
сформировать investment memo для человека, не appraisal report
```

Memo sections:

```text
object facts
data quality
manual assumptions
market evidence
selected comps
excluded comps
rent estimate
NOI bridge
yield/payback
scenario table
risk flags
human questions
recommended next checks
limitations
```

Output:

```text
markdown
PDF later
Google Docs later
```

Acceptance:

```text
memo clearly says it is not appraisal report
all assumptions and evidence traceable
no unsupported facts
```

## PR39 — Valuation-style report structure, non-certified

Цель:

```text
приблизиться по структуре к valuation report, но без заявления о профессиональной оценке
```

Sections:

```text
scope / purpose
basis of analysis
data sources
object identification
market context
highest and best use hypothesis
approaches considered
income approach calculation
comparable evidence
assumptions and limitations
sensitivity
human review
```

Important disclaimer:

```text
This is an internal investment analysis memo, not a certified appraisal or valuation report.
```

Acceptance:

```text
report is transparent
limitations explicit
no legal valuation conclusion
```

---

# Phase H — operational production for team / SaaS-readiness

## PR40 — Team workflow and queues

Add:

```text
review queues
assigned analyst
status pipeline
SLA per object
comments
mentions
export
```

Statuses:

```text
new
needs_review
research_needed
called
confirmed
rejected
watchlist
sent_to_expert
deal_candidate
closed
```

## PR41 — Notification and escalation rules

Add:

```text
alert severity
routing rules
team recipients
daily digest
weekly digest
failed delivery escalation
```

No automatic investment action.

## PR42 — Parser quality and source compliance monitoring

Add:

```text
parser success rate
layout_changed trend
captcha/block trend
source response diagnostics
rate-limit safety
debug retention
source compliance notes
```

## PR43 — Security, privacy and data governance

Add:

```text
secret scanning
payload redaction
contact data policy
user access policy
audit export
retention policy
source terms/compliance checklist
```

## PR44 — Staging environment and release process

Add:

```text
staging compose/env
migration rehearsal
production deploy checklist
rollback checklist
seed smoke data
release notes template
```

## PR45 — Production SLA dashboard

Add:

```text
uptime
worker heartbeat
queue lag
alert delivery latency
failed delivery count
parser error rate
analysis failure rate
agent failure rate
research ingestion failure rate
DB backup status
disk usage
```

---

# Production readiness gates

## Gate 1 — Internal personal production

Minimum:

```text
PR16 complete
PR17 complete
alerts stable
manual smoke handoffs
health checks
backup exists
no side effects
```

Status target:

```text
usable by owner with manual supervision
```

## Gate 2 — Internal team production

Minimum:

```text
PR18 human decision logging
PR19 admin UI
PR20 alert retry dashboard
PR21 health dashboard
PR22 backup/restore
PR23 access/audit
```

Status target:

```text
usable by small analyst/broker team
```

## Gate 3 — Investment analytics production

Minimum:

```text
PR24 comparable quality
PR25 selection policy v2
PR26 adjusted comps
PR28 cap-rate/sale evidence
PR29 scenarios
PR35 backtesting
```

Status target:

```text
reliable internal investment screening platform
```

## Gate 4 — Valuation-style internal memo

Minimum:

```text
PR30 DCF-lite
PR31 financing/tax
PR32 SPb taxonomy
PR33 object quality model
PR34 confirmed data workflow
PR38 memo
PR39 valuation-style structure
```

Status target:

```text
strong internal investment memo system, not certified appraisal
```

## Gate 5 — SaaS / external users

Minimum:

```text
PR40 team workflow
PR41 notifications/escalation
PR42 parser quality monitoring
PR43 security/data governance
PR44 staging/release process
PR45 SLA dashboard
legal/compliance review
```

Status target:

```text
controlled external product candidate
```

---

# What remains explicitly not covered

Even after this roadmap, unless separately certified and legally reviewed, the system should not claim:

```text
certified appraisal
professional valuation opinion
IVS/RICS/ФСО-compliant valuation report
automated investment advice
guaranteed market value
guaranteed rent
guaranteed yield
```

Correct positioning:

```text
internal investment screening
decision support
evidence-backed underwriting assistant
human-reviewed investment memo
```

Final target:

```text
A controlled investment analytics platform that finds candidates, explains why they are interesting or risky, shows evidence, learns from human outcomes, and supports expert decisions without pretending to replace a licensed valuer.
```

