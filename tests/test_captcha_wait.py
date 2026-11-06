#!/usr/bin/env python3
"""Hermetic captcha-WAIT contract tests.

No network, no real browser: the WAIT-and-retry cycle, the env forwarding
that reaches the detached launcher, the local solver step behind its kill
switch (the bounded re-wait governs, not the solver's word), the fetch_text
integration (persist skips honestly, solved proceeds with the mark) and the
search-lane mark surfacing are all exercised with stubbed pages and
scripted time. The single LIVE PROBE is manual verification, never a test.
"""
import re
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import browser_session as bs
import browser_ports as bp
import captcha_solve as cs
import patchright_headless as ph
import research_integration as ri
import searx_fallback as sf
from urllib.error import URLError

pytestmark = [pytest.mark.real_home]

PRESENT = {"widget_iframes": 1, "empty_response": 1,
           "filled_response": 0, "interstitial": False}
TOKEN = {"widget_iframes": 1, "empty_response": 0,
         "filled_response": 1, "interstitial": False}
CLEAR = {"widget_iframes": 0, "empty_response": 0,
         "filled_response": 0, "interstitial": False}

_PERSISTED_MARK = "captcha: persisted (solver wait + retry exhausted)"
_SOLVED_RE = re.compile(r"^captcha: solver waited \d+s \(solved\)$")
_CLOCK = {"t": 10.0}


def _set_clock(monkeypatch, start=10.0):
    """Scripted wall clock: the clock advances only on page settles (the
    waits), so a cycle's waited_s equals the settle total it consumed."""
    _CLOCK["t"] = start
    monkeypatch.setattr(time, "time", lambda: _CLOCK["t"])


def _knobs(monkeypatch, wait_s="0.001", poll_s="0.001"):
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_S", wait_s)
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_POLL_S", poll_s)


def _clear_holds():
    del sf._CAPTCHA_HOLDS[:]


@pytest.fixture(autouse=True)
def _knob_env(monkeypatch):
    monkeypatch.delenv("GREYSEARCH_CAPTCHA_SOLVER", raising=False)
    monkeypatch.delenv("GREYSEARCH_CAPTCHA_WAIT_S", raising=False)
    monkeypatch.delenv("GREYSEARCH_CAPTCHA_POLL_S", raising=False)
    yield


class _FakePage:
    """Minimal page for the capture contract: evaluate answers scripted
    captcha states in order (repeating the last), settles advance the
    scripted clock when one is set."""

    def __init__(self, states, body=""):
        self._states = list(states)
        self._n = 0
        self.sleeps_ms = []
        self.body = body
        self.url = "https://example.three/page"
        self.closed = False

    def goto(self, url, timeout=None, wait_until=None):
        return None

    def evaluate(self, js):
        if "g-recaptcha-response" not in js:
            return 0
        self._n += 1
        st = self._states[min(self._n - 1, len(self._states) - 1)]
        if st is None:
            raise RuntimeError("evaluate unavailable")
        return dict(st)

    def wait_for_timeout(self, ms):
        _CLOCK["t"] += ms / 1000.0
        self.sleeps_ms.append(ms)

    def locator(self, sel=""):
        return self

    def inner_text(self, timeout=None):
        return self.body

    def content(self):
        return ""

    def title(self):
        return "T"

    def close(self):
        self.closed = True


class _SfPage:
    """Page for the search-lane flow: the q-input fill succeeds, result
    items come from the scripted item list, and captcha states repeat."""

    def __init__(self, states, items=None):
        self._states = list(states)
        self._n = 0
        self.sleeps_ms = []
        self.items = items or []
        self.body = "gardens body"

    def evaluate(self, js):
        self._n += 1
        i = min(self._n - 1, len(self._states) - 1)
        st = self._states[i]
        if st is None:
            raise RuntimeError("evaluate unavailable")
        return dict(st)

    def wait_for_timeout(self, ms):
        self.sleeps_ms.append(ms)

    def goto(self, url, timeout=None, wait_until=None):
        self._n = 0

    def title(self):
        return "gardens"

    def wait_for_selector(self, sel, timeout=None):
        if not self.items:
            raise RuntimeError("no results rendered")
        return True

    def locator(self, sel):
        return _SfLoc(self, sel)


class _SfLoc:
    def __init__(self, pg, sel):
        self._pg, self._sel = pg, sel

    @property
    def first(self):
        return self

    def fill(self, s, timeout=None):
        pass

    def click(self, timeout=None):
        pass

    def count(self):
        return len(self._pg.items)

    def nth(self, i):
        return _SfItem(*self._pg.items[i])

    def inner_text(self, timeout=None):
        return self._pg.body

    def get_attribute(self, k):
        return None


class _SfItem:
    def __init__(self, title, href):
        self._title, self._href = title, href

    def locator(self, sel):
        return self

    @property
    def first(self):
        return self

    def inner_text(self, timeout=None):
        return self._title

    def get_attribute(self, k):
        return self._href


def test_cycle_solved_on_second_read_proceeds(monkeypatch):
    """The WAIT contract: a captcha'd page whose token arrives on the next
    evidence read leaves the cycle solved after exactly one settle, the
    mark names the waited seconds in the solved wording, and attribution
    keeps the read key-honest (marks never match a producing shape)."""
    _knobs(monkeypatch)
    pg = _FakePage([PRESENT, TOKEN])
    rec = bs.captcha_wait_cycle(pg)
    assert rec == {"engaged": True, "solved": True, "waited_s": 0.0}
    assert pg.sleeps_ms == [1]
    assert _SOLVED_RE.match(bs.captcha_mark(rec))
    assert bs.captcha_mark({"engaged": False, "solved": True,
                            "waited_s": 5.0}) == ""
    assert ri._engine_attribution(
        {"engines_tried": ["default+fullset"],
         "ladder": [bs.captcha_mark(rec)]}) == ["default+fullset"]


def test_cycle_exhausted_exactly_two_waits(monkeypatch):
    """A page that never solve-reports takes the bounded retry and no more:
    with wait 4 and poll 1 under the scripted clock the cycle runs ONE wait
    of four polls, ONE more of the same length, and returns the honest
    persisted record summing the two phase lengths."""
    _knobs(monkeypatch, wait_s="4", poll_s="1")
    _set_clock(monkeypatch)
    pg = _FakePage([PRESENT])
    rec = bs.captcha_wait_cycle(pg)
    assert rec == {"engaged": True, "solved": False, "waited_s": 8.0}
    assert pg.sleeps_ms == [1000] * 8
    assert bs.captcha_mark(rec) == _PERSISTED_MARK


def test_cycle_default_knobs_bench(monkeypatch):
    """Explicit-tunables bench with the documented defaults (30 s phase
    one, 60 s phase two, 5 s poll, instant-stubbed settles) per the
    documented 30-60 s contract: a solved token arrives inside one settle, an
    exhausted page consumes exactly eighteen settles and hits the
    documented worst case of the two phase lengths summed."""
    _set_clock(monkeypatch)
    pg = _FakePage([PRESENT, TOKEN])
    rec = bs.captcha_wait_cycle(pg)
    assert rec == {"engaged": True, "solved": True, "waited_s": 5.0}
    assert pg.sleeps_ms == [5000]
    pg2 = _FakePage([PRESENT])
    rec2 = bs.captcha_wait_cycle(pg2)
    assert rec2 == {"engaged": True, "solved": False, "waited_s": 90.0}
    assert pg2.sleeps_ms == [5000] * 18


def test_cycle_absent_state_engaged_false_no_sleeps():
    """An ordinary page never shows captcha state: no wait, no settle, no
    mark; an unreadable page degrades the same way."""
    pg = _FakePage([CLEAR])
    rec = bs.captcha_wait_cycle(pg)
    assert rec == {"engaged": False, "solved": False, "waited_s": 0.0}
    assert pg.sleeps_ms == []
    assert bs.captcha_mark(rec) == ""
    assert bs.captcha_state(
        type("P", (), {"evaluate": lambda self, js: None})()) is None


def test_cycle_zero_wait_knob_keeps_today_behavior(monkeypatch):
    """GREYSEARCH_CAPTCHA_WAIT_S=0 keeps today's behavior: a present
    captcha marks persisted immediately with no settle and no retry, and
    the record never claims a wait."""
    _knobs(monkeypatch, wait_s="0", poll_s="5")
    _set_clock(monkeypatch)
    pg = _FakePage([PRESENT])
    rec = bs.captcha_wait_cycle(pg)
    assert rec == {"engaged": True, "solved": False, "waited_s": 0.0}
    assert pg.sleeps_ms == []
    assert bs.captcha_mark(rec) == _PERSISTED_MARK


def test_cycle_token_counts_solved_even_with_anchor_mounted():
    """A filled token with no empty field and no interstitial is solve
    evidence even when the anchor iframe is still mounted (how a solved
    reCAPTCHA looks); a tokenless marker stays present."""
    mounted = {**CLEAR, "widget_iframes": 1}
    assert bs.captcha_present(PRESENT)
    assert bs.captcha_present(TOKEN)
    assert bs.captcha_solved(TOKEN)
    assert not bs.captcha_solved(PRESENT)
    assert not bs.captcha_solved(mounted)
    assert bs.captcha_solved(CLEAR)
    assert not bs.captcha_solved(None)
    assert not bs.captcha_present(None)


def test_marks_never_become_lane_keys():
    """Both mark wordings ride the ladder and stay inert for attribution:
    neither matches any producing shape."""
    for mark in ("captcha: solver waited 14s (solved)", _PERSISTED_MARK):
        assert ri._lane_keys_from_ladder(
            [mark, "searx.space browser: 4 results"]) == ["searx.space browser"]
        assert ri._engine_attribution(
            {"engines_tried": [], "ladder": [mark]}) == []


def test_kill_switch_wait_cycle_unchanged(monkeypatch):
    """The solver knob no longer gates any launch-time addon (the loader is
    gone from the launcher), so the knob's only wait-side contract is the
    one that never changed: the bounded two-tries cycle marks an
    never-lifted captcha persisted regardless of the knob's value."""
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_SOLVER", "on")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_S", "0.001")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_POLL_S", "0.001")
    _set_clock(monkeypatch)
    pg = _FakePage([PRESENT])
    rec = bs.captcha_wait_cycle(pg)
    assert rec == {"engaged": True, "solved": False, "waited_s": 0.0}
    assert bs.captcha_mark(rec) == _PERSISTED_MARK


def test_env_forward_reaches_launcher(monkeypatch, tmp_path):
    """The detached launch path carries the session's GREYSEARCH_* env so
    the solver knob resolves in the launcher process too, unrelated names
    are never forwarded, and the launcher the knob reaches loads nothing at
    launch time (no addon path in its source at all)."""
    cfg = {"key": "task-14", "port": 9394,
           "profile": str(tmp_path / "prof14")}
    opens = []

    def fake_popen(cmd, env=None, **k):
        opens.append({"cmd": list(cmd), "env": dict(env or {})})
        return types.SimpleNamespace(pid=1)

    monkeypatch.setenv("GREYSEARCH_CAPTCHA_SOLVER", "0")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_S", "3")
    monkeypatch.setenv("UNRELATED_KNOB", "never forwarded")
    monkeypatch.setattr(bp, "_healthy", lambda port: opens != [])
    monkeypatch.setattr(bp.subprocess, "Popen", fake_popen)
    assert bp.ensure(cfg, wait_s=5) is True
    assert opens, "ensure must launch when unhealthy"
    env = opens[0]["env"]
    assert env["GREYSEARCH_CAPTCHA_SOLVER"] == "0"
    assert env["GREYSEARCH_CAPTCHA_WAIT_S"] == "3"
    assert "PATH" in env and "HOME" in env
    assert "UNRELATED_KNOB" not in env
    assert opens[0]["cmd"] == bp.launch_cmd(cfg)
    assert bp.launch_cmd(cfg)[-4:] == [
        "--port", "9394", "--profile", cfg["profile"]]
    src = (Path(ph.__file__).resolve().parent
           / "patchright_headless.py").read_text().lower()
    assert "--load-extension" not in src


def _ctx_of(pg):
    class _Ctx:
        def new_page(self):
            return pg

    return _Ctx()


def test_fetch_text_persist_path_skips_honestly(monkeypatch):
    """The scrape-lane integration: a captcha'd page that never reports a
    token makes fetch_text fail with the exact honest line, the tab is
    closed, the persisted mark rides the record, and the shape mirrors
    today's nav-failed skip record (two bounded waits consumed)."""
    _knobs(monkeypatch, wait_s="2", poll_s="1")
    pg = _FakePage([PRESENT])
    monkeypatch.setattr(bs, "_connection", lambda: {"ctx": _ctx_of(pg)})
    res = bs.fetch_text("https://example.three/captcha-page")
    assert res["ok"] is False
    assert res["error"] == ("captcha persisted after solver wait + bounded "
                            "retry (waited 0s)")
    assert res["metadata"]["captcha"] == _PERSISTED_MARK
    assert res["lane"] == "cdp-slot"
    assert res["scrape_transport"] == bs._proxy_mode.scrape_guard_note()
    assert "text" not in res
    assert pg.closed is True
    assert pg.sleeps_ms[0] == 1200
    assert pg.sleeps_ms[1:] == [1000] * 4


def test_fetch_text_solved_path_proceeds_with_mark(monkeypatch):
    """A solved captcha leaves the fetch proceeding normally after exactly
    one settle: the waited mark rides the metadata and the body text
    extraction populates from the same page object exactly as before."""
    _knobs(monkeypatch, wait_s="2", poll_s="1")
    body = "Rain garden sizing starts with the drainage area. "
    states = [PRESENT, TOKEN, TOKEN]
    pg = _FakePage(states, body=body)
    monkeypatch.setattr(bs, "_connection", lambda: {"ctx": _ctx_of(pg)})
    monkeypatch.setattr(bs._rij, "sanitize_text", lambda t: (t, []))
    monkeypatch.setattr(bs, "_clean_text", lambda t: t.strip())
    res = bs.fetch_text("https://example.three/solved-page")
    assert res["ok"] is True
    assert res["metadata"]["captcha"] == "captcha: solver waited 0s (solved)"
    assert res["text"] == body.strip()
    assert res["status_code"] == 0
    assert res["elapsed_s"] >= 0
    assert pg.sleeps_ms == [1200, 1000]
    assert pg._n >= 2


def test_fetch_text_no_captcha_ordinary(monkeypatch):
    """An ordinary page with clear state keeps today's behavior: no waits,
    no mark, no metadata key, ok from the body text."""
    _knobs(monkeypatch, wait_s="20", poll_s="5")
    body = "Ordinary page body for the no-captcha case. "
    pg = _FakePage([CLEAR], body=body)
    monkeypatch.setattr(bs, "_connection", lambda: {"ctx": _ctx_of(pg)})
    monkeypatch.setattr(bs._rij, "sanitize_text", lambda t: (t, []))
    monkeypatch.setattr(bs, "_clean_text", lambda t: t.strip())
    res = bs.fetch_text("https://example.three/plain")
    assert res["ok"] is True
    assert res["text"] == body.strip()
    assert pg.sleeps_ms == [1200]
    assert "captcha" not in res["metadata"]


def _sf_lane(monkeypatch, pg):
    class _Ctx:
        def clear_cookies(self):
            pass

        @property
        def browser(self):
            return None

    monkeypatch.setattr(sf, "_tuning", types.SimpleNamespace(
        get=lambda *a, **k: 0.0, scaled=lambda *a, **k: 0.0))
    monkeypatch.setattr(sf, "pace_wait", lambda *_: None)
    monkeypatch.setattr(sf, "_warm_remote_host", lambda *a, **k: None)
    monkeypatch.setattr(sf, "ensure_instance_ready", lambda *a, **k: None)
    monkeypatch.setattr(sf, "_auth_settle", lambda *a, **k: None)
    monkeypatch.setattr(sf, "_gs_window", lambda: pg)
    monkeypatch.setattr(sf, "_GS_WINDOW",
                        {"proc": None, "cdp": None,
                         "ctx": _Ctx(), "page": None})


def test_sf_browser_lane_persist_holds_attempt(monkeypatch):
    """The search-lane contract: two gate bases that stay captcha'd each
    take one wait twice (the phase pair) then are held with the honest
    persisted line, the call returns no rows, and the holds list carries
    exactly two persisted marks from this attempt alone."""
    _knobs(monkeypatch, wait_s="1", poll_s="1")
    _clear_holds()
    sf._LAST_BROWSER_SEARCH = time.time()
    pg = _SfPage([PRESENT])
    _sf_lane(monkeypatch, pg)
    assert sf.browser_search_public("gardens", timeout=1) == []
    holds = sf._captcha_holds()
    assert holds == [_PERSISTED_MARK, _PERSISTED_MARK]
    assert pg.sleeps_ms.count(1000) == 4
    assert pg.sleeps_ms[:5] == [2000, 8000, 1000, 1000, 2000]


def test_sf_browser_lane_solved_proceeds(monkeypatch):
    """A base whose solver reports a token inside the wait leaves the lane
    proceeding normally into the result hunt and returning the rows, with
    the solved waited mark in the holds list and the per-base debug."""
    _knobs(monkeypatch, wait_s="1", poll_s="1")
    _clear_holds()
    sf._LAST_BROWSER_SEARCH = time.time()
    pg = _SfPage([PRESENT, TOKEN, TOKEN], items=[
        ("gardens one", "https://example.four/one"),
        ("gardens two", "https://example.four/two")])
    _sf_lane(monkeypatch, pg)
    monkeypatch.setattr(sf, "_result_is_junk", lambda q, r: False)
    monkeypatch.setattr(sf, "_result_relevant", lambda q, r: True)
    monkeypatch.setattr(sf, "_relevant", lambda q, out: bool(out))
    rows = sf.browser_search_public("gardens", timeout=1)
    assert [r["url"] for r in rows] == [
        "https://example.four/one", "https://example.four/two"]
    holds = sf._captcha_holds()
    assert len(holds) == 1
    assert _SOLVED_RE.match(holds[0])
    base = "https://search.hbubli.cc"
    assert _LAST_DEBUG_CAPTCHA(base) and holds[0] == _LAST_DEBUG_CAPTCHA(base)


def _LAST_DEBUG_CAPTCHA(base):
    return (sf._LAST_BROWSER_DEBUG.get(base) or {}).get("captcha") or ""


def _CAPTCHA_ROWS(monkeypatch, rows, marks):
    _clear_holds()

    def fake_browse(*a, **k):
        del sf._CAPTCHA_HOLDS[:]
        sf._CAPTCHA_HOLDS.extend(marks)
        return list(rows)

    monkeypatch.setattr(sf, "browser_search_public", fake_browse)


def _stub_ladder(monkeypatch):
    monkeypatch.setattr(sf, "_fetch",
                        lambda *a, **k: (_ for _ in ()).throw(
                            URLError("stubbed local lane")))
    monkeypatch.setattr(sf._proxy_mode, "prepared",
                        lambda *a, **k: {"ok": True, "mode": "off"})
    monkeypatch.setattr(sf._proxy_mode, "ladder_note",
                        lambda *a, **k: "proxy mode off: direct")


def test_sf_search_ladder_surfaces_marks(monkeypatch):
    """sf.search surfaces the marks for the attempt it made: the ladder is
    the proxy note, the captcha marks, then the static lane keys
    byte-for-byte."""
    monkeypatch.setattr(sf, "_harvest_instances", lambda **k: [])
    monkeypatch.setattr(sf, "_direct_search", lambda *a, **k: [])
    _stub_ladder(monkeypatch)
    _CAPTCHA_ROWS(monkeypatch,
                  [{"url": "https://example.five/a", "title": "gardens"}],
                  marks=["captcha: solver waited 9s (solved)"])
    res = sf.search("gardens")
    assert res["ok"] is True
    assert res["ladder"] == ["proxy mode off: direct",
                             "captcha: solver waited 9s (solved)",
                             "local", "searx.space-json",
                             "searx.space-browser"]


def test_sf_search_none_record_carries_marks(monkeypatch):
    """Marks from an attempted-but-barren browser lane ride the none
    record's ladder too, before the static keys, so a persisted lane is
    never silently dropped."""
    monkeypatch.setattr(sf, "_harvest_instances", lambda **k: [])
    monkeypatch.setattr(sf, "_direct_search", lambda *a, **k: [])
    _stub_ladder(monkeypatch)
    _CAPTCHA_ROWS(monkeypatch, [], marks=[
        _PERSISTED_MARK,
        "captcha: solver waited 3s (solved)",
        _PERSISTED_MARK])
    res = sf.search("gardens")
    assert res["ok"] is False
    assert res["source"] == "none"
    assert res["ladder"] == ["proxy mode off: direct", _PERSISTED_MARK,
                             "captcha: solver waited 3s (solved)",
                             _PERSISTED_MARK,
                             "local", "searx.space", "direct"]


def test_sf_search_held_back_never_surfaces_stale(monkeypatch):
    """A search where the browser lane is held back never surfaces stale
    marks: holds attach only after a call that ran."""
    monkeypatch.setenv("GREYSEARCH_LOCAL_SEARX", "")
    monkeypatch.setenv("GREYSEARCH_BROWSER_LANE", "0")
    monkeypatch.setattr(sf, "_harvest_instances", lambda **k: [])
    monkeypatch.setattr(sf, "_direct_search", lambda *a, **k: [])
    _stub_ladder(monkeypatch)
    _clear_holds()
    sf._CAPTCHA_HOLDS.extend(["captcha: solver waited 5s (solved)"])
    res = sf.search("gardens")
    assert not any(str(l).startswith("captcha") for l in res["ladder"])
    assert res["ladder"][0] == "proxy mode off: direct"
    assert "error" not in res
    del sf._CAPTCHA_HOLDS[:]


def test_sf_search_barren_attempt_only_mine(monkeypatch):
    """Two searches that each attempt: the first leaves a persisted mark,
    the second's attempt clears and answers rows with NONE, its ladder
    carries none (the holds never leak across attempts)."""
    monkeypatch.setattr(sf, "_harvest_instances", lambda **k: [])
    monkeypatch.setattr(sf, "_direct_search", lambda *a, **k: [])
    _stub_ladder(monkeypatch)
    marks = [_PERSISTED_MARK]
    _CAPTCHA_ROWS(monkeypatch, [], marks=marks)
    res1 = sf.search("gardens")
    assert res1["ladder"][1] == _PERSISTED_MARK
    _CAPTCHA_ROWS(monkeypatch, [{"url": "https://example.five/b",
                                 "title": "gardens"}], marks=[])
    res2 = sf.search("gardens")
    assert not any(str(l).startswith("captcha") for l in res2["ladder"])


def test_sf_cycle_wrapper_records_only_engaged():
    """The wrapper records the mark only when the cycle engaged: absent
    state adds nothing, an evaluate failure adds nothing, an engaged
    cycle's mark lands in the holds list once."""
    _clear_holds()
    pg = _SfPage([CLEAR])
    cyc, mark = sf._captcha_cycle(pg)
    assert cyc == {"engaged": False, "solved": False, "waited_s": 0.0}
    assert mark == "" and sf._captcha_holds() == []
    pg2 = _SfPage([None])
    cyc2, mark2 = sf._captcha_cycle(pg2)
    assert cyc2 == {"engaged": False, "solved": False, "waited_s": 0.0}
    assert sf._captcha_holds() == []
    pg3 = _SfPage([PRESENT], items=[])
    del pg3._states[:]
    pg3._states.append(None)
    pg3._n = 0
    pg3.evaluate = lambda js: dict(PRESENT)
    pg3.sleeps_ms = []
    pg3.wait_for_timeout = lambda ms: None
    cyc3, mark3 = sf._captcha_cycle(pg3)
    assert cyc3["engaged"] is True
    assert cyc3["solved"] is False
    assert sf._captcha_holds() == [_PERSISTED_MARK]
    del sf._CAPTCHA_HOLDS[:]


_SOLVED_RES = {"engaged": True, "provider": "recaptcha",
               "rungs": ["llm"], "rung": "llm", "rounds": 1,
               "solved": True, "latency_ms": {"llm": 12.5}, "error": None}
_SKIPPED_RES = {"engaged": True, "provider": "recaptcha",
                "rungs": ["llm"], "rung": "llm", "rounds": 1,
                "solved": False, "latency_ms": {"llm": 5.0},
                "error": "cap: rounds exhausted without a landed solve"}
_SOLVED_NOTE = "provider=recaptcha,rung=llm,rounds=1"
_SKIP_NOTE = "provider=recaptcha,rounds=1"
_TURNSTILE_RES = {"engaged": True, "provider": "turnstile",
                  "rungs": ["behavioral"], "rung": "behavioral",
                  "rounds": 1, "solved": True, "latency_ms": {},
                  "error": None}
_TURNSTILE_NOTE = "provider=turnstile,rung=behavioral,rounds=1"


def _solver_calls(monkeypatch, res):
    """Script the local solver's side from the test side: the three
    solver functions answer from the staged record and count their calls,
    so the hermetic lane tests never touch a real model or the network."""
    calls = []

    def _detect(page):
        calls.append("detect")
        return "recaptcha"

    def _solve(page, provider=None):
        calls.append("solve")
        return dict(res)

    def _turnstile(page):
        calls.append("turnstile")
        return dict(res)

    monkeypatch.setattr(cs, "detect_provider", _detect)
    monkeypatch.setattr(cs, "solve_challenge", _solve)
    monkeypatch.setattr(cs, "solve_turnstile", _turnstile)
    return calls


def _wired_pg(monkeypatch, pg):
    """Wire a fake page into fetch_text exactly the way the neighboring
    fetch-flow tests do."""
    monkeypatch.setattr(bs, "_connection", lambda: {"ctx": _ctx_of(pg)})
    monkeypatch.setattr(bs._rij, "sanitize_text", lambda t: (t, []))
    monkeypatch.setattr(bs, "_clean_text", lambda t: t.strip())


def test_solver_lifts_page_flow_proceeds_with_solved_note(monkeypatch):
    """Outcome 1: solver on and resolving, the wait exhausted anyway -
    fetch_text goes on with the page, the re-wait's own solved mark rides
    the metadata, and the solver's honest solved note names
    provider/rung/rounds; no persisted early-return happens."""
    _knobs(monkeypatch)
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MIN_S", "0.001")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_SOLVER", "on")
    _set_clock(monkeypatch)
    body = "Rain garden sizing starts with the drainage area. "
    pg = _FakePage([PRESENT, PRESENT, PRESENT, TOKEN, TOKEN], body=body)
    _wired_pg(monkeypatch, pg)
    calls = _solver_calls(monkeypatch, _SOLVED_RES)
    res = bs.fetch_text("https://example.three/solver-lifts")
    assert res["ok"] is True
    assert res["text"] == body.strip()
    assert res["metadata"]["captcha_solved"] == _SOLVED_NOTE
    assert res["metadata"]["captcha"] == "captcha: solver waited 0s (solved)"
    assert calls == ["detect", "solve"]
    assert not any("captcha persisted" in str(k) for k in
                   (res["error"],) + tuple(res["metadata"].values()))
    assert pg.sleeps_ms == [1200, 1, 1, 1]


def test_solver_no_resolve_keeps_persist_record_wording(monkeypatch):
    """Outcome 2: solver on but not resolving - the early-return keeps
    today's exact wording and the existing captcha key byte-identical,
    the solver's honest skip note rides alongside as a NEW metadata key,
    and the tab still closes on the persisted shape."""
    _knobs(monkeypatch)
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MIN_S", "0.001")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_SOLVER", "on")
    _set_clock(monkeypatch)
    pg = _FakePage([PRESENT])
    _wired_pg(monkeypatch, pg)
    calls = _solver_calls(monkeypatch, _SKIPPED_RES)
    res = bs.fetch_text("https://example.three/solver-skips")
    assert res["ok"] is False
    assert res["error"] == ("captcha persisted after solver wait + bounded "
                            "retry (waited 0s)")
    assert res["metadata"]["captcha"] == _PERSISTED_MARK
    assert res["metadata"]["captcha_skip"] == _SKIP_NOTE
    assert res["lane"] == "cdp-slot"
    assert "text" not in res
    assert pg.closed is True
    assert calls == ["detect", "solve"]


def test_solver_record_never_rides_when_it_never_engaged(monkeypatch):
    """The REAL solver against a fake page: a wall the solver cannot even
    detect a provider on leaves the persisted record exactly today's shape
    with no solver key at all (and no re-wait, no waits beyond the
    contract's own two phases)."""
    _knobs(monkeypatch)
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_SOLVER", "on")
    _set_clock(monkeypatch)
    pg = _FakePage([PRESENT])
    _wired_pg(monkeypatch, pg)
    res = bs.fetch_text("https://example.three/no-provider")
    assert res["ok"] is False
    assert res["error"] == ("captcha persisted after solver wait + bounded "
                            "retry (waited 0s)")
    assert res["metadata"] == {"captcha": _PERSISTED_MARK}
    assert pg.closed is True
    assert pg.sleeps_ms[1:] == [1, 1]


def test_solver_step_dispatches_turnstile_to_the_behavioral_rung(monkeypatch):
    """The dispatch arm: a wall the detector names turnstile takes the
    behavioral rung (solve_turnstile), never the capture ladder - and
    because the re-wait governs, not the solver's word, the lift hands back
    only after the bounded re-wait sees the challenge gone (the same
    standard a click-settled checkbox page meets), with the honest solver
    note naming the behavioral rung."""
    _knobs(monkeypatch)
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MIN_S", "0.001")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_SOLVER", "on")
    _set_clock(monkeypatch)
    pg = _FakePage([CLEAR])
    calls = []

    def _detect(page):
        calls.append("detect")
        return "turnstile"

    def _turnstile(p):
        calls.append("turnstile")
        return dict(_TURNSTILE_RES)

    def _solve(p, provider=None):
        calls.append("solve")
        return dict(_TURNSTILE_RES)

    monkeypatch.setattr(cs, "detect_provider", _detect)
    monkeypatch.setattr(cs, "solve_turnstile", _turnstile)
    monkeypatch.setattr(cs, "solve_challenge", _solve)
    rec, extra = bs.captcha_solver_step(
        pg, {"engaged": True, "solved": False, "waited_s": 1.0})
    assert calls == ["detect", "turnstile"]
    assert rec == {"engaged": True, "solved": True, "waited_s": 0.0}
    assert extra == {"captcha_solved": _TURNSTILE_NOTE}


def test_solver_off_fetch_flow_untouched_zero_calls(monkeypatch):
    """Outcome 3: solver off - not a single solver function is touched,
    and the persisted record equals today's full shape key for key."""
    _knobs(monkeypatch)
    _set_clock(monkeypatch)
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_SOLVER", "off")
    pg = _FakePage([PRESENT])
    _wired_pg(monkeypatch, pg)
    calls = _solver_calls(monkeypatch, _SOLVED_RES)
    res = bs.fetch_text("https://example.three/solver-off")
    assert calls == []
    assert res == {"ok": False, "url": "https://example.three/solver-off",
                   "error": ("captcha persisted after solver wait + bounded "
                             "retry (waited 0s)"),
                   "metadata": {"captcha": _PERSISTED_MARK},
                   "domain": "", "tier": "", "lane": "cdp-slot",
                   "scrape_transport": bs._proxy_mode.scrape_guard_note(),
                   "elapsed_s": 1.2}


def test_solver_step_governs_by_rewait_not_the_solvers_word(monkeypatch):
    """The step's own contract: a solver claim the bounded re-wait cannot
    see never lifts the lane (the skip note rides instead, naming
    provider and rounds); a re-wait that finds the token, or a page whose
    captcha evidence vanished altogether, lifts it exactly once and
    reports the lifted record the wait contract's own evidence shape
    supports; an already-solved or never-engaged cycle is passed back
    with no solver consultation at all."""
    _knobs(monkeypatch)
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MIN_S", "0.001")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_SOLVER", "on")
    _set_clock(monkeypatch)
    held = {"engaged": True, "solved": False, "waited_s": 3.0}
    pg = _FakePage([PRESENT])
    calls = _solver_calls(monkeypatch, _SOLVED_RES)
    rec, extra = bs.captcha_solver_step(pg, dict(held))
    assert rec == held
    assert extra == {"captcha_skip": _SKIP_NOTE}
    assert calls == ["detect", "solve"]
    # the re-wait's reads only: the page is handed to the step already
    # past its first cycle, so the states script just that re-wait
    pg2 = _FakePage([CLEAR])
    rec2, extra2 = bs.captcha_solver_step(pg2, dict(held))
    assert rec2 == {"engaged": True, "solved": True, "waited_s": 0.0}
    assert rec2 is not held
    assert extra2 == {"captcha_solved": _SOLVED_NOTE}
    pg3 = _FakePage([PRESENT, TOKEN, TOKEN])
    rec3, extra3 = bs.captcha_solver_step(pg3, dict(held))
    assert rec3 == {"engaged": True, "solved": True, "waited_s": 0.0}
    assert extra3 == {"captcha_solved": _SOLVED_NOTE}
    solved_cyc = {"engaged": True, "solved": True, "waited_s": 2.0}
    plain_cyc = {"engaged": False, "solved": False, "waited_s": 0.0}
    calls4 = _solver_calls(monkeypatch, _SOLVED_RES)
    rec4, extra4 = bs.captcha_solver_step(pg2, solved_cyc)
    assert rec4 is solved_cyc and extra4 == {}
    rec5, extra5 = bs.captcha_solver_step(pg2, plain_cyc)
    assert rec5 is plain_cyc and extra5 == {}
    assert calls4 == []


def test_solver_step_off_or_absent_never_consults(monkeypatch):
    """Kill switch off and a never-engaged cycle both pass the untouched
    record back with zero solver calls: the lane never consults a model
    unless the step is both wanted and allowed."""
    _knobs(monkeypatch)
    _set_clock(monkeypatch)
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_SOLVER", "off")
    held = {"engaged": True, "solved": False, "waited_s": 2.0}
    pg = _FakePage([PRESENT])
    calls = _solver_calls(monkeypatch, _SOLVED_RES)
    assert bs.captcha_solver_step(pg, dict(held)) == (held, {})
    assert calls == []
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_SOLVER", "on")
    assert bs.captcha_solver_step(
        pg, {"engaged": False, "solved": False, "waited_s": 0.0}) == (
        {"engaged": False, "solved": False, "waited_s": 0.0}, {})
    assert calls == []


def test_sf_browser_lane_solver_lifts_base(monkeypatch):
    """The search lane runs the same solver step: a base the bounded wait
    left captcha'd proceeds with the rows when the re-wait sees the lift,
    the first cycle's persisted mark stays in the holds list with the
    solver's solved note riding beside it, and the per-base debug carries
    the re-wait's solved mark."""
    _knobs(monkeypatch, wait_s="1", poll_s="1")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MIN_S", "1")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_SOLVER", "on")
    _clear_holds()
    sf._LAST_BROWSER_SEARCH = time.time()
    pg = _SfPage([PRESENT, PRESENT, PRESENT, TOKEN, TOKEN], items=[
        ("gardens one", "https://example.four/one"),
        ("gardens two", "https://example.four/two")])
    _sf_lane(monkeypatch, pg)
    monkeypatch.setattr(sf, "_result_is_junk", lambda q, r: False)
    monkeypatch.setattr(sf, "_result_relevant", lambda q, r: True)
    monkeypatch.setattr(sf, "_relevant", lambda q, out: bool(out))
    calls = _solver_calls(monkeypatch, _SOLVED_RES)
    rows = sf.browser_search_public("gardens", timeout=1)
    assert [r["url"] for r in rows] == [
        "https://example.four/one", "https://example.four/two"]
    assert calls == ["detect", "solve"]
    holds = sf._captcha_holds()
    assert holds == [_PERSISTED_MARK, f"captcha_solved: {_SOLVED_NOTE}"]
    assert _LAST_DEBUG_CAPTCHA("https://search.hbubli.cc") == (
        "captcha: solver waited 0s (solved)")
    assert pg.sleeps_ms.count(1000) == 3


def test_sf_browser_lane_solver_no_resolve_holds_with_skip_note(monkeypatch):
    """A lane base the solver could not resolve is held exactly as before
    with the persisted debug mark, and the honest skip note rides the
    holds list as its own line per gate base - the flow itself unchanged
    (no rows, both bases tried)."""
    _knobs(monkeypatch, wait_s="1", poll_s="1")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_WAIT_MIN_S", "1")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_SOLVER", "on")
    _clear_holds()
    sf._LAST_BROWSER_SEARCH = time.time()
    pg = _SfPage([PRESENT])
    _sf_lane(monkeypatch, pg)
    calls = _solver_calls(monkeypatch, _SKIPPED_RES)
    assert sf.browser_search_public("gardens", timeout=1) == []
    assert calls == ["detect", "solve", "detect", "solve"]
    base = "https://search.hbubli.cc"
    assert _LAST_DEBUG_CAPTCHA(base) == _PERSISTED_MARK
    holds = sf._captcha_holds()
    assert holds == [_PERSISTED_MARK, f"captcha_skip: {_SKIP_NOTE}",
                     _PERSISTED_MARK, f"captcha_skip: {_SKIP_NOTE}"]
    assert pg.sleeps_ms.count(1000) == 4


def test_sf_browser_lane_solver_off_holds_zero_calls(monkeypatch):
    """Solver off on the lane: zero solver calls and the holds list is
    exactly the two persisted marks of today's shape."""
    _knobs(monkeypatch, wait_s="1", poll_s="1")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_SOLVER", "off")
    _clear_holds()
    sf._LAST_BROWSER_SEARCH = time.time()
    pg = _SfPage([PRESENT])
    _sf_lane(monkeypatch, pg)
    calls = _solver_calls(monkeypatch, _SOLVED_RES)
    assert sf.browser_search_public("gardens", timeout=1) == []
    assert calls == []
    assert sf._captcha_holds() == [_PERSISTED_MARK, _PERSISTED_MARK]
