"""Transport survival: a TargetClosedError-shaped scrape failure drops the
stale connection and retries the page inline once (a recovery, never a
wedge); pages that died on the shared wall get ONE second-wave retry when
wall remains and accepted < page_cap; outcomes carry honest retry/refill
reasons. research_scrape is stubbed; nothing real connects."""
import sys
from pathlib import Path
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_integration as ri
import relevance as relevance_mod

from test_full_capture_tiers import _stub_deep_bridge

_TARGET_CLOSED = ("scrape failed: TargetClosedError: Target page, "
                  "context or browser has been closed")


def _ok_fetch_bridge(monkeypatch, n_results=4):
    """Judge ON and unconditional-accept: these tests exercise transport,
    not the relevance gate, so every judged page accepts deterministically."""
    bridge = _stub_deep_bridge(monkeypatch, n_results=n_results)
    monkeypatch.setattr(relevance_mod, "judge_available", lambda: True)

    def fake_judge(question, pages):
        return [{"decision": "accept", "relevance_score": 90,
                 "reason": "test stub judge: on-topic",
                 "allowed_content": [], "risk_flags": []}
                for _ in pages]

    monkeypatch.setattr(relevance_mod, "judge_pages", fake_judge)
    return bridge


def _ok_result(url):
    return {"ok": True, "url": url, "final_url": url,
            "title": f"Recovered page {url.rsplit('-', 1)[-1]}",
            "text": "Transport recovery body text. " * 80,
            "raw_text": "x", "html": "", "html_capped": False,
            "links": [], "images": [], "videos": [], "audios": [],
            "pdfs": [], "canvases": [], "iframes": [], "meta": {},
            "status_code": 200, "tables": [], "headings": [],
            "code_blocks": [], "injection_flags": [],
            "injection_scanned": True, "elapsed_s": 0.1,
            "scrape_transport": "scrape direct (no proxy)"}


def _wall_result(url):
    return {"ok": False, "url": url, "error": "scrape wall (100s) exceeded"}


def _scripted_scrape(script):
    """Stub bridge.research_scrape with a script: dict = that exact
    result, the sentinel OK = a successful scrape of that url. Once the
    script empties every further call fails on the shared wall."""
    calls = []

    def fake_scrape(url, *args, **kwargs):
        calls.append(url)
        item = script.pop(0) if script else _wall_result(url)
        return _ok_result(url) if item is OK else item

    return fake_scrape, calls


OK = object()


def test_transport_classifier_shapes():
    assert ri._is_transport_reject(
        {"decision": "reject", "reason": "scrape wall (100s) exceeded"})
    assert ri._is_transport_reject(
        {"decision": "reject",
         "reason": "scrape worker error: playwright died"})
    assert ri._is_transport_reject(
        {"decision": "reject", "reason": "scrape failed: "
         "TargetClosedError: Target page, context or browser has been "
         "closed"})
    assert not ri._is_transport_reject(
        {"decision": "reject",
         "reason": "low topic-token coverage (17%; floor 21%)"})
    assert not ri._is_transport_reject(
        {"decision": "deprioritized", "reason": "page cap 16 reached"})


def test_target_closed_error_recovers_inline(monkeypatch):
    """First scrape dies TargetClosedError -> connection drop + ONE inline
    retry succeeds -> the page is accepted, browser_recoveries == 1, the
    wedge counter was never raised, and no TargetClosedError skip line
    survives."""
    bridge = _ok_fetch_bridge(monkeypatch)
    fake_scrape, calls = _scripted_scrape(
        [{"ok": False, "error": _TARGET_CLOSED}, OK, OK])
    monkeypatch.setattr(bridge, "research_scrape", fake_scrape)
    res = bridge.research_deep(
        "transport recovery body verification", max_pages=3,
        max_total_seconds=60, enable_semantic_judge=True,
        search_proxy="off")
    assert res["browser_recoveries"] == 1
    assert res["run_report"]["accepted_count"] >= 1
    assert bridge._scrape_wedges == 0
    assert not [s for s in res["skipped_pages"]
                if "TargetClosedError" in str(s.get("reason") or "")]


def test_target_closed_error_retry_fail_still_skips(monkeypatch):
    """Recovery attempts once per page; a second failure records the skip
    with the honest recovery note instead of looping."""
    bridge = _ok_fetch_bridge(monkeypatch)
    fake_scrape, calls = _scripted_scrape(
        [{"ok": False, "error": _TARGET_CLOSED},
         {"ok": False, "error": _TARGET_CLOSED}])
    monkeypatch.setattr(bridge, "research_scrape", fake_scrape)
    res = bridge.research_deep(
        "transport recovery body verification", max_pages=1,
        max_total_seconds=60, enable_semantic_judge=True,
        search_proxy="off")
    assert res["run_report"]["accepted_count"] == 0
    assert calls.count("https://example.one/lead-0") >= 2, \
        "the recovery must re-attempt the page at least once"
    assert any("recovery" in str(s.get("reason") or "")
               for s in res["skipped_pages"])


def test_wall_reject_gets_second_wave(monkeypatch):
    """Three pages die on the shared wall, one scrapes clean; the retry
    wave fills accepted back toward page_cap from the transport rejects
    and the survival is recorded as a resumed retry line."""
    bridge = _ok_fetch_bridge(monkeypatch)
    fake_scrape, calls = _scripted_scrape(
        [_wall_result("https://example.one/lead-0"),
         _wall_result("https://example.one/lead-1"),
         _wall_result("https://example.one/lead-2"),
         OK, OK])
    monkeypatch.setattr(bridge, "research_scrape", fake_scrape)
    res = bridge.research_deep(
        "transport recovery body verification", max_pages=3,
        max_total_seconds=60, enable_semantic_judge=True,
        search_proxy="off")
    assert res["run_report"]["accepted_count"] >= 1
    assert calls.count("https://example.one/lead-0") >= 2
    recovered = [s for s in res["skipped_pages"]
                 if "retry" in str(s.get("reason") or "").lower()
                 and s.get("decision") == "resumed"]
    assert recovered, "the second-wave survival must be recorded"
