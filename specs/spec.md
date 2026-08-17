# Spec — Portfolio Metrics Extraction (crawl phase)

## Goal

Given a folder of portfolio-company PDF reporting packages, extract a meaningful set of
financial/operating metrics and organize them into a reviewable, analysis-ready structure —
with enough provenance that every number can be traced back to its source.

## Why the design looks the way it does

A read-through of the 25 sample PDFs shows the real problem is not PDF parsing — it is
inconsistency across companies and time:

- **Label drift**: NovaCloud "Total Billings" (Q2 2024) becomes "Recognized Revenue" (Q2 2025);
  LendBridge "Credit Loss Rate" becomes "Net Charge-off Rate" (footnotes state equivalence).
- **Entity rename**: FleetLink Logistics rebranded to Apex Freight Solutions (Apr 2025).
- **Mixed currencies**: PeopleFlow reports in GBP; MediSight prints "6.8M" with no symbol.
- **Mixed period bases**: NovaCloud reports monthly net burn; ConstructIQ quarterly.
- **Restatement**: PeopleFlow Q2 2025 restates Q1 revenue (4.7M → 4.6M); Q1 report not reissued.
- **Prose-only metrics**: PeopleFlow gross margin appears only in commentary, not in a table.
- **Overlapping sources**: the Portfolio Snapshot re-reports four companies that also file
  standalone reports.
- **Multi-period tables**: ConstructIQ/ClearPay tables carry current and prior quarter columns.

## Approach

LLM-based structured extraction (OpenAI structured outputs, Pydantic schema), followed by a
deterministic normalization layer in Python. The LLM reads; code applies rules.

1. **Ingest** — pdfplumber text per page (files are text-native; no OCR).
2. **Extract** — one LLM call per document; open extraction of all metrics with per-metric
   provenance: verbatim label, value, unit, currency, period basis, page, location
   (table / commentary / footnote). The prompt includes the canonical metric dictionary
   (`config/metrics.yaml`, name → definition), so the model also assigns each metric a
   canonical name where one fits.
3. **Normalize** — validate canonical assignments against the dictionary (unknown → tagged
   non-canonical, kept), entity map (FleetLink → ApexFreight), source precedence
   (standalone > snapshot; restated > original, superseded rows retained and flagged).
4. **Check** — provenance verification (extracted labels/values string-matched back to source
   text) plus three data-quality checks: duplicates, cross-source consistency, currency
   completeness. All issues land in `output/flags.csv` with severity.

## Canonical metrics (crawl scope)

revenue, arr, gross_margin, net_revenue_retention, logo_churn, headcount, cash, net_burn.
Sector-specific metrics (TPV, loan book, on-time delivery, …) are extracted and retained as
non-canonical rows rather than forced into a SaaS-shaped schema.

## Outputs

- `output/metrics_long.csv` — tidy fact table (company, metric, period, value, unit, currency,
  provenance columns); the natural input for a future dashboard or database.
- `output/pivot.csv` — companies × quarters for canonical metrics, for human review.
- `output/flags.csv` — everything the pipeline is unsure about (severity: error/warning/info).

## Key assumptions

- PDFs are text-native (true for the sample set); scanned documents are out of scope.
- Currency is recorded, not converted — cross-currency comparison needs an FX policy (next step).
- A later filing restating an earlier period supersedes it; both values are retained.
- Standalone company reports take precedence over the portfolio snapshot where they overlap.

## Out of scope (deliberate cuts for a 1–2 hour PoC)

OCR, FX conversion, database, UI/dashboard, retry/backoff, logging framework (stdlib only),
CI, data-quality frameworks, LLM accuracy eval harness (first "walk" step — see README next steps).

## Acceptance

- One command processes the folder; a failed document becomes a flag row, never a crashed run.
- Every extracted value is traceable to file + page + verbatim label.
- Unit tests cover the deterministic layers (mapping, precedence, verification, DQ) and run
  without an API key.
