"""Full-capture tier budgets + ranked search pool retention: the report
tier scrapes 16 pages over 900 s, the MCP hard cap rises to 40, and the
deep result carries the ranked candidate pool (url/title/snippet/domain/
tier/engine) so the guide can render leads that were never scraped.
All lanes stubbed; nothing real connects."""
import sys
from pathlib import Path
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import greysearch_mcp as gm
import research_integration as ri
import relevance as relevance_mod


def _stub_deep_bridge(monkeypatch, ladder=None, n_results=4):
    """Same lane-stubbing shape test_ladder_persistence uses: fake searxng
    manager, fake scrape fetch, pace 0, deterministic judge off."""
    bridge = ri.get_bridge()
    ladder = ladder if ladder is not None else [
        "proxy mode off: direct",
        "local: 4 results (full engine set)",
    ]

    def fake_search(query=None, **kwargs):
        rows = [{"url": f"https://example.one/lead-{i}",
                 "title": f"Ranked lead {i}",
                 "content": "full capture lead snippet text",
                 "engine": "testsuite"}
                for i in range(n_results)]
        return {"ok": True, "results": rows, "ladder": list(ladder),
                "engines_tried": ["testsuite"], "search_proxy": "off",
                "last_error": None}

    monkeypatch.setattr(
        bridge, "searxng_manager",
        types.SimpleNamespace(search=fake_search,
                              get_working_engines=lambda: []))

    def fake_fetch(url):
        body = ("Full capture verification body. " * 60)
        return {"ok": True, "url": url, "final_url": url,
                "title": f"Captured page {url.rsplit('-', 1)[-1]}",
                "text": body, "raw_text": body, "html": "",
                "html_capped": False,
                "links": [], "images": [], "videos": [], "audios": [],
                "pdfs": [], "canvases": [], "iframes": [], "meta": {},
                "status_code": 200, "tables": [],
                "headings": [{"level": "H1", "text": "Captured page"}],
                "code_blocks": [], "injection_flags": [],
                "injection_scanned": True, "elapsed_s": 0.1,
                "scrape_transport": "scrape direct (no proxy)"}

    monkeypatch.setattr(bridge.scraper, "fetch_url", fake_fetch)
    monkeypatch.setattr(ri, "_pace", lambda key: 0.0)
    monkeypatch.setattr(relevance_mod, "judge_available", lambda: False)
    return bridge


def test_report_tier_is_16_pages_900s():
    assert gm._TIER_CAPS["report"] == (16, 900.0)
    assert gm._TIER_CAPS["brief"] == (3, 240.0)
    assert gm._TIER_CAPS["standard"] == (4, 300.0)
    assert gm._TIER_CAPS["deep"] == (6, 420.0)


def test_max_results_hard_cap_is_40():
    assert gm.MAX_RESULTS_HARD_CAP == 40


def test_deep_result_carries_ranked_search_pool(monkeypatch):
    bridge = _stub_deep_bridge(monkeypatch, n_results=7)
    res = bridge.research_deep(
        "full capture report verification", max_pages=2,
        max_total_seconds=40, enable_semantic_judge=False,
        search_proxy="off")
    pool = res.get("search_pool")
    assert isinstance(pool, list) and len(pool) >= 5
    for entry in pool:
        assert set(("url", "title", "snippet", "domain", "tier", "engine")) \
            <= set(entry)
        assert entry["url"].startswith("https://example.one/lead-")
    # ranked order preserved: the first pool entry is lead 0
    assert pool[0]["url"].endswith("lead-0")
