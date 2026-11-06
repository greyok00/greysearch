<div align="center">
  <img src="doc/images/greysearch-logo.png" width="400" alt="GreySearch logo">
  <br>
  <strong>GreySearch</strong>
  <br>
  A local-first web research tool for autonomous, private research passes.
  <br><br>
  GreySearch runs its research pipelines on your local machine, with no accounts, no servers, and no telemetry. It queries search engines over the live internet, renders pages in a real headless browser, filters irrelevant content, and synthesizes verified information into a citable Master Guide, static HTML site, or PDF.
  <br><br>
  <a href="#what-it-does">What it does</a> · 
  <a href="#how-it-works">How it works</a> · 
  <a href="#architecture">Architecture</a> · 
  <a href="#entry-points">Entry points</a> · 
  <a href="#configuration">Configuration</a> · 
  <a href="#install">Install</a> · 
  <a href="#credits">Credits</a> · 
  <a href="#license">License</a>
</div>
<br>

```text
greysearch doctor
  [OK ] search_lanes   direct lanes answered (ddg-html, wikipedia-api)
  [OK ] browser_slots  launched on demand, isolated profile
  [OK ] portal         up on 127.0.0.1:8791
  [OK ] kit_source     17 themes found
 verdict: ALL HEALTHY
```

## What it does

GreySearch performs end-to-end web research from your own machine:
the pipeline runs locally, against the live web.
- **Queries multiple search sources** in a deliberate fallback order (local SearXNG → public searx.space instances → direct keyless engines like DuckDuckGo, Wikipedia, GitHub).
- **Fetches and renders results** using a real headless Chromium (via patchright) to execute JavaScript and capture full page content, not just snippets.
- **Filters noise**: removes ads, tracking URLs, off-topic pages, and boilerplate before anything is processed.
- **Synthesizes with citations**: organizes extracted information into a coherent narrative where every claim is explicitly linked to its source URL.
- **Outputs in multiple formats**: a Markdown Master Guide, a themed static HTML site, or a PDF, all built solely from what the tool actually read.

Unlike traditional search tools, GreySearch:
- Requires **no accounts and no required API keys**: search, page fetching, and captcha solving all run on keyless lanes. The only key file the tool can read is an optional `~/.greysearch/.env`, and nothing in it is needed for any run.
- Sends **no data to third parties** beyond the necessary search queries and page fetches.
- Provides **verifiable output**: you can trace every fact back to the original page.
- Is **internet-connected by design**: every research pass needs your network (search queries, page fetches, and installation downloads like Chromium and the optional captcha model weights). What never happens is a call home beyond those fetches - no telemetry, no accounts, no servers of ours.

## How it works

GreySearch follows a strict, transparent pipeline:

### 1. Search lane querying
The tool checks sources sequentially until it gathers sufficient results:
- **Local SearXNG** (always attempted first): uses your self-hosted instance on `127.0.0.1:8888`; its results are kept even when some engines are down, and thin or off-topic rows are flagged rather than discarded.
- **Public searx.space instances**: queries a harvested roster of community-run metasearch engines, with real browser sessions that wait out bot checks (30 to 60 seconds per page) and captcha waits.
- **Direct keyless engines**: falls back to DuckDuckGo HTML, Wikipedia API, and GitHub API, all requiring no keys or accounts.

A relevance floor decides when the ladder stops: by default it wants 20 rows that actually address your question (`GREYSEARCH_RELEVANT_TARGET`). While the count is under the floor, the upper rungs keep going and the time budget extends; when the floor stays unmet, the run record says so and every lane reports why it ran, failed, or was skipped. There is no "good enough, abort" bailout: the ladder cycles rungs and keeps hunting until either the floor is met or the budget is exhausted.

### 2. Content fetching
For each result URL:
- Launches an isolated headless Chromium instance (via the Playwright-compatible patchright API).
- Waits for page load, executes JavaScript, and retrieves the fully rendered DOM.
- Respects rate limits and pauses between requests to avoid overwhelming hosts.
- Reports any failures (timeouts, blocks) rather than guessing content.
- **Solves captcha checks locally**: when a bot-check page presents a challenge, the lane detects the frame and attempts to solve it with the model ladder in `captcha_solve.py` and `captcha_model.py`. Zero third-party captcha services, zero captcha API keys.
  - Image grids (hCaptcha, reCAPTCHA): the resident-model rung answers, then per-prompt binary tile classifiers from hcaptcha-challenger's modelhub release (fetched at install, decompiled and wired fresh), then a CLIP pair for semantic multi-select prompts.
  - Coordinate challenges ("click the object"): routed to vendored YOLO detectors, prompts matched through the upstream label table (including `not an animal` / `head of the ...`-style phrasing), boxes filtered by class, clicked all-or-nothing.
  - Arkose/FunCaptcha match rounds and legacy text captchas run through the resident-model rung (picks or transcription are typed into the page).
  - Honest limits: Arkose rotation puzzles and reCAPTCHA v3 are detected and recorded (rotation click semantics are unverifiable; v3 is a score widget with no challenge), and every other miss is recorded as a miss, never a fabricated solve. The full per-family coverage map is `doc/captcha-bench-2026-11-05.md`.
  - `GREYSEARCH_CAPTCHA_SOLVER=off` skips solving and records the skip in the run record. Where weights are absent or fail to load, the model rung deactivates itself and the record says the lane ran model-only.
- **No captcha browser extension ships or loads with the lane**: greysearch never installs, seeds, loads,
  or wires a captcha-solving extension into its own browser profiles. A captcha extension may exist on
  the operating machine for its owner's own use in other browsers; the lane neither knows about nor
  needs it, and its solver needs neither a captcha service nor any API key.

### 3. Content processing
Raw HTML is transformed into clean text:
- Strips navigation, sidebars, footers, and ad blocks using heuristic filtering.
- Removes boilerplate text (cookie notices, copyright lines, promo rails) common across sites.
- Discards pages with insufficient topical relevance to the original query.
- Captures page body paragraphs uncapped so nothing readable is dropped, and deduplicates repeated captures of the same query.
- Preserves structural elements like headings, lists, and tables where semantically useful.

### 4. Synthesis and citation
Filtered content is assembled into the final output:
- Groups related information by topic and source.
- Maps every sentence or claim to its exact source URL and retrieval timestamp.
- Resolves contradictions by noting conflicting sources in the run report.
- Generates a bibliography-style citation list for the Master Guide.

### 5. Output generation
From the synthesized data, GreySearch can produce:
- **Master Guide** (Markdown): a single `.md` file with inline citations and a references section.
- **Themed static HTML site**: a navigable website with pagination, table of contents, and themeable styling (17 themes ship by default).
- **PDF export**: a paginated, print-optimized document for offline reading.

## Architecture

The repository is plain, readable Python 3.13 source.

| Path | Contents |
|------|----------|
| (repo root) | The 30 core modules, one concern each: search lanes (`searxng_integrated`, `searx_fallback`, `direct_sources`), the browser layer (`browser_session`, `patchright_scraper`, `patchright_headless`, `browser_ports`), page cleanup and relevance gating (`page_clean`, `relevance`, `pipeline_sites`), synthesis and publishing (`research_integration`, `greysearch_publisher`, `site_render`, `site_guardrails`, `guide_scrub`), the portal and its run ledger (`portal_server`, `portal_ledger`), the MCP server (`greysearch_mcp`), plus supporting modules for photos, PDF export, and configuration (`photos`, `pdf_export`, `llm_audit`, `llm/`, `keys`, `tuning`, `research`, `doctor`). The optional post-research model audit (`llm_audit` plus the `llm/` helper package) never runs unless you configure a local model endpoint yourself, and the research phase never calls a model in any configuration. |
| `bin/` | Five thin launcher scripts, one per entry point. |
| `tests/` | The regression suite: search pacing and captcha waits, proxy lanes, relevance floors, slot claims, portal behavior, and MCP contracts. |
| `conf/` | Optional user configuration (search proxy env file is read from `~/.config/greysearch/`, not from the repo). |
| `assets/` | Portal UI, the site theme framework, and the source-kit build tooling used by site themes. |
| `research/` | Tunable data: the relevant-result floor, gating snapshots, search lane lists, and the searx.space instance roster. |

There is no build step and no compiled artifact in the tree: clone it and run the source with CPython 3.13.

## Entry points

| Command | Script | Purpose |
|---------|--------|---------|
| `greysearch-portal` | `bin/greysearch-portal.py` | Local web UI on `127.0.0.1:8791`: start, monitor, and manage research passes; browse run records and built sites. |
| `greysearch-research` | `bin/greysearch-research.py` | One-shot CLI: `greysearch-research "your question"` produces a Master Guide in `guides/<date>/`. |
| `greysearch-mcp` | `bin/greysearch-mcp.py` | Model Context Protocol server: exposes `greysearch research` as a tool for any MCP client. |
| `greysearch-build-site` | `bin/greysearch-build-site.py` | Converts a Master Guide into a themed static site (`sites/`) or PDF (`out/`). |
| `greysearch-doctor` | `bin/greysearch-doctor.py` | Health checker: validates dependencies, browser slots, search lane responsiveness, and writable paths. |

Each launcher adds the repo root to `sys.path` and delegates to the matching module's entry function (`research.main`, `doctor.main`, `greysearch_mcp.main`, `greysearch_site_builder.render`, `portal_server.main`). The portal launcher additionally registers the run-ledger blueprint from `portal_ledger` on the Flask app before serving.

## Configuration

- **`research/*.json`**: the shipped tunables. `rules/gating.json` documents the content-gating constants in one place, `tuning.json` and the lane lists in `browser-ports.json` and `searx-fallback-roster.json` drive the search ladder.
- **`~/.config/greysearch/proxy.env`** (optional): user-provided search proxy credentials, created by copying `etc/proxy.env.example` and filling in your own endpoint values. Only `HTTP_PROXY` / `HTTPS_PROXY` are read, only into memory, and the file is never committed. Search requests ride the proxy only when the mode is `on`; page scrapes never use it in any mode, and the `GREYSEARCH_SEARCH_PROXY` environment variable, when set, wins over the requested mode.
- **Environment variables** such as `GREYSEARCH_RELEVANT_TARGET` (the relevant-result floor) and `GREYSEARCH_PORTAL_PORT` adjust behavior without code changes.

## Install

Requires **CPython 3.13 or newer** and **Linux** (the browser automation layer is a Playwright/Chromium stack tested on x86-64 Linux). Installation uses the network for the dependency download, the Chromium build, and the optional captcha model weights.

```console
$ git clone https://github.com/greyok00/greysearch.git
$ cd greysearch
$ python3 -m venv .venv
$ source .venv/bin/activate
$ pip install -r requirements.txt

# Download the Chromium build driven by patchright (first run only)
$ python3 -m patchright install chromium

# Optional but recommended: vendor the local captcha-solver weights,
# fetched and sha256-verified against models/CHECKSUMS. The install script
# vendors the hCaptcha grid classifier library by default (~36 MB);
# GREYSEARCH_CAPTCHA_VENDOR=all adds the YOLO detector and CLIP families
# (the detector family is on the order of GBs). Without the weights the
# model rungs deactivate themselves honestly and the resident-model rung
# alone still answers.
$ ./install.sh

# Confirm system health
$ python3 bin/greysearch-doctor.py
```

After installation, run your first research pass:

```console
$ python3 bin/greysearch-research.py "impact of renewable energy adoption on grid stability"
```

The Master Guide is written to `guides/<date>/` (override with `--write-guide-to`); build a themed static site from it afterwards with `bin/greysearch-build-site.py`.

Alternatively, `pip install .` puts the same five commands shown above on your `PATH` as `greysearch-portal`, `greysearch-research`, `greysearch-mcp`, `greysearch-build-site`, and `greysearch-doctor` (defined in `pyproject.toml`). When building the wheel locally, clear `build/` and any `*.egg-info` directories first so a stale build tree cannot re-inject previously purged files into the wheel.

## Verification

`tests/` holds the regression suite: `python3 -m pytest tests/`. Lint with `python3 -m ruff check .`. Continuous integration runs both, builds a wheel, checks that the wheel ships pure Python (no compiled artifacts), and installs it into a clean virtual environment.

The verification matrix covers the documented contracts in the test suite, not every behavior the tool exhibits against arbitrary live web content. The pipeline description above documents how the tool works; running it for yourself with `greysearch-doctor` is the quickest way to confirm a given environment is healthy.

## Output and your data

- **Nothing leaves your machine** except:
  - The search queries sent to public search engines (DuckDuckGo, Wikipedia, etc.).
  - The HTTP requests to fetch pages those engines return.
- **All artifacts stay local**: guide markdown, fetched assets (images, CSS), and build outputs live under your project directory.
- **No telemetry**: no analytics, crash reporting, usage beacons, or data collection of any kind.
- **You control retention**: delete or archive `out/`, `sites/`, `guides/`, and run records whenever you wish.

## Credits

Third-party components used:

- patchright (browser automation): Apache License 2.0
- Flask (web portal): BSD 3-Clause License
- Markdown (rendering): BSD 3-Clause License
- PyYAML (configuration): MIT License
- hcaptcha-challenger model weights, YOLO detectors, and CLIP encoders from the same project's model release (local captcha solving): GPL-3.0 works that install.sh downloads to your machine's disk and never redistributes; the repo carries checksum pins and disclosure only, no weights and no source from that project.

SearXNG is not distributed with this project. GreySearch queries SearXNG instances operated by third parties over the network; those instances and their operators are independent of this project.

## License

GreySearch is released under the **Apache License, Version 2.0**. See the `LICENSE` file for full terms.
