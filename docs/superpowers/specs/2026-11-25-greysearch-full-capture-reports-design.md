# Greysearch full-capture reports — design

**Date:** 2026-11-25
**Status:** approved by owner (session same day)
**Version target:** 0.9.5 → 0.10.0
**Scope:** capture-transport fixes + report expansion, one deliverable set

## Problem

Two stacked failures make Master Guides ~1.5 pages of boilerplate instead of
5–10+ pages of usable research:

1. **Capture crisis.** Recent runs accept 0 pages: browser-lane scrapes die on
   TargetClosedError, wedge inside the per-page shared wall ("scrape wall
   (100s) exceeded"), DDG captcha, 429s across public instances. With no
   accepted pages the publisher renders DATA NOT CAPTURED sections.
2. **Distillation.** Even when pages land, the pipeline discards useful
   material before writing: the ranked search pool (150+ leads) never reaches
   the guide; rejected/quarantined sources appear as one-line bullets; tier
   caps scrape only 3/4/6/8 pages (report 8); the model compose lane is off in
   prod and, when on, only touches the executive brief (600-token cap).

Owner decisions locked in this session:

- Scope = both capture fixes AND report expansion.
- Report body = keep everything (deterministic, works with model lane down)
  + model polish when the lane is up.
- Report tier budget = 16 pages / 900 s with a retry wave.
- Implementation shape = one exhaustive Master Guide (no second artifact).

## 1. Capture fixes

**Files:** `research_integration.py`, `browser_session.py`.

- **Browser-session recovery.** When a scrape fails with TargetClosedError
  (browser died mid-run) or a wedge timeout, rebuild the browser session once
  per run and retry the page immediately. The recovery does NOT count toward
  the 3-strike wedge counter (it is a recovery, not a wedge).
- **Retry wave.** After the main scrape loop, if `accepted < page_cap` and
  wall remains, re-attempt pages whose rejection was transport-shaped
  ("scrape wall ... exceeded", wedge errors) once each, bounded by the same
  shared-window formula with the existing 30 s floor. Retry outcomes are
  recorded in the skip/judge records with `retry: true` so the report shows
  which pages survived the second attempt.
- **Proxy law unchanged:** scraping stays DIRECT; IPRoyal remains search-only
  (per the 11-25 incident rule and the IPRoyal routing discipline).

**Success check:** a run with yesterday's failure signature (TargetClosedError
+ per-page walls) accepts pages instead of zeroing out.

## 2. Capture budgets

**File:** `greysearch_mcp.py`.

- `_TIER_CAPS["report"]`: (8, 540.0) → (16, 900.0). brief/standard/deep
  unchanged.
- MCP `max_results` hard cap: 25 → 40; tool schema text updated.
- The ranked candidate pool already slimmed by the search lane (SEARCH_POOL_MAX
  trim) rides onto the result dict as `result["search_pool"]` (url, title,
  snippet, domain, tier, engine, pre-rank) so the guide can render it without
  any new fetching. If it already exists under a different key, render from
  that instead of duplicating.

## 3. Keep-everything guide (rules sections, zero model dependence)

**File:** `greysearch_publisher.py`.

New appendix sections, all rendered from data the run already produced:

- **Search index** — full ranked pool: title, snippet, URL, domain, tier,
  engine, pre-rank position. This is the lead list for follow-up work.
- **Rejected & quarantined sources** — extends today's Research-limitations
  bullets with each entry's page title and snippet plus the judge reason.
- **Per-source detail** — under each accepted source's full text: headings
  outline, rendered tables, code blocks, top 3–5 source-grounded passages
  (reuse the deterministic sentence ranker from `dossier.py`), capped links.

Section 2 keeps full accepted-page text. Per-page prose cap
(BREAKDOWN_PROSE_CAP) 40,000 → 80,000 on report tier, truncation note
unchanged. Rules mode invents nothing; it renders what arrived. Empty runs
(0 accepted) still render their search index and rejected-source appendices,
so even a fully degraded run hands over usable leads.

## 4. Model polish (fail-safe, additive)

**File:** `llm_audit.py` (+ publisher hook in `greysearch_publisher.py` or
`greysearch_mcp._attach_guide`).

- Report tier only. Gate: `GREYSEARCH_ALLOW_LLM_AUDIT=1` opened on the LOCAL
  portal/MCP (public hosting must never set it — repo law unchanged); kill
  switch `GREYSEARCH_NO_MODEL` always wins.
- Chaptered compose: up to 6 chapters, one call each to
  `http://127.0.0.1:11600` with `glm-5.3-flash:cloud`, `think:false`,
  `num_predict` 2048, temperature 0.2; each chapter gets its own dose of
  accepted-source text (per-page excerpts, total ~20–40k chars per run).
- Any failure (429 quota, timeout, unparsable, endpoint down) drops only that
  chapter and records the reason on `result["llm_audit"]`; no retry loops; the
  pipeline never blocks on the model (repo model-lane law).
- Chapters render as a new "Long-form findings" section after the executive
  brief, clearly labeled as model-composed FROM accepted sources; without the
  lane the guide is complete without them. The audit brief lane (600 tokens)
  is untouched.

## 5. Version, deploy, verification

- `pyproject.toml` version bump 0.9.5 → 0.10.0.
- Deploy per one-tree recipe: import smoke in the live tree first; rsync to
  `~/greyagent/core/greysearch` and `~/cortex-agent/mcp/greysearch` (NO
  --delete); guided restart only when no run is in flight.
- New unit tests: retry wave (transport-shaped rejects get re-attempted within
  wall), browser-recovery (TargetClosedError rebuild, recovery not counted as
  wedge), tier caps, search-pool rendering, rejected-source appendix, chapter
  composer fail-safe with a fake 429 endpoint.
- Full existing suite stays green.
- **Success bar:** a report-tier run with pages landing produces a guide of
  ≥ 15,000 chars (≈ 5 printed pages; page ≈ 3,000 chars), typically 30K+.

## Not touched (exclusions)

- Research phase stays model-free; deterministic gate keeps its authority;
  model content only ever summarizes accepted sources and is labeled.
- brief/standard/deep tier budgets, janitor, auto-off/idle law, autostart,
  captcha lanes, IP Royal routing, site builder themes, portal API surface,
  works library, guide scrub and site guardrails.
