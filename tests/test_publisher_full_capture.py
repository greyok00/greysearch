"""Keep-everything guide: the ranked lead pool renders as an appendix
(even for zero-accepted runs), discarded-source bullets carry the pool's
title/snippet, accepted sections gain the headings outline and top
passages, and report-tier prose caps at 80k instead of 40k. No model, no
network; publisher only."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import greysearch_publisher as gp

_BIG = ("Verified longform paragraph about the research question. " * 1000)


def _fake_result():
    return {
        "question": "image comparison microtask sites",
        "pages_fetched": [{
            "url": "https://alpha.example/compare-guide",
            "title": "Alpha compare guide",
            "clean": {"title": "Alpha compare guide",
                      "h1": "Alpha compare guide",
                      "h2s": ["Task formats", "Pay rates"],
                      "body_text": _BIG},
            "relevance": {"decision": "accept", "relevance_score": 88},
            "images": [], "links": [{"href": "https://alpha.example/docs",
                                     "text": "docs"}],
            "tables": [], "code_blocks": [], "videos": [], "audios": [],
            "pdfs": [], "canvases": [], "iframes": [],
        }],
        "rejected_pages": [{"url": "https://beta.example/rejected",
                            "decision": "reject",
                            "reason": "low topic-token coverage (17%)"}],
        "search_pool": [
            {"url": "https://alpha.example/compare-guide",
             "title": "Alpha compare guide", "snippet": "how to compare",
             "domain": "alpha.example", "tier": "unlisted",
             "engine": "ddg"},
            {"url": "https://beta.example/rejected",
             "title": "Beta page", "snippet": "beta snippet text",
             "domain": "beta.example", "tier": "unlisted",
             "engine": "mojeek"},
            {"url": "https://gamma.example/lead",
             "title": "Gamma lead", "snippet": "gamma lead text",
             "domain": "gamma.example", "tier": "unlisted",
             "engine": "bing-rss"},
        ],
    }


def test_search_index_lists_all_leads_and_marks_accepted():
    md = gp.publish_from_greysearch("T", None, _fake_result(),
                                    question="image comparison microtask sites")
    sidx = md.index("## Search index — all ranked leads")
    tail = md[sidx:]
    assert "https://beta.example/rejected" in tail
    assert "https://gamma.example/lead" in tail
    assert tail.index("beta.example") < tail.index("gamma.example"), \
        "pool order must be preserved"
    assert any("alpha.example/compare-guide" in ln and "✅" in ln
               for ln in tail.splitlines()), \
        "the accepted lead must carry the accepted mark"
    assert "beta snippet text" in tail


def test_rejection_bullet_carries_pool_title_and_snippet():
    md = gp.publish_from_greysearch("T", None, _fake_result(),
                                    question="image comparison microtask sites")
    lim = md.index("## Research limitations")
    block = md[lim:md.index("## 5. Primary Sources")]
    assert "low topic-token coverage" in block
    assert "Beta page" in block and "beta snippet text" in block


def test_accepted_source_gains_outline_and_passages():
    md = gp.publish_from_greysearch("T", None, _fake_result(),
                                    question="image comparison microtask sites")
    assert "**Outline:**" in md
    assert "Task formats" in md and "Pay rates" in md
    assert "**Key passages:**" in md


def test_report_tier_prose_cap_80k_and_honest_truncation():
    res = _fake_result()
    res["pages_fetched"][0]["clean"]["body_text"] = _BIG * 2
    md = gp.publish_from_greysearch("T", None, res, tier="report",
                                    question="image comparison microtask sites")
    assert re.search(r"first (79|80),\d{3}", md), \
        f"report-tier note must cap near 80,000, got: " + (
            md[md.index("Truncated"):md.index("Truncated") + 120]
            if "Truncated" in md else "NO NOTE")[:120]


def test_standard_tier_keeps_40k_cap():
    res = _fake_result()
    res["pages_fetched"][0]["clean"]["body_text"] = _BIG * 2
    md = gp.publish_from_greysearch("T", None, res,
                                    question="image comparison microtask sites")
    assert re.search(r"first (39|40),\d{3}", md)


def test_zero_accepted_run_still_renders_lead_index():
    res = _fake_result()
    res["pages_fetched"] = []
    md = gp.publish_from_greysearch("T", None, res,
                                    question="image comparison microtask sites")
    assert "## Search index — all ranked leads" in md
    assert "https://beta.example/rejected" in md
