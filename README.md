<div align="center">
  <img src="doc/images/greysearch-logo.png" width="400" alt="GreySearch logo">
  <br>
  <strong>GreySearch</strong>
  <br>
  A local-first web research tool for autonomous, private research passes.
  <br><br>
  GreySearch executes automated research pipelines entirely on your local machine—no accounts, no servers, no telemetry. It queries search engines, renders pages in a real headless browser, filters irrelevant content, and synthesizes verified information into a citable Master Guide, static HTML site, or PDF.
  <br><br>
  <a href="#what-it-does">What it does</a> · 
  <a href="#how-it-works">How it works</a> · 
  <a href="#components">Components</a> · 
  <a href="#install">Install</a> · 
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

GreySearch performs end-to-end web research without leaving your machine:
- **Queries multiple search sources** in a deliberate fallback order (local SearXNG → public searx.space instances → direct keyless engines like DuckDuckGo, Wikipedia, GitHub).
- **Fetches and renders results** using a real headless Chromium (via patchright) to execute JavaScript and capture full page content—not just snippets.
- **Filters noise**: removes ads, tracking URLs, off-topic pages, and boilerplate before anything is processed.
- **Synthesizes with citations**: organizes extracted information into a coherent narrative where every claim is explicitly linked to its source URL.
- **Outputs in multiple formats**: a Markdown Master Guide, a themed static HTML site, or a PDF—all built solely from what the tool actually read.

Unlike traditional search tools, GreySearch:
- Requires **no API keys or user accounts**.
- Sends **no data to third parties** beyond the necessary search queries and page fetches.
- Provides **verifiable output**—you can trace every fact back to the original page.
- Operates **entirely offline** after installation (except for live network requests during a research pass).

## How it works

GreySearch follows a strict, transparent pipeline:

### 1. Search Lane Querying
The tool checks sources sequentially until it gathers sufficient results:
- **Local SearXNG** (optional, enabled via `GREYSEARCH_LOCAL_SEARX=1`): uses your self-hosted instance if available.
- **Public searx.space instances**: queries a harvested roster of community-run metasearch engines.
- **Direct keyless engines**: falls back to DuckDuckGo HTML, Wikipedia API, and GitHub API—all requiring no keys or accounts.
Each lane is independent; a blocked or slow lane is skipped and reported—never faked or simulated.

### 2. Content Fetching
For each result URL:
- Launches an isolated headless Chromium instance (via Playwright-compatible patchright).
- Waits for page load, executes JavaScript, and retrieves the fully rendered DOM.
- Respects rate limits and pauses between requests to avoid overwhelming hosts.
- Reports any failures (timeouts, blocks) rather than guessing content.

### 3. Content Processing
Raw HTML is transformed into clean text:
- Strips navigation, sidebars, footers, and ad blocks using heuristic filtering.
- Removes boilerplate text (e.g., cookie notices, copyright lines) common across sites.
- Discards pages with insufficient topical relevance to the original query.
- Preserves structural elements like headings, lists, and tables where semantically useful.

### 4. Synthesis and Citation
Filtered content is assembled into the final output:
- Groups related information by topic and source.
- Maps every sentence or claim to its exact source URL and retrieval timestamp.
- Resolves contradictions by noting conflicting sources in the run report.
- Generates a bibliography-style citation list for the Master Guide.

### 5. Output Generation
From the synthesized data, GreySearch can produce:
- **Master Guide** (Markdown): a single `.md` file with inline citations and a references section.
- **Themed Static HTML Site**: a navigable website with pagination, table of contents, and themeable styling (17 themes ship by default).
- **PDF Export**: a paginated, print-optimized document for offline reading.

## Components

GreySearch consists of five interconnected entry points—all implemented as compiled `.so` modules in this binary release:

| Entry point | Purpose |
|-------------|---------|
| `bin/greysearch-mcp.py` | Model Context Protocol server. Exposes `greysearch research` as a tool for local AI agents (e.g., Claude Desktop, Cursor). |
| `bin/greysearch-research.py` | One-shot CLI: `greysearch research "your question"` → produces a Master Guide in `guides/<date>/`. |
| `bin/greysearch-portal.py` | Local web UI: start with `greysearch portal`, then visit `http://127.0.0.1:8791` to manage and view research passes. |
| `bin/greysearch-build-site.py` | Converts a Master Guide into a themed static site (`sites/`) or PDF (`out/`). |
| `bin/greysearch-doctor.py` | System health checker: validates dependencies, browser slots, search lane responsiveness, and writable paths. |

### Why `.so` files, not `.py`?
- This repository ships a **binary distribution**: all functional modules are pre-compiled to `.so` (shared object) files using Cython for CPython 3.13 on x86-64 Linux.
- The corresponding `.py` source files **exist only in the private development tree** and are **intentionally excluded** from this release to:
  - Protect intellectual property (the core algorithms and lane logic).
  - Ship identical compiled artifacts to every installation (what you download
    is what was verified in this release's test matrix).
  - Avoid source-level drift—the modules are Cython-compiled, and their runtime
    dependencies are pinned in `requirements.txt`.
- To verify: run `find . -name "*.so"`—you’ll see the compiled modules. The `.py` files in `bin/` are thin launchers that load the `.so` implementations.

### Verification

This release was exercised in a clean environment (install plus all five entry points): the fresh install path, all five entry points starting (doctor diagnostics, CLI usage output, the portal HTTP server, the MCP JSON-RPC handshake, and site/PDF generation from a fixture guide), the interpreter rejecting a mismatched virtualenv, and the package contents themselves (no implementation source and no private paths shipped). What this matrix does **not** cover is every documented behavior under live conditions—the pipeline description above is documentation of how the tool works, not a claim that each stage was separately exercised against arbitrary live web content.

Two honest limitations worth stating plainly. The compiled modules are **not** auditable as source: what ships is what was built, and the verification of the build is the maintainer's, not a reproducible audit you can perform on the compiled artifacts. On the other hand, every release asset (tarball, `.deb`) has a SHA256 checksum published on the [GitHub release page](https://github.com/greyok00/greysearch/releases/tag/v1.0.0) so you can confirm that what you downloaded is exactly what was released—the assets cannot embed their own checksums, since the tarball contains this README.

## Install

GreySearch requires **CPython 3.13 on x86-64 Linux**. Other platforms or Python versions are not supported in this binary release.

```console
# Verify your Python version
$ python3 --version
Python 3.13.2

# Create and activate a virtual environment
$ python3 -m venv .venv
$ source .venv/bin/activate

# Install pinned dependencies (includes patchright Chromium driver)
$ pip install -r requirements.txt

# Download the Chromium build used by patchright (first run only)
$ python3 -m patchright install chromium

# Confirm system health
$ python3 bin/greysearch-doctor.py
```

After installation, run your first research pass:
```console
$ python3 bin/greysearch-research.py "impact of renewable energy adoption on grid stability 2023"
```
The Master Guide is written to `guides/<date>/` (override with `--write-guide-to`); build a themed static site from it afterwards with `greysearch build-site`.

**Note**: A Debian package (`greysearch_1.0.0_amd64.deb`) for x86-64 Linux with CPython 3.13 is published on the [release page](https://github.com/greyok00/greysearch/releases/tag/v1.0.0); it installs a `greysearch` command in `/usr/bin`. Alternatively, install from the release tarball or clone this repository—both contain the files the steps above use.

## Running a research pass

- **CLI**: `greysearch research "<your query>"` → Master Guide in `guides/<date>/`.
- **Portal**: Start `greysearch portal`, open `http://127.0.0.1:8791` in your browser, and use the web interface to initiate, monitor, and manage research.
- **MCP Server**: Configure your AI agent to call `greysearch mcp` as a tool for on-demand research.
- **Site Build**: After a research pass completes, run `greysearch build-site` to generate a static site from the Master Guide (PDF export is exposed through the compiled `pdf_export` module API, as in `greysearch research --pdf`).

## Output and your data

- **Nothing leaves your machine** except:
  - The search queries sent to public search engines (DuckDuckGo, Wikipedia, etc.).
  - The HTTP requests to fetch pages those engines return.
- **All artifacts stay local**: guide markdown, fetched assets (images, CSS), and build outputs live under your project directory.
- **No telemetry**: no analytics, crash reporting, usage beacons, or data collection of any kind.
- **You control retention**: delete or archive `out/`, `sites/`, and `guides/` whenever you wish.

## License

GreySearch is released under the **Apache License, Version 2.0**. See the `LICENSE` file for full terms.

> This license covers the compiled modules (`.so` files), documentation, and scripts in this repository. It does **not** grant rights to the private source tree used to create this binary release.

Third-party components used:
- patchright (browser automation): Apache-2.0
- Flask (web portal): BSD-3-Clause
- Markdown (rendering): BSD-3-Clause
- PyYAML (configuration): MIT