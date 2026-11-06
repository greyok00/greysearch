"""Ladder persistence tests: the per-run lane ladder and engine
attribution survive search -> deep result -> run report -> run JSON ->
health digest, the guide blurb names only lanes that actually ran, and the
searxng_integrated ladder bugs (skip_local trample, empty browser-lane
error, stale bing-rss label, invisible not-engaged lanes) are gone. All
network lanes are stubbed: assertions inspect records, not traffic.
Must-run posture: the searx.space JSON instance lane attempts on every search
regardless of local health; every skip or unreachable case keeps an honest
record and the local-only skip clause is gone."""
import json
import time
import types

import pytest

from urllib.error import URLError

import proxy_mode as pm
import searx_fallback as sf
import searxng_integrated as si
import greysearch_mcp as gm
import research_integration as ri
import relevance as relevance_mod
import greysearch_publisher as gp
from conftest import set_env_file


_LADDER_FULL_LOCAL = [
    "proxy mode off: direct",
    "local: 19 results (full engine set)",
    "searx.space: total after lane 23 (instances: testsuite.one)",
    "direct keyless: not engaged (enough results)",
]


def _fail_urlopen(req, timeout=None, **k):
    pytest.fail(f"a search query ran where none was allowed: "
                f"{getattr(req, 'full_url', '')}")


def _fake_ok_fetch(url):
    body = ("Rain garden sizing begins with the drainage area that feeds "
            "the bed, and the sizing rule ties the volume to the runoff. ")
    body += ("Rain garden sizing then checks the infiltration rate of the "
             "soil so the bed empties within a day. ")
    body += ("Finally, rain garden sizing places the overflow outlet high, "
             "because a garden sized past the runoff floods the path. ")
    return {"ok": True, "url": url, "final_url": url,
            "title": "Rain garden sizing",
            "text": body * 8, "raw_text": body * 8, "html": "",
            "html_capped": False, "links": [{"href": url + "#detail",
                                             "text": "rain garden sizing"}],
            "images": [], "videos": [], "audios": [], "pdfs": [],
            "canvases": [], "iframes": [], "meta": {}, "status_code": 200,
            "tables": [], "headings": [{"level": "H1",
                                        "text": "Rain garden sizing"}],
            "code_blocks": [], "injection_flags": [],
            "injection_scanned": True, "elapsed_s": 0.1,
            "scrape_transport": "scrape direct (no proxy)"}


class _FakeJsonResponse:
    def __init__(self, payload):
        self.status = 200
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _stub_fallback(browser_exc=RuntimeError("stubbed browser lane"),
                   direct_rows=None, browser_rows=None, holds=None):
    def factory():
        def no_browser(*args, **kwargs):
            if browser_rows is not None:
                return list(browser_rows)
            raise browser_exc
        rows = direct_rows if direct_rows is not None else []
        return types.SimpleNamespace(
            browser_search_public=no_browser,
            _direct_search=lambda *a, **k: list(rows),
            _warm_remote_host=lambda *a, **k: None,
            _filter_instance_junk=lambda q, r, *a, **k: list(r),
            _captcha_holds=lambda: list(holds or []))
    return factory


# ---- searxng_integrated: skip_local flip + empty browser error ------------

def test_skip_local_honored_with_alive_cache(monkeypatch, tmp_path):
    """Engine-cache edge: the engine cache used to flip local_alive back True on an
    explicit skip (and the flip never even ran the query it named); now the
    flag wins, exactly one skip ladder line appears, no local query runs,
    and the external lanes still proceed."""
    monkeypatch.setenv("GREYSEARCH_SKIP_LOCAL", "1")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    set_env_file(monkeypatch, tmp_path / "absent.env")
    cache_path = tmp_path / "engine-status.json"
    cache_path.write_text(json.dumps(
        {"at": time.time(), "engines": {"wikipedia": {"working": True}}}))
    monkeypatch.setattr(si, "_engine_cache_path", lambda: cache_path)
    monkeypatch.setattr(si, "_local_alive", lambda *a, **k: True)
    monkeypatch.setattr(si.urllib.request, "urlopen", _fail_urlopen)
    monkeypatch.setattr(si, "_searx_fallback", _stub_fallback())
    mgr = si.SearXNGEngineManager()
    monkeypatch.setattr(mgr, "_discover_public_instances",
                        lambda max_instances=6, mode=None: [])
    assert mgr.skip_local is True and mgr.searxng_urls == []
    res = mgr.search("rain garden sizing testsuite", search_proxy="off")
    local_lines = [l for l in res["ladder"] if l.startswith("local:")]
    assert local_lines == ["local: skipped (GREYSEARCH_SKIP_LOCAL)"]
    assert all("engine cache says alive" not in l for l in res["ladder"])
    assert res["engines_tried"] == []
    assert res["ok"] is False


def test_engine_cache_flip_still_covers_non_skip_down(monkeypatch, tmp_path):
    """The cache flip survives for a genuine probe-down case without the
    skip flag, so an explicitly skipped run cannot claim the cached
    engines while a real down probe still can (cached engines = the flip;
    the flip consults get_working_engines, as the down-lane test stubs)."""
    monkeypatch.delenv("GREYSEARCH_SKIP_LOCAL", raising=False)
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.setattr(si, "_local_alive", lambda *a, **k: False)
    monkeypatch.setattr(si.urllib.request, "urlopen", _fail_urlopen)
    monkeypatch.setattr(si, "_searx_fallback", _stub_fallback())
    mgr = si.SearXNGEngineManager()
    monkeypatch.setattr(mgr, "get_working_engines",
                        lambda: ["wikipedia", "github"])
    monkeypatch.setattr(mgr, "_discover_public_instances",
                        lambda max_instances=6, mode=None: [])
    res = mgr.search("rain garden sizing testsuite", search_proxy="off")
    assert any("engine cache says alive" in l for l in res["ladder"])


def test_browser_lane_error_line_names_the_exception(monkeypatch, tmp_path):
    """Error-label edge: an empty exception message used to leave a bare
    'searx.space browser failed/bound-out: '; the line now carries the
    exception class even with an empty message string."""
    monkeypatch.delenv("GREYSEARCH_SKIP_LOCAL", raising=False)
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.setattr(si, "_local_alive", lambda *a, **k: False)
    monkeypatch.setattr(si.urllib.request, "urlopen", _fail_urlopen)
    monkeypatch.setattr(si, "_searx_fallback",
                        _stub_fallback(browser_exc=RuntimeError("")))
    mgr = si.SearXNGEngineManager()
    monkeypatch.setattr(mgr, "get_working_engines", lambda: [])
    monkeypatch.setattr(mgr, "_discover_public_instances",
                        lambda max_instances=6, mode=None: [])
    res = mgr.search("rain garden sizing testsuite", search_proxy="off")
    err_lines = [l for l in res["ladder"]
                 if l.startswith("searx.space browser:")]
    assert len(err_lines) == 1, res["ladder"]
    assert err_lines[0].startswith(
        "searx.space browser: failed/bound-out ("), err_lines
    assert "RuntimeError" in err_lines[0], err_lines


# ---- searxng_integrated: not-engaged lanes + engines_tried + stale label --

def test_browser_lane_captcha_marks_flow_into_si_ladder(monkeypatch, tmp_path):
    """The browser rung's WAIT behavior is reported in the run's
    ladder. When the delivered attempt engaged the solver, exactly one
    mark precedes the produced-rows record; the marks are inert for
    attribution keys (the rung line stays the only producing shape). This
    machinery's local lane runs with zero working engines, so the flow key
    stays unset and attribution reads the ladder scan: the produced rung
    line names it and the preceding captcha mark never does."""
    monkeypatch.delenv("GREYSEARCH_SKIP_LOCAL", raising=False)
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.setattr(si, "_local_alive", lambda *a, **k: False)
    monkeypatch.setattr(si.urllib.request, "urlopen", _fail_urlopen)
    browser_rows = [{"url": f"https://html.ddg.test/{i}",
                     "title": f"row {i}",
                     "content": "rain garden sizing detail"}
                    for i in range(3)]
    monkeypatch.setattr(si, "_searx_fallback", _stub_fallback(
        browser_rows=browser_rows,
        holds=["captcha: solver waited 6s (solved)"]))
    mgr = si.SearXNGEngineManager()
    monkeypatch.setattr(mgr, "get_working_engines", lambda: [])
    monkeypatch.setattr(mgr, "_discover_public_instances",
                        lambda max_instances=6, mode=None: [])
    res = mgr.search("rain garden sizing testsuite", max_results=3,
                     search_proxy="off")
    cap_i = res["ladder"].index("captcha: solver waited 6s (solved)")
    browser_i = next(k for k, l in enumerate(res["ladder"])
                     if l.startswith("searx.space browser:"))
    assert cap_i < browser_i
    assert "searx.space browser: 3 results" in res["ladder"]
    assert res["engines_tried"] == []
    assert ri._engine_attribution(
        {"engines_tried": res["engines_tried"],
         "ladder": res["ladder"]}) == ["searx.space browser"]


def test_local_cover_records_attempted_rung_lines(monkeypatch, tmp_path):
    """Visibility under the must-run posture: a local lane that
    covers max_results no longer hides the searx.space rung behind a
    not-engaged line; the JSON instance lane is still attempted (no
    reachable instances here, so the honest no-reach record appears) and
    the produced rows still land in engines_tried."""
    monkeypatch.delenv("GREYSEARCH_SKIP_LOCAL", raising=False)
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    monkeypatch.setenv("GREYSEARCH_RELEVANT_TARGET", "4")
    set_env_file(monkeypatch, tmp_path / "absent.env")
    called_urls = []
    payload = {"results": [
        {"url": f"https://example.one/rain-garden-{i}",
         "title": f"Rain garden sizing example {i}",
         "content": "rain garden sizing walkthrough for a driveway",
         "engines": ["duckduckgo"]} for i in range(6)]}
    holder = {"resp": _FakeJsonResponse(payload)}

    def _serve(req, timeout=None, **k):
        called_urls.append(getattr(req, "full_url", ""))
        return holder["resp"]

    monkeypatch.setattr(si.urllib.request, "urlopen", _serve)
    monkeypatch.setattr(si, "_local_alive", lambda *a, **k: True)
    monkeypatch.setattr(si, "_searx_fallback", _stub_fallback())
    mgr = si.SearXNGEngineManager()
    monkeypatch.setattr(mgr, "working_engines", ["duckduckgo"])
    monkeypatch.setattr(mgr, "get_working_engines", lambda: ["duckduckgo"])
    monkeypatch.setattr(mgr, "_discover_public_instances",
                        lambda max_instances=6, mode=None: [])
    res = mgr.search("rain garden sizing testsuite", max_results=4,
                     search_proxy="off")
    assert "local: 6 results (full engine set)" in res["ladder"]
    assert "searx.space: no reachable instances discovered" in res["ladder"]
    assert "direct keyless: not engaged (enough results)" in res["ladder"]
    assert called_urls and all(
        u.startswith("http://127.0.0.1:8888/search") for u in called_urls)
    assert res["engines_tried"] == ["default+fullset"]
    assert res["engine_used"] == "default+fullset"
    assert res["ok"] is True


def _local_cover_payload():
    return {"results": [
        {"url": f"https://example.one/rain-garden-{i}",
         "title": f"Rain garden sizing example {i}",
         "content": "rain garden sizing walkthrough for a driveway",
         "engines": ["duckduckgo"]} for i in range(6)]}


def _mustrun_manager(monkeypatch, tmp_path, space_rows):
    """A local-healthy manager whose local lane covers 6 rows, with the
    JSON instance lane stubbed hermetically: space_rows is either a row
    payload (probe delivers), the string "refuse" (probe cannot connect) or
    the string "empty" (no instances reachable)."""
    monkeypatch.delenv("GREYSEARCH_SKIP_LOCAL", raising=False)
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    set_env_file(monkeypatch, tmp_path / "absent.env")
    called_urls = []
    probe_urls = []
    local_payload = _local_cover_payload()

    def _serve(req, timeout=None, **k):
        called_urls.append(getattr(req, "full_url", ""))
        return _FakeJsonResponse(local_payload)

    space_payload = {"results": [
        {"url": f"https://testsuite.space/rain-garden-{i}",
         "title": f"Rain garden sizing instance {i}",
         "content": "rain garden sizing walkthrough for a driveway"}
        for i in range(4)]}

    def _serve_space(url, timeout=None, mode=None, **k):
        probe_urls.append(url)
        return _FakeJsonResponse(space_payload)

    def _refuse(url, timeout=None, mode=None, **k):
        probe_urls.append(url)
        raise URLError("connection refused")

    def _engaged_factory():
        def no_browser(*args, **kwargs):
            raise RuntimeError("stubbed browser lane")
        return types.SimpleNamespace(
            browser_search_public=no_browser,
            _direct_search=lambda *a, **k: [],
            _warm_remote_host=lambda *a, **k: None,
            _filter_instance_junk=lambda q, rows, *a, **k: list(rows))

    monkeypatch.setattr(si.urllib.request, "urlopen", _serve)
    monkeypatch.setattr(pm, "open_search",
                        _refuse if space_rows == "refuse" else _serve_space)
    monkeypatch.setattr(si, "_local_alive", lambda *a, **k: True)
    monkeypatch.setattr(si, "_searx_fallback", _engaged_factory)
    mgr = si.SearXNGEngineManager()
    monkeypatch.setattr(mgr, "working_engines",
                        list(si.REQUIRED_GENERAL_ENGINES))
    monkeypatch.setattr(mgr, "get_working_engines",
                        lambda: list(si.REQUIRED_GENERAL_ENGINES))
    reachable = ["https://testsuite.space"]
    monkeypatch.setattr(mgr, "_discover_public_instances",
                        lambda max_instances=6, mode=None: list(reachable))
    return mgr, called_urls, probe_urls


def test_mustrun_space_lane_engages_over_local_cover(monkeypatch, tmp_path):
    """Must-run: a local lane that covers max_results does not keep the
    JSON instance lane out; when the probe delivers, the produced-row
    record rides the same add-shape family as the ladder tests (attribution reads
    searx.space as producing) and the rows merge into the pool tagged
    +searxspace, exactly one probe per search call."""
    mgr, called_urls, probe_urls = _mustrun_manager(
        monkeypatch, tmp_path, space_rows="deliver")
    monkeypatch.setenv("GREYSEARCH_RELEVANT_TARGET", "8")
    res = mgr.search("rain garden sizing testsuite", max_results=8,
                     search_proxy="off")
    assert "local: 6 results (full engine set)" in res["ladder"]
    assert ("searx.space: total after lane 10 "
            "(instances: https://testsuite.space)") in res["ladder"]
    assert "direct keyless: not engaged (enough results)" in res["ladder"]
    assert len(probe_urls) == 1, res["ladder"]
    assert (probe_urls[0].startswith("https://testsuite.space/search")
            and "format=json" in probe_urls[0]), probe_urls
    assert called_urls and all(
        u.startswith("http://127.0.0.1:8888/search") for u in called_urls)
    space_rows = [r for r in res["results"]
                  if (r.get("engine") or "").endswith("+searxspace")]
    assert len(space_rows) == 2, res["results"]
    assert res["total_results"] == 10
    assert ri._engine_attribution(
        {"engines_tried": [], "ladder": res["ladder"]},
        ["ddg news"]) == ["local", "searx.space"]


def test_mustrun_space_budget_skip_line(monkeypatch, tmp_path):
    """Must-run honesty: an exhausted time budget before the lane's
    turn is the only health-independent skip; the record names the budget
    (reusing the direct keyless mechanism, no new timer), the lane never
    even attempts, and the local-only skip clause is gone."""
    mgr, called_urls, probe_urls = _mustrun_manager(
        monkeypatch, tmp_path, space_rows="deliver")
    monkeypatch.setenv("GREYSEARCH_RELEVANT_TARGET", "4")

    def _no_attempt(*args, **kwargs):
        pytest.fail("searx.space lane attempted on an exhausted budget")

    monkeypatch.setattr(mgr, "_discover_public_instances", _no_attempt)
    res = mgr.search("rain garden sizing testsuite", max_results=12,
                     max_seconds=0.0, search_proxy="off")
    space_recs = [l for l in res["ladder"] if l.startswith("searx.space:")]
    assert space_recs == ["searx.space: not engaged (time budget reached)"]
    absent_clause = "local" + " merged enough"
    assert not any(absent_clause in l for l in res["ladder"]), res["ladder"]
    assert probe_urls == []
    assert ("direct keyless: not engaged (time budget reached)"
            in res["ladder"]), res["ladder"]


def test_mustrun_space_probe_failure_carries_class(monkeypatch, tmp_path):
    """Must-run honesty: a probe that cannot connect keeps the browser
    lane's precedent; the record carries the exception class, and a
    failed-probes record is never read as producing by the add-shape scan."""
    mgr, called_urls, probe_urls = _mustrun_manager(
        monkeypatch, tmp_path, space_rows="refuse")
    res = mgr.search("rain garden sizing testsuite", max_results=8,
                     search_proxy="off")
    space_recs = [l for l in res["ladder"] if l.startswith("searx.space:")]
    assert len(space_recs) == 1, res["ladder"]
    assert space_recs[0].startswith(
        "searx.space: tried 1 instance-probes, failed ("), space_recs
    assert "URLError" in space_recs[0], space_recs
    assert "refused" in space_recs[0], space_recs
    assert probe_urls and probe_urls[0].startswith(
        "https://testsuite.space/search"), probe_urls
    assert "format=json" in probe_urls[0], probe_urls
    assert ri._lane_keys_from_ladder(res["ladder"]) == ["local"]
    assert "local: 6 results (full engine set)" in res["ladder"]


def test_direct_lane_label_is_ddg_html_no_bing(monkeypatch, tmp_path):
    """The stale '(bing-rss, ddg-html)' label names only the lane that
    actually runs, and no output mentions bing-rss."""
    monkeypatch.delenv("GREYSEARCH_SKIP_LOCAL", raising=False)
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.setattr(si, "_local_alive", lambda *a, **k: False)
    monkeypatch.setattr(si.urllib.request, "urlopen", _fail_urlopen)
    direct_rows = [{"url": f"https://html.ddg.test/{i}", "title": f"row {i}",
                    "content": "rain garden sizing detail",
                    "source": "ddg-html"} for i in range(3)]
    monkeypatch.setattr(si, "_searx_fallback",
                        _stub_fallback(direct_rows=direct_rows))
    mgr = si.SearXNGEngineManager()
    monkeypatch.setattr(mgr, "get_working_engines", lambda: [])
    monkeypatch.setattr(mgr, "_discover_public_instances",
                        lambda max_instances=6, mode=None: [])
    res = mgr.search("rain garden sizing testsuite", search_proxy="off")
    assert "direct keyless: +3 (ddg-html)" in res["ladder"]
    assert not [l for l in res["ladder"]
                if l.startswith("direct keyless: 0")]
    assert "bing-rss" not in str(res["ladder"])


def test_direct_lane_label_names_the_added_sub_lanes(monkeypatch, tmp_path):
    """The +N record names every sub-lane whose rows actually stayed in the
    pool, in first-seen added-row order, and never names a sub-lane whose
    only row was already seen (rows carry their sub-lane key from
    _direct_search; the ddg-html, wikipedia-api and github-api api lanes
    share this rung with ddg-html)."""
    monkeypatch.delenv("GREYSEARCH_SKIP_LOCAL", raising=False)
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.setattr(si, "_local_alive", lambda *a, **k: False)
    monkeypatch.setattr(si.urllib.request, "urlopen", _fail_urlopen)
    direct_rows = [{"url": f"https://html.ddg.test/{i}", "title": f"row {i}",
                    "content": "rain garden sizing detail",
                    "source": "ddg-html"} for i in (0, 2)]
    direct_rows.append({"url": "https://en.wikipedia.test/wiki/0",
                        "title": "row 1", "content": "rain garden sizing detail",
                        "source": "wikipedia-api"})
    direct_rows.append({"url": "https://html.ddg.test/0",
                        "title": "row 0 duplicated under github-api",
                        "content": "rain garden sizing detail",
                        "source": "github-api"})
    monkeypatch.setattr(si, "_searx_fallback",
                        _stub_fallback(direct_rows=direct_rows))
    mgr = si.SearXNGEngineManager()
    monkeypatch.setattr(mgr, "get_working_engines", lambda: [])
    monkeypatch.setattr(mgr, "_discover_public_instances",
                        lambda max_instances=6, mode=None: [])
    res = mgr.search("rain garden sizing testsuite", search_proxy="off")
    assert "direct keyless: +3 (ddg-html, wikipedia-api)" in res["ladder"]
    assert not [l for l in res["ladder"] if "github-api" in l], res["ladder"]


# ---- research_deep: search_ladder + searches[i].engines -------------------

def _stub_bridge_for_deep(monkeypatch, ladder, engines_tried, n_results=4):
    bridge = ri.get_bridge()

    def fake_search(query=None, **kwargs):
        rows = [{"url": f"https://example.one/rain-garden-{i}",
                 "title": f"Rain garden sizing example {i}",
                 "content": "rain garden sizing walkthrough for a driveway",
                 "engine": "testsuite"}
                for i in range(n_results)]
        return {"ok": True, "results": rows, "ladder": list(ladder),
                "engines_tried": list(engines_tried),
                "search_proxy": "off", "last_error": None}

    monkeypatch.setattr(
        bridge, "searxng_manager",
        types.SimpleNamespace(search=fake_search,
                              get_working_engines=lambda: []))
    monkeypatch.setattr(bridge.scraper, "fetch_url", _fake_ok_fetch)
    monkeypatch.setattr(ri, "_pace", lambda key: 0.0)
    monkeypatch.setattr(relevance_mod, "judge_available", lambda: False)
    return bridge


def test_deep_carries_search_ladder_and_engines(monkeypatch):
    """The deep result carries the ladder and per-search attribution; a
    residual rule: a non-empty engines_tried keeps its lead AND the
    producing lanes the ladder records name are appended, so
    local: 19 results plus searx.space: total after lane 23 credit their
    keys behind the manager's own."""
    bridge = _stub_bridge_for_deep(
        monkeypatch, _LADDER_FULL_LOCAL, ["default+fullset"])
    res = bridge.research_deep(
        "rain garden sizing for a driveway", max_pages=2,
        max_total_seconds=40, enable_semantic_judge=False,
        search_proxy="off")
    assert res["search_ladder"], "deep result must carry the ladder"
    for rec in _LADDER_FULL_LOCAL:
        assert rec in res["search_ladder"]
    assert len(res["search_ladder"]) <= ri.SEARCH_LADDER_MAX
    lane = next(s for s in res["searches"] if s.get("category") == "general")
    assert lane["engines"] == ["default+fullset", "local", "searx.space"]
    assert isinstance(res["run_report"].get("search_ladder"), list)
    assert res["run_report"]["search_ladder"] == res["search_ladder"]


def test_deep_ladder_bounded_to_last_twenty(monkeypatch):
    ladder = [f"lane testsuite: record {i} of many" for i in range(30)]
    bridge = _stub_bridge_for_deep(monkeypatch, ladder, ["default+fullset"])
    res = bridge.research_deep(
        "rain garden sizing for a driveway", max_pages=1,
        max_total_seconds=40, enable_semantic_judge=False,
        search_proxy="off")
    assert len(res["search_ladder"]) == ri.SEARCH_LADDER_MAX
    assert ("lane testsuite: record 29 of many" in res["search_ladder"])
    assert ("lane testsuite: record 0 of many" not in res["search_ladder"])
    assert ("lane testsuite: record 10 of many" not in res["search_ladder"])


def test_engine_attribution_prefers_tried_keeps_selection():
    """A lane the manager reached carries the manager's engines_tried; a
    lane the manager never reached keeps the caller's selection so the
    persisted record never loses the selection context."""
    assert ri._engine_attribution({"engines_tried": ["default+fullset"]},
                                  ["selection"]) == ["default+fullset"]
    assert ri._engine_attribution({"ok": False}, ["ddg news"]) == ["ddg news"]
    assert ri._engine_attribution(None, []) == []


def test_engine_attribution_reads_ladder_when_tried_empty():
    """r1 finding 2: empty engines_tried no longer leaves the attribution
    blank when the run's own ladder records show a lane ADDING results.
    Every shape that says a lane added names it; shapes for 0 results, +0,
    off-topic-only, not engaged, held back and skipped lanes never leak a
    key; with no producing record the caller's selection keeps standing."""
    assert ri._engine_attribution(
        {"engines_tried": [],
         "ladder": ["searx.space: total after lane 2 (instances: a)",
                    "searx.space browser: 2 results",
                    "direct keyless: +2 (ddg-html, wikipedia-api)",
                    "searx_fallback merge: +4 (source: searx.space: a)"]}) \
        == ["searx.space", "searx.space browser", "direct keyless",
            "searx_fallback merge"]
    assert ri._engine_attribution(
        {"engines_tried": [],
         "ladder": ["searx.space: tried 2 instance-probes, +0 results",
                    "searx.space: tried 2 instance-probes, failed "
                    "(URLError: connection refused)",
                    "searx.space: off-topic results dropped (1 instance "
                    "result sets, 3 results)",
                    "searx.space browser: off-topic (4 results)",
                    "local: 0 results across []",
                    "local: full set off-topic (4 results)",
                    "direct keyless: 0 results",
                    "local: skipped (GREYSEARCH_SKIP_LOCAL)",
                    "direct keyless: not engaged (time budget reached)"]},
        ["selection"]) == ["selection"]
    assert ri._engine_attribution(
        {"engines_tried": [], "ladder": []}, ["ddg news"]) == ["ddg news"]
    assert ri._engine_attribution(
        {"engines_tried": [],
         "ladder": ["local: degraded (3 required engines down: ddg)"]},
        []) == []
    assert ri._engine_attribution(
        {"engines_tried": [],
         "ladder": ["direct keyless: +4 (ddg-html, archive-org)",
                    "google browser: 3 results"]}) \
        == ["direct keyless", "archive-org", "google browser"]


def test_engine_attribution_union_appends_ladder_keys():
    """Residual rule: a non-empty engines_tried keeps its lead;
    producing lanes the ladder records name that it did NOT report are
    appended deduplicated in first-seen ladder order; captcha marks and
    held-back/off-topic records never name a lane; with no producing
    record the manager's selection alone stands."""
    assert ri._engine_attribution(
        {"engines_tried": ["default+fullset"],
         "ladder": ["proxy mode off: direct",
                    "local: 3 results (full engine set)",
                    "searx.space: total after lane 23 (instances: a)",
                    "direct keyless: not engaged (enough results)"]}) \
        == ["default+fullset", "local", "searx.space"]
    assert ri._engine_attribution(
        {"engines_tried": ["default+fullset", "searx.space"],
         "ladder": ["searx.space: total after lane 10 (instances: a)",
                    "searx.space browser: 4 results"]}) \
        == ["default+fullset", "searx.space", "searx.space browser"]
    assert ri._engine_attribution(
        {"engines_tried": ["default+fullset"],
         "ladder": ["captcha: solver waited 14s (solved)",
                    "captcha: persisted (solver wait + retry exhausted)",
                    "searx.space browser: held back",
                    "local: skipped (GREYSEARCH_SKIP_LOCAL)",
                    "local: 0 results across []"]}) \
        == ["default+fullset"]
    assert ri._engine_attribution({"engines_tried": ["selection"]},
                                  ["selection"]) == ["selection"]


def test_co_run_engines_union_end_to_end(monkeypatch):
    """Residual rule end to end: a stubbed co-run whose manager names its
    local selection AND whose ladder records the searx.space lane adding
    rows persists the union (managed keys in the lead, searx.space behind)
    in searches[i]["engines"]; captcha marks ride the ladder without
    turning into keys."""
    ladder = ["proxy mode off: direct",
              "captcha: solver waited 12s (solved)",
              "searx.space: total after lane 2 (instances: testsuite.one)",
              "direct keyless: not engaged (enough results)"]
    bridge = _stub_bridge_for_deep(
        monkeypatch, ladder, ["default+fullset"])
    res = bridge.research_deep(
        "rain garden sizing for a driveway", max_pages=2,
        max_total_seconds=40, enable_semantic_judge=False,
        search_proxy="off")
    lane = next(s for s in res["searches"] if s.get("category") == "general")
    assert lane["engines"] == ["default+fullset", "searx.space"]
    assert ("captcha: solver waited 12s (solved)" in res["search_ladder"])


def test_deep_external_lane_engines_and_curation_claim(monkeypatch, tmp_path):
    """r1 finding 2 end to end: a run whose manager search carries no
    engines_tried but whose ladder records show the external lanes adding
    results persists those lane keys in searches[i]["engines"], and the
    curation record the docstring describes carries the same keys."""
    ladder = [
        "proxy mode off: direct",
        "local: 0 results across []",
        "local: degraded (3 required engines down: wikipedia)",
        "searx.space: tried 2 instance-probes, +0 results",
        "searx.space: total after lane 2 (instances: testsuite.one)",
        "direct keyless: +2 (ddg-html, wikipedia-api)",
    ]
    bridge = _stub_bridge_for_deep(monkeypatch, ladder, [])
    res = bridge.research_deep(
        "rain garden sizing for a driveway", max_pages=2,
        max_total_seconds=40, enable_semantic_judge=False,
        search_proxy="off")
    lane = next(s for s in res["searches"] if s.get("category") == "general")
    assert lane["engines"] == ["searx.space", "direct keyless"]
    assert all(rec in res["search_ladder"] for rec in ladder)
    cpath = gm._write_curation(res, "rain garden sizing for a driveway",
                               tmp_path / "guide.md")
    data = json.loads(cpath.read_text())
    cur_lane = next(s for s in data["searches"]
                    if s.get("category") == "general")
    assert cur_lane["engines"] == ["searx.space", "direct keyless"]


def test_deep_mustrun_full_local_ladder_names_space_engines(monkeypatch):
    """Must-run at the attribution level: the must-run full-local ladder
    carries the space co-lane's produced-row record, and the
    ladder-derived engine scan reads it into the lane record for a run
    whose manager return carries no engines_tried (a delivering co-lane is
    never recorded as never-ran)."""
    bridge = _stub_bridge_for_deep(monkeypatch, _LADDER_FULL_LOCAL, [])
    res = bridge.research_deep(
        "rain garden sizing for a driveway", max_pages=1,
        max_total_seconds=40, enable_semantic_judge=False,
        search_proxy="off")
    lane = next(s for s in res["searches"] if s.get("category") == "general")
    assert lane["engines"] == ["local", "searx.space"]
    assert all(rec in res["search_ladder"] for rec in _LADDER_FULL_LOCAL)


# ---- run JSON writer + health digest --------------------------------------

def test_run_json_writer_persists_bounded_ladder(monkeypatch, tmp_path):
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(gm, "_RUNS_DIR", runs_dir)
    monkeypatch.setattr(gm, "_RUNS", {})
    records = (_LADDER_FULL_LOCAL
               + [f"lane testsuite: filler record {i}" for i in range(30)])
    gm._set_run("run-testsuite-json", status="done", query="rain garden",
                started_ts=1.0, search_ladder=list(records))
    data = json.loads((runs_dir / "run-testsuite-json.json").read_text())
    kept = data["search_ladder"]
    assert len(kept) == ri.SEARCH_LADDER_MAX
    assert kept == records[len(records) - ri.SEARCH_LADDER_MAX:]
    gm._set_run("run-testsuite-start", status="running", query="q",
                started_ts=1.0)
    data = json.loads((runs_dir / "run-testsuite-start.json").read_text())
    assert "search_ladder" not in data


def test_digest_collapses_ladder_into_short_line(monkeypatch):
    monkeypatch.setattr(gm, "_RUNS", {
        "run-testsuite-a": {"query": "rain garden", "status": "done",
                            "started_ts": 2.0,
                            "search_ladder": list(_LADDER_FULL_LOCAL)}})
    out = gm._trim_runs_for_digest()
    rec = out[0]
    assert "search_ladder" not in rec
    assert rec["ladder"] == ("local: 19 results (full engine set); "
                             "searx.space: total after lane 23 "
                             "(instances: testsuite.one); "
                             "direct keyless: not engaged (enough results)")


def test_digest_ladder_last_state_wins_and_notes_dropped(monkeypatch):
    monkeypatch.setattr(gm, "_RUNS", {
        "run-testsuite-b": {"query": "q", "status": "done",
                            "started_ts": 3.0,
                            "search_ladder": [
                                "proxy mode off: direct",
                                "local: probe down",
                                "local: 3 results (engines: wikipedia)",
                                "search proxy: on requested "
                                "(external search lanes held back)",
                                "direct keyless: 0 results",
                                "direct keyless: +1 (ddg-html)"]}})
    out = gm._trim_runs_for_digest()
    rec = out[0]
    assert rec["ladder"] == ("local: 3 results (engines: wikipedia); "
                             "direct keyless: +1 (ddg-html)")


# ---- thin fill lane joins the ladder ---------------------------------------

def test_thin_fill_appends_lane_record(monkeypatch):
    def fake_deep(query, **kwargs):
        return {"pages_fetched": [], "searches": [],
                "search_ladder": ["local: 1 result (engines: wikipedia)"],
                "run_report": {"quality_warnings": [], "skipped_pages": [],
                               "accepted_count": 0, "quarantined_count": 0,
                               "rejected_count": 0, "candidate_count": 0,
                               "scraped_count": 0, "accepted_sources": [],
                               "engines": [], "search_proxy": "off",
                               "search_proxy_note": "proxy mode off: direct",
                               "scrape_transport": "scrape direct (no proxy)"},
                "skipped_pages": [], "sources": [], "findings": [],
                "fill_lane": None, "judge_ran": False}
    monkeypatch.setattr(gm, "research_deep", fake_deep)
    fill_payload = {"ok": True, "results": [
        {"url": "https://example.two/fill", "title": "rain garden fill",
         "content": "rain garden sizing fill content", "domain": "example.two",
         "tier": "unlisted"}], "source": "searx.space: testsuite",
        "ladder": ["searx.space: +1"]}
    monkeypatch.setattr(sf, "search", lambda *a, **k: dict(fill_payload))
    bridge = ri.get_bridge()
    monkeypatch.setattr(bridge.scraper, "fetch_url", _fake_ok_fetch)
    monkeypatch.setattr(relevance_mod, "judge_available", lambda: False)
    res = gm._execute_deep("rain garden sizing testsuite fill", None, 10, 60)
    fill_rec = res["search_ladder"][-1]
    assert fill_rec.startswith("thin-fill: searx.space: testsuite"), fill_rec
    assert res["run_report"]["search_ladder"][-1] == fill_rec
    assert res["ladder_lane"] == "searx.space: testsuite"


# ---- publisher: truthful per-run lane blurb --------------------------------

def test_blurb_names_lanes_that_ran_only():
    result = {"findings": [
        {"text": "Rain garden sizing depends on the drainage area and the "
                 "overflow outlet placement together in every design.",
         "source_domains": ["example.com"], "source_urls": [],
         "tier": "unlisted", "score": 2}],
        "pages_fetched": [],
        "search_ladder": list(_LADDER_FULL_LOCAL)}
    base = gp._synthesize_brief(result)
    assert "searx.space and direct-engine search ladder" not in base
    assert "Search lanes this run: " in base
    assert "searx.space: total after lane 23 (instances: testsuite.one)" in base
    assert "direct keyless: not engaged (enough results)" in base
    assert "proxy mode off" not in base


def test_blurb_neutral_when_ladder_record_absent():
    result = {"findings": [
        {"text": "Rain garden sizing depends on the drainage area and the "
                 "overflow outlet placement together in every design.",
         "source_domains": ["example.com"], "source_urls": [],
         "tier": "unlisted", "score": 2}],
        "pages_fetched": []}
    base = gp._synthesize_brief(result)
    assert "searx.space" not in base
    assert "direct keyless" not in base
    assert "Search lanes this run" not in base


def test_overview_appends_true_lane_summary():
    result = {"searches": [
        {"category": "decomposition", "query": "a", "ok": True, "n": 1},
        {"category": "general", "query": "b", "ok": True, "n": 8}],
        "search_ladder": list(_LADDER_FULL_LOCAL)}
    out = gp._synthesize_overview(result)
    assert ("Search coverage for this run, decomposition: 1 results; "
            "general: 8 results.") in out
    assert "Lanes engaged this run: " in out
    assert "searx.space: total after lane 23 (instances: testsuite.one)" in out


def test_publisher_lane_states_last_per_lane():
    result = {"search_ladder": [
        "local: probe said down",
        "searx.space: tried 2 instance-probes, +0 results",
        "searx.space browser: failed/bound-out (RuntimeError: no slot)",
        "searx.space browser: 4 results",
        "direct keyless: 0 results",
        "direct keyless: +5 (ddg-html)"]}
    entries = gp._lane_states(result)
    assert entries == ["local: probe said down",
                       "searx.space: tried 2 instance-probes, +0 results",
                       "searx.space browser: 4 results",
                       "direct keyless: +5 (ddg-html)"]


def test_publisher_lane_states_exclude_notes_and_odd_keys():
    result = {"search_ladder": [
        "proxy mode off: direct",
        "search proxy: on requested (external lanes held back)",
        "relevance gate: 12 kept of 30",
        "local: 4 results (engines: wikipedia)"]}
    entries = gp._lane_states(result)
    assert entries == ["relevance gate: 12 kept of 30",
                       "local: 4 results (engines: wikipedia)"]


# ---- honesty sweep: no persisted/blurb output carries bing-rss -------------

def test_no_new_output_carries_bing_rss_marker():
    manager_res = {"ladder": [
        "proxy mode off: direct",
        "local: 3 results (engines: wikipedia)",
        "direct keyless: +2 (ddg-html)"]}
    publisher_result = {"findings": [
        {"text": "Rain garden sizing depends on the drainage area and the "
                 "overflow outlet placement together in every design.",
         "source_domains": ["example.com"], "source_urls": [],
         "tier": "unlisted", "score": 2}],
        "pages_fetched": [], "search_ladder": list(_LADDER_FULL_LOCAL)}
    for blob in (str(manager_res["ladder"]), str(_LADDER_FULL_LOCAL),
                 str(gp._synthesize_brief(publisher_result)),
                 gp._lane_summary_text(publisher_result)):
        assert "bing-rss" not in blob and "bing rss" not in blob.lower()


_THREE_ENTITY_LADDERS = {
    "RainGarden Sizing": [
        "local: probe down",
        "local: 5 results (engines: wikipedia)",
        "searx.space: total after lane 7 (instances: testsuite.one)",
        "searx.space browser: 2 results",
        "direct keyless: +3 (ddg-html, archive-org)",
        "google browser: 1 results",
        "proxy mode off: direct",
        "captcha: solver waited 11s (solved)",
    ],
    "Driveway Drainage": [
        "local: 4 results (engines: duckduckgo)",
        "local unfiltered rescue: 2 results",
        "searx.space: total after lane 5 (instances: testsuite.two)",
        "searx.space browser: held back",
        "direct keyless: +2 (ddg-html)",
        "google browser: held back",
        "captcha: solver waited 9s (solved)",
        "search proxy: on requested (external lanes held back)",
    ],
    "PrunerBlade Sharpening": [
        "local: 0 results across []",
        "searx.space: tried 1 instance-probes, failed (URLError: refused)",
        "direct keyless: 0 results",
        "proxy mode off: direct",
        "captcha: persisted (solver wait + retry exhausted)",
    ],
}


def _three_entity_bridge(monkeypatch):
    """Deep-run stub whose three per-entity searches each return their own
    distinct ladder records, so the persisted-ladder composition is
    exercised against a real multi-entity record flood (24 distinct
    records before the run-level bound)."""
    bridge = ri.get_bridge()

    def fake_search(query=None, **kwargs):
        recs = _THREE_ENTITY_LADDERS.get(query)
        if recs is None:
            return {"ok": False, "results": [],
                    "ladder": ["proxy mode off: direct"],
                    "engines_tried": [], "search_proxy": "off",
                    "last_error": "stubbed rescue lane"}
        rows = [{"url": (f"https://example.one/"
                         f"{query.lower().replace(' ', '-')}-{i}"),
                 "title": f"{query} example {i}",
                 "content": (f"{query} walkthrough with "
                             f"{' '.join(_THREE_ENTITY_LADDERS)} depth"),
                 "engine": "testsuite"} for i in range(4)]
        return {"ok": True, "results": rows, "ladder": list(recs),
                "engines_tried": [], "search_proxy": "off",
                "last_error": None}

    monkeypatch.setattr(bridge, "searxng_manager",
                        types.SimpleNamespace(search=fake_search,
                                              get_working_engines=lambda: []))
    monkeypatch.setattr(bridge.scraper, "fetch_url", _fake_ok_fetch)
    monkeypatch.setattr(ri, "_pace", lambda key: 0.0)
    monkeypatch.setattr(relevance_mod, "judge_available", lambda: False)
    return bridge


def test_three_entity_run_keeps_every_lane_record(monkeypatch):
    """A 3-entity decomposition floods the persisted ladder; run-level lane
    records keep priority so no engaged lane's records are crowded out,
    the decomposition pseudo-records (the entity query strings) never ride
    the run ladder, and each entity's detail stays on its own per-search
    record exactly as before."""
    bridge = _three_entity_bridge(monkeypatch)
    res = bridge.research_deep(
        "RainGarden Sizing vs Driveway Drainage vs PrunerBlade Sharpening",
        max_pages=2, max_total_seconds=40, enable_semantic_judge=False,
        search_proxy="off")
    ladder = res["search_ladder"]
    assert len(ladder) == ri.SEARCH_LADDER_MAX
    for entity, recs in _THREE_ENTITY_LADDERS.items():
        entity_rec = next(s for s in res["searches"]
                          if s.get("query") == entity)
        assert entity_rec["ladder"] == recs
        for lane_rec in [r for r in recs if ri._is_lane_record(r)]:
            assert lane_rec in ladder, (entity, lane_rec)
    decomp = res["searches"][0]
    assert decomp["category"] == "decomposition"
    for pseudo in _THREE_ENTITY_LADDERS:
        assert pseudo in decomp["ladder"]
        assert pseudo not in ladder
    assert ladder[0] == "local: probe down"
    assert (ladder.index("local: probe down")
            < ladder.index("captcha: solver waited 11s (solved)"))
    assert ladder[-1] == "scrape direct (no proxy)"
    assert ri._lane_keys_from_ladder(ladder) == [
        "local", "searx.space", "searx.space browser", "direct keyless",
        "archive-org", "google browser", "local unfiltered rescue"]


def test_deep_ladder_cap_still_yields_to_nothing_but_lane_truth():
    """The priority tiers never grow the bound: lane truth keeps its
    records first, detail fills the remainder, and the persisted list
    never exceeds SEARCH_LADDER_MAX under either tier."""
    assert ri._is_lane_record("searx.space browser: held back") is True
    assert ri._is_lane_record("searx.space: tried 2 instance-probes, +0 "
                              "results") is True
    assert ri._is_lane_record("proxy mode off: direct") is False
    assert ri._is_lane_record("search proxy: on requested "
                              "(external lanes held back)") is False
    assert ri._is_lane_record("") is False


def test_digest_clip_carries_marker_and_exact_fit_gains_nothing(monkeypatch):
    """A digest value the 400-char cap actually cuts ends with "..." (three
    ASCII dots); a value that fits exactly and a short lane line gain
    nothing and stay byte-identical."""
    states = ["local: " + ("q" * 113)]
    states += [f"{key}: " + ("q" * (120 - len(key) - 2))
               for key in ("searx.space browser", "direct keyless",
                           "google browser")]
    monkeypatch.setattr(gm, "_RUNS", {
        "run-testsuite-cap": {"query": "cap", "status": "done",
                              "started_ts": 9.0,
                              "search_ladder": list(states),
                              "preview": "w" * 500},
        "run-testsuite-exact": {"query": "exact", "status": "done",
                                "started_ts": 8.0,
                                "search_ladder": ["local: 3 results "
                                                  "(engines: ddg)"],
                                "preview": "p" * 400}})
    out = {r["query"]: r for r in gm._trim_runs_for_digest()}
    cut = out["cap"]["ladder"]
    assert cut.endswith("...") and len(cut) == 403
    assert cut[:400] == "; ".join(states)[:400]
    assert out["cap"]["preview"] == ("w" * 500)[:400] + "..."
    assert out["exact"]["ladder"] == "local: 3 results (engines: ddg)"
    assert out["exact"]["preview"] == "p" * 400


def test_mcp_curation_searches_carry_union_engines(monkeypatch, tmp_path):
    """At the mcp level: a stubbed deep run whose per-search records carry
    the union attribution (managed keys in the lead, ladder-derived
    producing keys appended) keeps those keys verbatim in the curation
    JSON the mcp writer stores."""
    pages = [{"url": f"https://example.one/rain-garden-{i}",
              "title": f"rain garden sizing {i}", "domain": "example.one",
              "tier": "unlisted", "ok": True, "text": "", "raw_text": "",
              "links": [], "images": [],
              "clean": {"body_text": f"rain garden sizing paragraph {i}",
                        "main_chars": 40},
              "relevance": {"decision": "accept", "relevance_score": 90,
                            "source_type": "reference",
                            "reason": "testsuite accept",
                            "allowed_content": [], "risk_flags": []}}
             for i in range(3)]

    def fake_deep(query, **kwargs):
        return {"question": query,
                "pages_fetched": [dict(p) for p in pages],
                "searches": [
                    {"category": "decomposition", "query": "a", "ok": True,
                     "n": 1, "engines": []},
                    {"category": "general", "query": "rain garden sizing",
                     "ok": True, "n": 4,
                     "engines": ["default+fullset", "searx.space",
                                 "direct keyless"]}],
                "search_ladder": ["local: 4 results (full engine set)"],
                "all_scraped_pages": [dict(p) for p in pages],
                "run_report": {"skipped_pages": [], "accepted_count": 3,
                               "scraped_count": 3, "candidate_count": 3}}

    monkeypatch.setattr(gm, "research_deep", fake_deep)
    res = gm._execute_deep("rain garden sizing testsuite", None, 3, 60)
    cpath = gm._write_curation(res, "rain garden sizing testsuite",
                               tmp_path / "guide.md")
    data = json.loads(cpath.read_text())
    lane = next(s for s in data["searches"]
                if s.get("category") == "general")
    assert lane["engines"] == ["default+fullset", "searx.space",
                               "direct keyless"]
    decomp = next(s for s in data["searches"]
                  if s.get("category") == "decomposition")
    assert decomp["engines"] == []


def test_overview_names_no_lane_when_ladder_absent():
    """A run with no ladder records (or an old result dict without the key)
    keeps the overview lane clause neutral: the coverage line prints and
    no lane name leaks into the guide."""
    base = {"searches": [
        {"category": "decomposition", "query": "a", "ok": True, "n": 1},
        {"category": "general", "query": "b", "ok": True, "n": 8}]}
    for result in (dict(base, search_ladder=[]), dict(base)):
        out = gp._synthesize_overview(result)
        assert ("Search coverage for this run, decomposition: 1 results; "
                "general: 8 results.") in out
        assert "Lanes engaged this run" not in out
        assert "searx.space" not in out
        assert "direct keyless" not in out
