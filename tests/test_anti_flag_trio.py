#!/usr/bin/env python3
"""The anti-flag trio, hermetic: the browser-lane search pace guard, the
relevant-floor hunt budget cap, and the captcha WAIT 30-60 s tunables.

Contracts: a browser-lane search request to
one engine never comes faster than the human pace within a process (page
fetches keep their own 3 s politeness and never consult the guard); the
floor hunt may climb rungs WITHOUT extending the run budget (extension is
opt-in and capped); a captcha/challenge page is waited out on the 30-60 s
doctrine with bounded two-phase machinery and the solver kill switch
keeping full priority. All network lanes are stubbed; assertions inspect
records, sleeps and ladder lines, never traffic."""
import random
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import browser_session as bs
import research_integration as ri
import searx_fallback as sf
import searxng_integrated as si
import test_captcha_wait as cw
import test_continue_floor as cf
import test_ladder_persistence as lp

_PACE_LINE = "searx.space browser: pace wait 30s before https://h.test"


def _clock(monkeypatch, t=100.0):
    """Scripted wall clock plus a sleep recorder: the clock only moves when
    the test moves it, so every guard sleep is observed, never real."""
    box = {"t": t}
    sleeps = []
    monkeypatch.setattr(time, "time", lambda: box["t"])
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    return box, sleeps


@pytest.fixture(autouse=True)
def _pace_clean():
    bs.browser_pace_reset()
    yield
    bs.browser_pace_reset()


@pytest.fixture(autouse=True)
def _wait_env_clean(monkeypatch):
    for name in ("GREYSEARCH_CAPTCHA_WAIT_S", "GREYSEARCH_CAPTCHA_WAIT_MIN_S",
                 "GREYSEARCH_CAPTCHA_WAIT_MAX_S",
                 "GREYSEARCH_CAPTCHA_POLL_S", "GREYSEARCH_CAPTCHA_SOLVER"):
        monkeypatch.delenv(name, raising=False)


# ---- fix 1: the browser-lane search pace guard ----------------------------

def test_pace_settings_defaults_and_clamps(monkeypatch):
    """The pair comes from GREYSEARCH_BROWSER_PACE_S (default 30, exact 0
    is the inert bypass, a value in (0, 1) clamps up to the 1 s floor) and
    GREYSEARCH_BROWSER_PACE_JITTER_S (default 15, never below 0); junk or
    negative knob values fall back to their defaults."""
    monkeypatch.delenv("GREYSEARCH_BROWSER_PACE_S", raising=False)
    monkeypatch.delenv("GREYSEARCH_BROWSER_PACE_JITTER_S", raising=False)
    assert bs.browser_pace_settings() == (30.0, 15.0)
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_S", "0")
    assert bs.browser_pace_settings() == (0.0, 15.0)
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_S", "0.5")
    assert bs.browser_pace_settings() == (1.0, 15.0)
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_S", "90")
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_JITTER_S", "7")
    assert bs.browser_pace_settings() == (90.0, 7.0)
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_JITTER_S", "-3")
    assert bs.browser_pace_settings() == (90.0, 15.0)
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_S", "junk")
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_JITTER_S", "junk")
    assert bs.browser_pace_settings() == (30.0, 15.0)
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_S", "-2")
    assert bs.browser_pace_settings() == (30.0, 15.0)


def test_pace_wait_first_request_free_and_inert_bypass(monkeypatch):
    """An engine's first search request of the process never waits and a
    zero pace never engages: the exact-0 bypass adds nothing, sleeps
    nothing and writes no state, with or without a key."""
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_S", "0")
    box, sleeps = _clock(monkeypatch)
    assert bs.browser_pace_wait("https://g.example") == 0.0
    assert bs.browser_pace_wait("https://g.example") == 0.0
    assert sleeps == []
    assert bs.browser_pace_at() == {}
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_S", "30")
    assert bs.browser_pace_wait("") == 0.0
    assert bs.browser_pace_at() == {}


def test_pace_wait_spaces_same_engine_only(monkeypatch):
    """Two same-engine search requests keep at least the pace between them
    within a process (30 here, watched as one recorded sleep), while a
    different engine never waits and a request already past its spacing is
    free; the engine's timestamp updates on every request."""
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_S", "30")
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_JITTER_S", "0")
    box, sleeps = _clock(monkeypatch)
    assert bs.browser_pace_wait("https://g.example") == 0.0
    assert bs.browser_pace_wait("https://other.example") == 0.0
    assert sleeps == []
    assert bs.browser_pace_wait("https://g.example") == 30.0
    assert sleeps == [30.0]
    box["t"] = 135.0
    assert bs.browser_pace_wait("https://g.example") == 0.0
    assert bs.browser_pace_wait("https://other.example") == 0.0
    assert bs.browser_pace_wait("https://g.example") == 30.0
    assert sleeps == [30.0, 30.0]
    at = bs.browser_pace_at()
    assert at["https://g.example"] == 135.0
    assert at["https://other.example"] == 135.0


def test_pace_wait_applies_jitter_window(monkeypatch):
    """The wait is the pace plus one draw in 0-jitter seconds, all inside
    the recorded sleep; a blank engine name never engages."""
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_S", "30")
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_JITTER_S", "7")
    box, sleeps = _clock(monkeypatch)
    monkeypatch.setattr(random, "uniform", lambda a, b: 3.5)
    assert bs.browser_pace_wait("eng") == 0.0
    assert bs.browser_pace_wait("eng") == 33.5
    assert sleeps == [33.5]
    assert list(bs.browser_pace_at()) == ["eng"]


def test_pace_never_gates_page_fetches(monkeypatch):
    """A page fetch with the pace guard armed (a fresh timestamp already
    seeded for its host) keeps exactly the fetch politeness settle and is
    never delayed or recorded by the search pace machinery."""
    assert ri._POLITENESS_S == 3.0
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_S", "30")
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_JITTER_S", "0")
    box, sleeps = _clock(monkeypatch)
    bs._BROWSER_PACE_AT["example.three"] = box["t"]
    body = "Ordinary pacing page body for the fetch case. "
    pg = cw._FakePage([cw.CLEAR], body=body)
    monkeypatch.setattr(bs, "_connection", lambda: {"ctx": cw._ctx_of(pg)})
    monkeypatch.setattr(bs._rij, "sanitize_text", lambda t: (t, []))
    monkeypatch.setattr(bs, "_clean_text", lambda t: t.strip())
    res = bs.fetch_text("https://example.three/paced")
    assert res["ok"] is True
    assert res["text"] == body.strip()
    assert pg.sleeps_ms == [1200]
    assert not any(s >= 1.0 for s in sleeps)
    assert bs.browser_pace_at() == {"example.three": 100.0}


def test_pace_records_landed_for_instance_bases(monkeypatch):
    """An instance base whose search request waits for the pace records the
    waited seconds in its lane line and in its per-base debug, and two
    consecutive searches to the same instance base each wait their own
    spacing (clear-then-record, so a digest names only its own attempts)."""
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_S", "30")
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_JITTER_S", "0")
    box, sleeps = _clock(monkeypatch)
    pg = cw._SfPage([cw.CLEAR])
    cw._sf_lane(monkeypatch, pg)
    bs._BROWSER_PACE_AT["https://search.hbubli.cc"] = box["t"]
    assert sf.browser_search_public("gardens", timeout=1) == []
    assert sf.pace_wait_records() == [
        "searx.space browser: pace wait 30s before https://search.hbubli.cc"]
    dbg = (sf._LAST_BROWSER_DEBUG.get("https://search.hbubli.cc") or {})
    assert dbg.get("pace_waited_s") == 30.0
    assert sleeps.count(30.0) == 1
    assert bs.browser_pace_at()["https://search.hbubli.cc"] == 100.0
    assert sf.browser_search_public("gardens", timeout=1) == []
    assert sf.pace_wait_records() == [
        "searx.space browser: pace wait 30s before https://search.hbubli.cc",
        "searx.space browser: pace wait 30s before https://searx.ononoki.org"]
    assert sleeps.count(30.0) == 5
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_S", "0")
    assert sf.browser_search_public("gardens", timeout=1) == []
    assert sf.pace_wait_records() == []
    assert sleeps.count(30.0) == 5


def test_pace_records_reach_the_rung_lanes(monkeypatch):
    """The google and bing browser rungs are browser-lane search requests
    too: each waits its own engine's spacing and names the waited seconds
    in its own record family, ahead of the rung's outcome line."""
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_S", "30")
    monkeypatch.setenv("GREYSEARCH_BROWSER_PACE_JITTER_S", "0")
    box, sleeps = _clock(monkeypatch)

    class _BoomPage:
        def goto(self, *a, **k):
            raise RuntimeError("stubbed")

    cw._sf_lane(monkeypatch, _BoomPage())
    bs._BROWSER_PACE_AT["https://www.google.com"] = box["t"]
    bs._BROWSER_PACE_AT["https://www.bing.com"] = box["t"]
    assert sf.browser_search_public("gardens", timeout=1) == []
    grecs = sf.google_lane_records()
    assert grecs[0] == "google browser: pace wait 30s before the search request"
    assert grecs[1].startswith("google browser: failed/bound-out"), grecs
    brecs = sf.bing_lane_records()
    assert brecs[0] == "bing browser: pace wait 30s before the search request"
    assert brecs[1].startswith("bing browser: failed/bound-out"), brecs
    assert sleeps == [30.0, 30.0]
    at = bs.browser_pace_at()
    assert at["https://www.google.com"] == 100.0
    assert at["https://www.bing.com"] == 100.0


def test_pace_records_ride_the_ladder_digests(monkeypatch):
    """A nonzero pace wait rides the affected lane's digest on both
    surfaces: sf.search places it ahead of the captcha marks while
    searxng_integrated relays it inside the browser rung's branch after the
    marks, and neither ever invents a record for an attempt that ran free."""
    def fake_browse(query, max_results=None, instances=None, need=None,
                    **k):
        del sf._PACE_RECORDS[:]
        del sf._CAPTCHA_HOLDS[:]
        sf._PACE_RECORDS.append(_PACE_LINE)
        sf._CAPTCHA_HOLDS.append(cw._PERSISTED_MARK)
        return []

    monkeypatch.setattr(sf, "browser_search_public", fake_browse)
    monkeypatch.setattr(sf, "_harvest_instances", lambda **k: [])
    monkeypatch.setattr(sf, "_direct_search", lambda *a, **k: [])
    cw._stub_ladder(monkeypatch)
    res = sf.search("gardens")
    assert res["ok"] is False
    assert res["ladder"] == ["proxy mode off: direct", _PACE_LINE,
                             cw._PERSISTED_MARK, "local", "searx.space",
                             "direct"]

    monkeypatch.setenv("GREYSEARCH_RELEVANT_TARGET", "40")
    cf._patch_proxy(monkeypatch)
    sf_stub = types.SimpleNamespace(
        browser_search_public=fake_browse,
        _direct_search=lambda *a, **k: [],
        _captcha_holds=lambda: list(sf._CAPTCHA_HOLDS),
        pace_wait_records=lambda: list(sf._PACE_RECORDS),
        _warm_remote_host=lambda *a, **k: None,
        _filter_instance_junk=lambda q, r, *a, **k: list(r))
    monkeypatch.setattr(si, "_searx_fallback", lambda: sf_stub)
    mgr = cf._floor_mgr(monkeypatch, cf._rows("a", 10, "127.0.0.1:8888"), True)
    monkeypatch.setattr(si, "_local_alive", lambda u, timeout=15.0: True)
    res2 = mgr.search(cf._QUERY, max_results=10, max_seconds=30)
    ladder = res2["ladder"]
    assert _PACE_LINE in ladder and cw._PERSISTED_MARK in ladder
    assert ladder.index(cw._PERSISTED_MARK) < ladder.index(_PACE_LINE)


# ---- fix 2: the relevant-floor hunt budget cap ----------------------------

def _cap_sf_stub(browse):
    """searx_fallback namespace stub for the manager's browser rung: browse
    is the browser_search_public replacement, the record families stay
    empty and the JSON-probe warm call is inert."""
    return types.SimpleNamespace(
        browser_search_public=browse,
        _direct_search=lambda *a, **k: [],
        _captcha_holds=lambda: [],
        pace_wait_records=lambda: [],
        google_lane_records=lambda: [],
        bing_lane_records=lambda: [],
        _warm_remote_host=lambda *a, **k: None,
        _filter_instance_junk=lambda q, r, *a, **k: list(r))


def test_floor_hunt_default_zero_never_extends(monkeypatch):
    """The default cap is 0: an unmet floor engages the upper rungs WITHOUT
    extending the run budget (a spent budget stays spent, the digest says
    so with the cap), the browser rung still hunts when there is budget to
    do it in, and no run ever claims an extension it did not take."""
    monkeypatch.delenv("GREYSEARCH_FLOOR_HUNT_EXTENSIONS", raising=False)
    assert si._floor_hunt_cap() == 0
    assert ri.floor_hunt_extensions() == 0
    monkeypatch.setenv("GREYSEARCH_RELEVANT_TARGET", "40")
    cf._patch_proxy(monkeypatch)
    seen = []

    def browse(q, mr, insts=None, need=None, **k):
        seen.append(q)
        return cf._rows("b", 10, "https://searx.ononoki.org")

    monkeypatch.setattr(si, "_searx_fallback", lambda: _cap_sf_stub(browse))
    mgr = cf._floor_mgr(monkeypatch, cf._rows("a", 10, "127.0.0.1:8888"), True)
    monkeypatch.setattr(si, "_local_alive", lambda u, timeout=15.0: True)
    res = mgr.search(cf._QUERY, max_results=10, max_seconds=0.0)
    floor_lines = [l for l in res["ladder"]
                   if l.startswith("relevant floor:")]
    assert floor_lines == [
        "relevant floor: 10/40 relevant so far; upper rungs engaged "
        "(floor hunt: budget not extended (cap 0))"]
    assert "searx.space: not engaged (time budget reached)" in res["ladder"]
    assert ("direct keyless: not engaged (time budget reached)"
            in res["ladder"])
    assert "floor_hunt_extension_used" not in res
    assert seen == []
    assert mgr._floor_hunt_used == 0

    res2 = mgr.search(cf._QUERY, max_results=10, max_seconds=30)
    floor_lines2 = [l for l in res2["ladder"]
                    if l.startswith("relevant floor:")]
    assert floor_lines2 == [
        "relevant floor: 10/40 relevant so far; upper rungs engaged "
        "(floor hunt: budget not extended (cap 0))"]
    assert "searx.space browser: 10 results" in res2["ladder"]
    assert "floor_hunt_extension_used" not in res2
    assert seen == [cf._QUERY]
    assert mgr._floor_hunt_used == 0


def test_floor_hunt_cap_extends_then_exhausts(monkeypatch):
    """With the cap at 1 the old extension behavior returns for the first
    under-floor hunt of that manager (the spent budget buys the browser
    rung its attempt and the flag rides the result), the second hunt is
    refused with the cap named, and a fresh manager extends again."""
    monkeypatch.setenv("GREYSEARCH_FLOOR_HUNT_EXTENSIONS", "1")
    monkeypatch.setenv("GREYSEARCH_RELEVANT_TARGET", "40")
    cf._patch_proxy(monkeypatch)

    def browse(q, mr, insts=None, need=None, **k):
        return cf._rows("b", 10, "https://searx.ononoki.org")

    monkeypatch.setattr(si, "_searx_fallback", lambda: _cap_sf_stub(browse))
    mgr = cf._floor_mgr(monkeypatch, cf._rows("a", 10, "127.0.0.1:8888"), True)
    monkeypatch.setattr(si, "_local_alive", lambda u, timeout=15.0: True)
    res = mgr.search(cf._QUERY, max_results=10, max_seconds=0.0)
    floor_lines = [l for l in res["ladder"]
                   if l.startswith("relevant floor:")]
    assert floor_lines == [
        "relevant floor: 10/40 relevant so far; upper rungs engaged "
        "(budget extended for the hunt, cap 1)"]
    assert "searx.space browser: 10 results" in res["ladder"]
    assert ("searx.space: not engaged (time budget reached)"
            not in res["ladder"])
    assert res["floor_hunt_extension_used"] is True
    assert mgr._floor_hunt_used == 1

    res2 = mgr.search(cf._QUERY, max_results=10, max_seconds=0.0)
    floor_lines2 = [l for l in res2["ladder"]
                    if l.startswith("relevant floor:")]
    assert floor_lines2 == [
        "relevant floor: 10/40 relevant so far; upper rungs engaged "
        "(floor hunt: budget not extended (cap 1))"]
    assert "floor_hunt_extension_used" not in res2
    assert "searx.space: not engaged (time budget reached)" in res2["ladder"]
    assert mgr._floor_hunt_used == 1

    mgr2 = cf._floor_mgr(monkeypatch, cf._rows("a", 10, "127.0.0.1:8888"),
                         True)
    res3 = mgr2.search(cf._QUERY, max_results=10, max_seconds=0.0)
    assert res3["floor_hunt_extension_used"] is True
    assert mgr2._floor_hunt_used == 1


def test_floor_hunt_junk_values_fall_back_to_default(monkeypatch):
    """Junk and negative GREYSEARCH_FLOOR_HUNT_EXTENSIONS values fall back
    to the default cap of 0, a plain integer reads as the cap, and the
    research-side reader agrees with the manager's."""
    for junk in ("junk", "-4", ""):
        monkeypatch.setenv("GREYSEARCH_FLOOR_HUNT_EXTENSIONS", junk)
        assert si._floor_hunt_cap() == 0
        assert ri.floor_hunt_extensions() == 0
    monkeypatch.setenv("GREYSEARCH_FLOOR_HUNT_EXTENSIONS", "3")
    assert si._floor_hunt_cap() == 3
    assert ri.floor_hunt_extensions() == 3


_DEEP_QUESTION = ("RainGarden Sizing vs Driveway Drainage vs "
                  "PrunerBlade Sharpening")


def _deep_bridge(monkeypatch, env):
    """The bridge of test_ladder_persistence with a recording manager: each
    search records the floor_hunt_left it received and reports the
    extension flag exactly when it was granted budget (left > 0), so the
    run's tally behavior is observable call by call."""
    bridge = ri.get_bridge()
    seen = []

    def fake_search(query=None, **kwargs):
        left = kwargs.get("floor_hunt_left")
        assert isinstance(left, int) and left >= 0
        rows = [{"url": (f"https://example.one/"
                         f"{query.lower().replace(' ', '-')}-{i}"),
                 "title": f"{query} example {i}",
                 "content": f"{query} walkthrough detail",
                 "engine": "testsuite"} for i in range(4)]
        seen.append(left)
        record = {"ok": True, "results": rows,
                  "ladder": ["local: 4 results (engines: testsuite)"],
                  "engines_tried": [], "search_proxy": "off",
                  "last_error": None}
        if left > 0:
            record["floor_hunt_extension_used"] = True
        return record

    monkeypatch.setenv("GREYSEARCH_FLOOR_HUNT_EXTENSIONS", env)
    monkeypatch.setattr(bridge, "searxng_manager",
                        types.SimpleNamespace(search=fake_search,
                                              get_working_engines=lambda: []))
    monkeypatch.setattr(bridge.scraper, "fetch_url", lp._fake_ok_fetch)
    monkeypatch.setattr(ri, "_pace", lambda key: 0.0)
    monkeypatch.setattr(lp.relevance_mod, "judge_available", lambda: False)
    return bridge, seen


def test_floor_hunt_allowance_spans_one_deep_run(monkeypatch):
    """research_deep spends the run's allowance across its search lanes, not
    per lane: three entity lanes with a cap of 2 see left 2, then 1, then 0,
    each decrease matching a granted extension, so the third lane hunts in
    what budget the run already had and the run never exceeds the cap."""
    bridge, seen = _deep_bridge(monkeypatch, "2")
    res = bridge.research_deep(
        _DEEP_QUESTION, max_pages=1, max_total_seconds=40,
        enable_semantic_judge=False, search_proxy="off")
    assert seen[:3] == [2, 1, 0], seen
    assert all(left == 0 for left in seen[3:]), seen
    want = {"RainGarden Sizing", "Driveway Drainage",
            "PrunerBlade Sharpening"}
    lanes = [s for s in res["searches"] if s.get("query") in want]
    assert len(lanes) == 3, res["searches"]
    assert max(seen) == 2
    assert all(lefts >= 0 for lefts in seen)


def test_floor_hunt_allowance_zero_stays_zero_in_a_deep_run(monkeypatch):
    """With the cap at 0 every search lane of the deep run receives left 0:
    they all still run, none of them reports an extension, and none of the
    per-lane results carries the flag."""
    bridge, seen = _deep_bridge(monkeypatch, "0")
    res = bridge.research_deep(
        _DEEP_QUESTION, max_pages=1, max_total_seconds=40,
        enable_semantic_judge=False, search_proxy="off")
    assert seen[:3] == [0, 0, 0], seen
    assert all(left == 0 for left in seen[3:]), seen
    for s in res["searches"]:
        assert "floor_hunt_extension_used" not in s


def test_floor_hunt_left_reaches_manager_and_flag_relays(monkeypatch):
    """research_search passes its allowance to the manager (the default is
    the None shape for a run that never set one) and relays the manager's
    extension flag only when the manager granted one."""
    class _RecManager:
        def __init__(self):
            self.calls = []

        def search(self, **kwargs):
            self.calls.append(kwargs)
            record = {
                "ok": True,
                "results": [
                    {"title": f"Rain garden sizing example {i}",
                     "url": f"https://example.one/rain-garden-{i}",
                     "content": "rain garden sizing walkthrough for a "
                                "driveway"} for i in range(4)],
                "ladder": ["proxy mode off: direct",
                           "local: 4 results (full engine set)"],
                "engines_tried": ["duckduckgo"],
                "search_proxy": "",
                "last_error": None,
            }
            if kwargs.get("floor_hunt_left") == 1:
                record["floor_hunt_extension_used"] = True
            return record

        def get_working_engines(self):
            return ["duckduckgo"]

    def _bridge():
        b = ri.ResearchScraperBridge.__new__(ri.ResearchScraperBridge)
        b.excluded_domains = set()
        b.sites_data = {"categories": {}}
        return b

    mgr = _RecManager()
    b1 = _bridge()
    b1.searxng_manager = mgr
    res1 = b1.research_search("rain garden sizing", floor_hunt_left=1)
    assert mgr.calls[0]["floor_hunt_left"] == 1
    assert res1["floor_hunt_extension_used"] is True

    mgr2 = _RecManager()
    b2 = _bridge()
    b2.searxng_manager = mgr2
    res2 = b2.research_search("rain garden sizing")
    assert mgr2.calls[0]["floor_hunt_left"] is None
    assert "floor_hunt_extension_used" not in res2

    mgr3 = _RecManager()
    b3 = _bridge()
    b3.searxng_manager = mgr3
    res3 = b3.research_search("rain garden sizing", floor_hunt_left=0)
    assert mgr3.calls[0]["floor_hunt_left"] == 0
    assert "floor_hunt_extension_used" not in res3


# ---- fix 3: the captcha WAIT 30-60 s tunables ------------------------------

def test_captcha_phase_waits_defaults_clamp_and_pin(monkeypatch):
    """The pair defaults to (30, 60), the second never falls under the
    first (a lower max is raised), the single-length GREYSEARCH_CAPTCHA_WAIT_S
    pins both phases to its value and its own junk falls back to 45, and
    junk in the new knobs falls back to their defaults."""
    assert bs._CAPTCHA_WAIT_MIN_DEFAULT_S == 30.0
    assert bs._CAPTCHA_WAIT_MAX_DEFAULT_S == 60.0
    assert bs.captcha_phase_waits() == (30.0, 60.0)
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MIN_S", "80")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MAX_S", "9")
    assert bs.captcha_phase_waits() == (80.0, 80.0)
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MIN_S", "junk")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MAX_S", "")
    assert bs.captcha_phase_waits() == (30.0, 60.0)
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MIN_S", "20")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MAX_S", "55")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_S", "7")
    assert bs.captcha_phase_waits() == (7.0, 7.0)
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_S", "junk")
    assert bs.captcha_phase_waits() == (45.0, 45.0)


def test_wait_cycle_thirty_then_sixty_by_default(monkeypatch):
    """An exhausted page under the bare defaults (no knob env at all) takes
    the two-phase WAIT: with the poll at 9 s the split is observable as
    phase one consuming four settles (the 30 s phase) and phase two
    consuming seven (the 60 s phase), eleven settles and 99 s in total,
    and the mark is the persisted wording."""
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_POLL_S", "9")
    cw._set_clock(monkeypatch)
    pg = cw._FakePage([cw.PRESENT])
    rec = bs.captcha_wait_cycle(pg)
    assert rec == {"engaged": True, "solved": False, "waited_s": 99.0}
    assert pg.sleeps_ms == [9000] * 11
    assert bs.captcha_mark(rec) == cw._PERSISTED_MARK


def test_wait_default_split_solves_inside_phase_one(monkeypatch):
    """A token that arrives on the first evidence read of the 30 s phase
    ends the cycle right there: one 9 s settle, waited 9 s, and the mark
    names the waited seconds in the solved wording."""
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_POLL_S", "9")
    cw._set_clock(monkeypatch)
    pg = cw._FakePage([cw.PRESENT, cw.TOKEN])
    rec = bs.captcha_wait_cycle(pg)
    assert rec == {"engaged": True, "solved": True, "waited_s": 9.0}
    assert pg.sleeps_ms == [9000]
    assert bs.captcha_mark(rec) == "captcha: solver waited 9s (solved)"


def test_wait_env_pair_knobs_bound_the_cycle(monkeypatch):
    """GREYSEARCH_CAPTCHA_WAIT_MIN_S and GREYSEARCH_CAPTCHA_WAIT_MAX_S are
    read by the cycle itself: a 5 s phase one and a 9 s phase two with the
    poll at 1 s consume exactly fourteen settles and 14 s for an exhausted
    page, proving the bounded machinery under the new knobs."""
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MIN_S", "5")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MAX_S", "9")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_POLL_S", "1")
    cw._set_clock(monkeypatch)
    pg = cw._FakePage([cw.PRESENT])
    rec = bs.captcha_wait_cycle(pg)
    assert rec == {"engaged": True, "solved": False, "waited_s": 14.0}
    assert pg.sleeps_ms == [1000] * 14
    assert bs.captcha_mark(rec) == cw._PERSISTED_MARK


def test_wait_kill_switch_keeps_priority_and_bounded_wait(monkeypatch):
    """GREYSEARCH_CAPTCHA_SOLVER governs the local solver step, never the
    WAIT doctrine: with the knob off the cycle still runs its two bounded
    phases unchanged, and with it on the same bounded cycle answers the
    same (the solver is consulted only after a cycle ends
    engaged-but-unsolved, and launching never waits)."""
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_SOLVER", "off")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MIN_S", "2")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MAX_S", "3")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_POLL_S", "1")
    cw._set_clock(monkeypatch)
    pg = cw._FakePage([cw.PRESENT])
    rec = bs.captcha_wait_cycle(pg)
    assert rec == {"engaged": True, "solved": False, "waited_s": 5.0}
    assert pg.sleeps_ms == [1000] * 5
    assert bs.captcha_mark(rec) == cw._PERSISTED_MARK
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_SOLVER", "on")
    pg2 = cw._FakePage([cw.PRESENT])
    rec2 = bs.captcha_wait_cycle(pg2)
    assert rec2 == rec
    assert pg2.sleeps_ms == [1000] * 5
