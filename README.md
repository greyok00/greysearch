<div align="center">

<img src="doc/images/greysearch-logo.png" width="380" alt="GreySearch logo">

# GreySearch

**Local-first web research with citations, no account and no telemetry.**

GreySearch runs a complete research pass on your machine: it searches, renders pages in a real headless browser, filters noise, and writes a Master Guide with a source behind every claim.

[![Version](https://img.shields.io/badge/version-0.9.3-2f6f5e)](pyproject.toml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-informational)](pyproject.toml)
[![Platform](https://img.shields.io/badge/platform-Linux-1a1a2e)](#requirements)

[Overview](#overview) · [How it works](#how-it-works) · [Quick start](#quick-start) · [Command line](#command-line) · [Privacy](#privacy) · [License](#license)

</div>

---

## Overview

GreySearch performs end-to-end research against the live web, but the pipeline
and every artifact stay on your machine. It is internet-connected by design:
every research pass needs your network for the searches, the page fetches and
the first-run downloads. What never happens is a call home beyond those
fetches. There is no account, no required API key and no telemetry. The only
network traffic is the search queries you ask for and the pages you fetch.

- **Multiple search lanes** in a deliberate fallback order: local SearXNG,
  public searx.space instances, then direct keyless engines such as DuckDuckGo,
  Wikipedia and GitHub.
- **Real page rendering** with headless Chromium through patchright, so
  JavaScript pages are read rather than guessed from snippets.
- **Noise filtering** for ads, tracking URLs, boilerplate and off-topic pages.
- **Grounded synthesis**: every claim is linked to its source URL and retrieval
  time; conflicting sources are called out rather than averaged away.
- **Three outputs** from the same run: a Markdown **Master Guide**, a themed
  static **HTML site**, or a **PDF**.
- **A self-learning RAG loop** with the companion `knowledge-ingestor` MCP
  server: a search miss can research a topic, quality-gate the pages, and add
  the admitted material to a permanent offline library.

### Quick check

```text
$ greysearch-doctor
  [OK ] search_lanes   direct lanes answered (ddg-html, wikipedia-api)
  [OK ] browser_slots  launched on demand, isolated profile
  [OK ] portal         up on 127.0.0.1:8791
  [OK ] kit_source     17 themes found
 verdict: ALL HEALTHY
```

## How it works

### 1. Search the ladder

The tool checks sources sequentially until it has enough relevant results.
Local SearXNG is tried first; public instances follow; direct keyless engines
are the final fallback. A relevance floor decides when to stop. While the floor
is unmet, the upper rungs keep going and the time budget extends. Every lane
reports whether it ran, failed or was skipped.

The local lane's working-engine state is verified against reality before it is
trusted: cached process state first, then the disk engine-status cache, and
when both come up empty one real unfiltered query against the local instance
is the verdict. A returning query marks the lane working — a probe sweep that
the instance's own single-search limiter can starve never skips the local rung
on its own.

### 2. Fetch and render

Each result URL is loaded in an isolated headless Chromium session. The page is
allowed to execute JavaScript, then the rendered DOM is captured. Rate limits
and pauses between requests are respected; timeouts and blocks are reported,
never guessed. A wedged page load counts against a per-run wedge counter —
the count is zeroed at the start of every run, the wedged scrape's CDP
connection is dropped, and after three wedges the scrape lane stops calling
the browser for the rest of that run, so one bad stretch can never brick
later runs.

Captcha handling is local. Image grids, coordinate challenges and legacy text
captchas use a ladder of local models and vendored weights; no third-party
captcha service or API key is involved. Honest limits — Arkose rotation
puzzles, reCAPTCHA v3 score widgets and missing weights — are recorded as
skips or model-only runs, never as fabricated solves. Set
`GREYSEARCH_CAPTCHA_SOLVER=off` to skip solving entirely.

### 3. Clean and gate

Raw HTML is reduced to readable text. Navigation, sidebars, footers, cookie
notices and promo rails are stripped; pages with insufficient topical
relevance are discarded. Headings, lists and tables are preserved where they
carry meaning.

### 4. Synthesize with citations

Filtered material is grouped by topic and source. Every sentence or claim maps
back to an exact URL and retrieval timestamp; contradictions are noted in the
run report. The result is a bibliography-style Master Guide rather than an
unsourced summary.

### 5. Publish

The same research record can become a Markdown guide, a themed static site with
pagination and a table of contents, or a print-ready PDF. Seventeen themes ship
with the site builder. A chosen image is copied into the guide's own tree only
when it resolves there; anything else falls back to the remote URL, so a build
request cannot move arbitrary readable local files into the web-served guides
tree.

## Quick start

Requires **CPython 3.13+** and **Linux** (the browser layer is tested on
x86-64 Linux). Installation uses the network for dependencies, the Chromium
build and optional captcha weights.

```bash
git clone https://github.com/greyok00/greysearch.git
cd greysearch
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Download the Chromium build used by patchright (first run only)
python3 -m patchright install chromium

# Vendor the local captcha weights (optional but recommended); each
# checksum-pinned model's parent directory is created before fetching
./install.sh

# Confirm the environment
python3 bin/greysearch-doctor.py
```

Run a first research pass:

```bash
python3 bin/greysearch-research.py \
  "impact of renewable energy adoption on grid stability"
```

The Master Guide is written to `guides/<date>/`; override the destination with
`--write-guide-to`. Build a themed site or PDF from the guide afterward.

## Command line

| Command | Purpose |
|---|---|
| `greysearch` | Open the research station: start (or reuse) the portal on `127.0.0.1:8791` and point the browser at it; thin subcommands for `library`, `runs`, `search` and `stop` |
| `greysearch-portal` | Local web UI on `127.0.0.1:8791`: start and monitor passes, browse run records and built sites |
| `greysearch-research` | One-shot research: `greysearch-research "your question"` |
| `greysearch-mcp` | MCP server exposing `greysearch research` to any MCP client |
| `greysearch-build-site` | Turn a Master Guide into a themed static site or PDF |
| `greysearch-doctor` | Validate dependencies, browser slots, search lanes and writable paths |

`pip install .` puts all six commands on `PATH`. The same commands are also
runnable from `bin/` in a checkout.

### MCP and the offline library

`greysearch_mcp.py` exposes research as a tool; the companion
`knowledge-ingestor` server provides `kb_search`, `kb_ingest_text`,
`kb_ingest_urls` and `kb_learn_topic`. A search miss can trigger a deep
research pass, quality-gate the pages by domain, document path, density and
content type, and ingest only admitted material with per-record provenance.
Empty search results mean "escalate", never "guess".

Two walls keep one call from dragging the lane: the server checks whether the
private browser is already busy **before** it binds the slot, so a request that
is about to be rejected as busy never rewrites the process-wide run state out
from under the run that holds the lock; and the thin fill that rescues thin
results runs inside the caller's remaining wall, each fill scrape bounded to
its own 120-second wall on a fresh single-use executor that drops the CDP
connection on a wedge — after three wedges the fill stops calling the browser
for the rest of the run and returns honestly with whatever it has.

## Configuration

- `research/*.json` — shipped tunables: the relevant-result floor, gating
  constants, lane lists and the searx.space roster.
- `~/.config/greysearch/proxy.env` — optional search-proxy credentials. Only
  `HTTP_PROXY` / `HTTPS_PROXY` are read, in memory; page scrapes never use the
  proxy.
- Environment variables such as `GREYSEARCH_RELEVANT_TARGET`,
  `GREYSEARCH_PORTAL_PORT` and `GREYSEARCH_CAPTCHA_SOLVER` adjust behavior
  without code changes.

## Privacy

Nothing leaves your machine except the search queries sent to public search
engines and the HTTP requests for the pages those engines return. Guides,
fetched assets and build outputs stay under the project directory. There is no
analytics, crash reporting or usage beacon. Delete or archive `out/`, `sites/`,
`guides/` and run records whenever you like.

The optional post-research model audit never runs unless you configure a local
model endpoint yourself; the research phase itself does not call a model.

## Requirements

- CPython 3.13 or newer.
- Linux, x86-64.
- A working Chromium download through patchright.
- Network access for searches, page fetches and first-run downloads.

## Development

```bash
python3 -m pytest tests/
python3 -m ruff check .
```

CI runs both, builds a wheel, checks that the wheel contains only pure Python,
and installs it into a clean virtual environment. The tests cover search pacing,
captcha waits, proxy lanes, relevance floors, browser-slot claims, portal
behavior, hermetic scrape-lane wedge coverage and MCP contracts.

## Credits

- [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) — Apache-2.0
- Flask — BSD-3-Clause
- Markdown — BSD-3-Clause
- PyYAML — MIT
- hcaptcha-challenger model weights, YOLO detectors and CLIP encoders — GPL-3.0
  works downloaded locally at install time and never redistributed by this
  repository.

SearXNG is not distributed with this project. GreySearch queries independent
SearXNG instances over the network.

## License

Apache License 2.0. See [LICENSE](LICENSE).
