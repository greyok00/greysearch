#!/usr/bin/env python3
"""Doc-claim guard (2026-11-05 release pass): every capability claim stays
true everywhere it appears. The one honest positioning: greysearch is an
internet-connected research pipeline - research passes fetch live search
results and pages over the network, and installation itself downloads the
Chromium build and (optionally) the captcha model weights. What is never
claimed anywhere: offline operation, or a bare "No API keys" where the
truth is "no REQUIRED API keys" (keys.py reads only optional enhancer
entries from ~/.greysearch/.env, none functionally required)."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every user-visible claim surface: the docs a reader actually sees plus
# the module docstrings and echoes that speak in the product's voice.
_SURFACES = [
    "README.md",
    "NOTICE",
    "pyproject.toml",
    "install.sh",
    "searxng_integrated.py",
    "patchright_scraper.py",
    "keys.py",
    "greysearch_mcp.py",
    "doctor.py",
]

# Contradiction phrases: any hit means a surface is claiming the product
# runs offline (it cannot - research passes are live network fetches).
_FORBIDDEN = [
    "entirely offline",
    "fully offline",
    "works offline after",
    "offline after installation",
    "operates offline",
    "no internet access",
]

# A bare "No API keys" sentence - without the "required" qualifier it
# understates the optional enhancer entries keys.py can read.
_BARE_NO_KEYS = "no api keys."


def test_no_contradictory_claim_on_any_surface():
    hits = []
    for rel in _SURFACES:
        text = (REPO_ROOT / rel).read_text(errors="ignore").lower()
        for phrase in _FORBIDDEN:
            if phrase in text:
                hits.append(f"{rel}: {phrase!r}")
        if _BARE_NO_KEYS in text:
            hits.append(f"{rel}: bare 'No API keys' (must say 'required')")
    assert not hits, f"contradictory capability claims: {hits}"


def test_readme_positions_the_tool_as_internet_connected():
    text = (REPO_ROOT / "README.md").read_text(errors="ignore").lower()
    assert "internet" in text, "README must state the tool is \
internet-connected by design"
    assert "network" in text, "README must say research passes use \
the network"
    # the honest narrow truth stands too: no key is required anywhere
    assert "required api key" in text, \
        "README must carry the 'no required API keys' wording"
