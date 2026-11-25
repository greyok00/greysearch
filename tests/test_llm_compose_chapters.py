"""Chaptered compose lane: report tier + open gate + live endpoint only;
each chapter is one bounded call (think:false, num_predict 2048); a quota
429 aborts the lane with an honest reason; unparsable/thin answers drop
just that chapter; no gate, no kill-switch escape, no endpoint -> zero
calls, zero chapters, rules guide unaffected. urlopen is stubbed."""
import io
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import llm_audit as la

_CHAPTER_BODY = ("The accepted sources describe mechanisms in concrete "
                 "terms. " * 30)


def _page(i, chars=1200):
    return {"url": f"https://src.example/p{i}", "title": f"Source {i}",
            "relevance": {"decision": "accept", "relevance_score": 90},
            "clean": {"title": f"Source {i}", "h1": f"Source {i}",
                      "body_text": f"Fact paragraph {i}. " * (chars // 18)}}


def _allow(monkeypatch, endpoint=True, model="glm-5.3-flash:cloud"):
    monkeypatch.setenv("GREYSEARCH_ALLOW_LLM_AUDIT", "1")
    monkeypatch.delenv("GREYSEARCH_NO_MODEL", raising=False)
    monkeypatch.setattr(la, "endpoint_ok", lambda *a, **k: endpoint)
    monkeypatch.setattr(la, "resolve_model", lambda: model)


class _FakeResp(io.IOBase):
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen_ok(req, timeout=None):
    """Any chapter whose excerpts carry a page body answers full-length."""
    body = json.loads(req.data.decode()).get("messages", [{}])[0] \
        .get("content", "")
    assert json.loads(req.data.decode())["options"]["num_predict"] == 2048
    assert json.loads(req.data.decode())["think"] is False
    return _FakeResp({"message": {"content": _CHAPTER_BODY
                                  if "Fact paragraph" in body
                                  else "too short"}})


def _urlopen_thin_after_first(req, timeout=None):
    """Chapter 1 (p0-p2) answers full; every later chapter answers thin.
    Keyed on chapter 1's own dose line ("Fact paragraph 0."), NOT on the
    "[p0]" citation example that lives in the prompt template itself."""
    body = json.loads(req.data.decode()).get("messages", [{}])[0] \
        .get("content", "")
    return _FakeResp({"message": {"content": _CHAPTER_BODY
                                  if "Fact paragraph 0" in body
                                  else "too short"}})


def _urlopen_quota(req, timeout=None):
    raise HTTPError(req.full_url, 429, "quota", {}, io.BytesIO(b""))


def _urlopen_down(req, timeout=None):
    raise URLError("connection refused")


def test_report_tier_composes_chapters_with_open_gate(monkeypatch):
    _allow(monkeypatch)
    monkeypatch.setattr(la, "_urlopen", _urlopen_ok)
    chapters, reason = la.compose_chapters(
        "compose verification question", [_page(i) for i in range(4)],
        tier="report")
    assert len(chapters) == 2
    assert chapters[0]["pages"] == ["https://src.example/p0",
                                    "https://src.example/p1",
                                    "https://src.example/p2"]
    assert len(chapters[0]["text"]) >= 200
    assert "ok" in reason


def test_quota_429_aborts_lane_with_honest_reason(monkeypatch):
    _allow(monkeypatch)
    monkeypatch.setattr(la, "_urlopen", _urlopen_quota)
    chapters, reason = la.compose_chapters(
        "compose verification question", [_page(i) for i in range(6)],
        tier="report")
    assert chapters == []
    assert "429" in reason


def test_thin_chapter_is_dropped_not_fatal(monkeypatch):
    _allow(monkeypatch)
    monkeypatch.setattr(la, "_urlopen", _urlopen_thin_after_first)
    chapters, reason = la.compose_chapters(
        "compose verification question", [_page(i) for i in range(6)],
        tier="report")
    assert len(chapters) == 1  # chapter 1 (p0-p2) ok; chapter 2 drops
    assert "dropped" in reason or "thin" in reason


def test_no_gate_no_model_no_calls(monkeypatch):
    monkeypatch.delenv("GREYSEARCH_ALLOW_LLM_AUDIT", raising=False)
    def _no_call(*a, **k):
        pytest.fail("model called with the gate closed")
    monkeypatch.setattr(la, "_urlopen", _no_call)
    chapters, reason = la.compose_chapters(
        "q", [_page(0)], tier="report")
    assert chapters == []
    monkeypatch.setenv("GREYSEARCH_NO_MODEL", "1")
    chapters, reason = la.compose_chapters(
        "q", [_page(0)], tier="report")
    assert chapters == []


def test_non_report_tier_never_calls(monkeypatch):
    _allow(monkeypatch)
    def _no_call(*a, **k):
        pytest.fail("model called outside report tier")
    monkeypatch.setattr(la, "_urlopen", _no_call)
    chapters, reason = la.compose_chapters("q", [_page(0)], tier="deep")
    assert chapters == []


def test_chapters_render_under_long_form_findings_section():
    """The chaptered markdown renders right after the executive brief, with
    the model-composed label and the heading the hook wrote."""
    import greysearch_publisher as gp
    res = {"question": "q",
           "pages_fetched": [{"url": "https://a.example/x",
                              "title": "X",
                              "clean": {"h1": "X", "h2s": [],
                                        "body_text": "Long body. " * 40},
                              "relevance": {"decision": "accept"}}],
           "search_pool": []}
    chapters_md = ("## Long-form findings (model-composed)\n\n"
                   "Model-written summary of the accepted sources; "
                   "verify against the full texts below.\n\n"
                   "### Findings part 1\n\nChapter body text.")
    md = gp.publish_from_greysearch("T", None, res, chapters_md=chapters_md,
                                    question="q")
    assert "## Long-form findings (model-composed)" in md
    assert md.index("Long-form findings") < md.index("## 1. Deep-Dive")
    assert "### Findings part 1" in md
    assert "Chapter body text." in md
    md_none = gp.publish_from_greysearch("T", None,
                                         {"question": "q",
                                          "pages_fetched": [],
                                          "search_pool": []},
                                         question="q")
    assert "## Long-form findings" not in md_none
