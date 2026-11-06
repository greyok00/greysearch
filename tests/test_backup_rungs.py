"""Backup rung tests: the archive-org direct search lane, the per-URL
wayback fallback for dead links (scrape side), and the google browser rung
in the fallback ladder, plus the honesty records they author and the
proxy-mode interplay. All stubbed: assertions inspect records and calls,
never traffic. The fake page + scripted clock from the captcha wait tests
are reused so the google rung's captcha path exercises the shipped
wait-retry machinery."""
import json
import time
import types
from urllib.parse import quote as _quote_up
from urllib.error import URLError

import pytest

import searx_fallback as sf
import patchright_scraper as ps
import browser_session as bs
import searxng_integrated as si
import research_integration as ri

from conftest import set_env_file
from test_captcha_wait import (_FakePage, _knobs, _set_clock, _clear_holds,
                               PRESENT, TOKEN)


_W3_QUERY = "rain garden sizing testsuite"


@pytest.fixture(autouse=True)
def _rung_hygiene():
    """Record lists are per-attempt state: every test starts with the rung
    record lists empty so no stale honesty line can leak across tests."""
    del sf._GOOGLE_LANE_RECORDS[:]
    del sf._ARCHIVE_LANE_RECORDS[:]
    del sf._CAPTCHA_HOLDS[:]
    yield
    del sf._GOOGLE_LANE_RECORDS[:]
    del sf._ARCHIVE_LANE_RECORDS[:]
    del sf._CAPTCHA_HOLDS[:]


def _json(payload):
    return json.dumps(payload).encode("utf-8")


class _Resp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body


class _FakeOpener:
    """_proxy_mode.open_search stub: substring-routed bodies or raises,
    everything recorded (url, timeout, mode, headers) for the riding tests."""

    def __init__(self, routes, default_raise=True):
        self.routes = routes
        self.default_raise = default_raise
        self.calls: list = []

    def open_search(self, url, timeout=15, mode=None, env_file=None,
                    headers=None, data=None):
        self.calls.append({"url": url, "timeout": timeout, "mode": mode,
                           "headers": headers, "env_file": env_file,
                           "data": data})
        body = None
        for key, route in self.routes.items():
            if key in url:
                body = route
                break
        if body is None:
            if self.default_raise:
                raise URLError(f"no route for {url[:60]}")
            return _Resp(b"")
        if isinstance(body, bytes):
            return _Resp(body)
        raise body


_ARCHIVE_DOCS = {
    "response": {"docs": [
        {"identifier": "rain-garden-book-1",
         "title": "rain garden sizing archive notes"},
        {"identifier": "rain-garden-book-2",
         "title": ["rain garden sizing archive", "part two"]},
        {"identifier": "rain-garden-book-3",
         "title": "rain garden sizing archive notes three"},
        {"identifier": "rain-garden-book-4",
         "title": "rain garden sizing archive notes four"},
        {"identifier": "rain-garden-book-5",
         "title": "rain garden sizing archive notes five"},
    ]},
}


def test_archive_lane_adds_details_rows_and_mode_rides(monkeypatch):
    """The fourth direct lane: one advancedsearch.php attempt per search
    call, details URLs built from identifiers (list titles joined), and the
    mode the caller threaded is delivered to every open_search hop."""
    opener = _FakeOpener({"archive.org/advancedsearch.php": _json(_ARCHIVE_DOCS)})
    monkeypatch.setattr(sf, "_proxy_mode", opener)
    mode = {"mode": "on"}
    out = sf._direct_search(_W3_QUERY, max_results=10, mode=mode)
    arch = [r for r in out if r.get("source") == "archive-org"]
    assert [r["url"] for r in arch] == [
        "https://archive.org/details/rain-garden-book-1",
        "https://archive.org/details/rain-garden-book-2",
        "https://archive.org/details/rain-garden-book-3",
        "https://archive.org/details/rain-garden-book-4",
        "https://archive.org/details/rain-garden-book-5"]
    assert arch[1]["title"] == "rain garden sizing archive part two"
    assert sf.archive_lane_records() == []
    assert all(c["mode"] is mode for c in opener.calls if c["mode"] is not None)
    hops = [c for c in opener.calls
            if "archive.org/advancedsearch.php" in c["url"]]
    assert len(hops) == 1
    assert hops[0]["url"] == ("https://archive.org/advancedsearch.php?q="
                              + _quote_up(_W3_QUERY)
                              + "&output=json&rows=10")
    keys = ri._lane_keys_from_ladder(["direct keyless: +5 (archive-org)"])
    assert keys[0] == "direct keyless" and keys[1] == "archive-org"
    keys2 = ri._lane_keys_from_ladder(
        ["direct keyless: +7 (ddg-html, archive-org, github-api)"])
    assert "archive-org" in keys2


def test_archive_lane_obeys_max_results_and_empty_hits(monkeypatch):
    """The rows bound and the honest barren line: rows= rides max_results,
    an empty doc list states '0 candidates' and attribution keeps silent
    because no +N record can exist for it."""
    opener = _FakeOpener({"archive.org/advancedsearch.php": _json(_ARCHIVE_DOCS)})
    monkeypatch.setattr(sf, "_proxy_mode", opener)
    out = sf._direct_search(_W3_QUERY, max_results=1)
    assert [(r["url"], r["source"]) for r in out] == [
        ("https://archive.org/details/rain-garden-book-1", "archive-org")]
    assert sf.archive_lane_records() == []
    opener2 = _FakeOpener(
        {"archive.org/advancedsearch.php": _json({"response": {"docs": []}})})
    monkeypatch.setattr(sf, "_proxy_mode", opener2)
    out2 = sf._direct_search(_W3_QUERY, max_results=5)
    assert out2 == []
    recs = sf.archive_lane_records()
    assert recs == ["archive-org: 0 candidates"]
    assert ri._lane_keys_from_ladder(recs) == []


def test_archive_lane_failure_is_honest_and_never_fatal(monkeypatch):
    """A failed catalog attempt records the class and the call stays a
    skipped lane: out stays empty and no attribution shape matches the
    failure line."""
    opener = _FakeOpener({})
    monkeypatch.setattr(sf, "_proxy_mode", opener)
    out = sf._direct_search(_W3_QUERY, max_results=5)
    assert out == []
    recs = sf.archive_lane_records()
    assert len(recs) == 1
    assert recs[0].startswith("archive-org: tried 1 catalog attempt, failed (")
    assert "(URLError:" in recs[0]
    assert ri._lane_keys_from_ladder(recs) == []


def test_archive_lanes_skip_when_rows_filled_by_earlier_lanes(monkeypatch):
    """When an earlier direct lane already filled the budget the archive
    rung engages nowhere: the honest not-engaged line and no archive hop at
    the opener at all."""
    anchors = ("".join(
        f'<a class="result__a" href="https://example.org/rain-garden-sizing-{i}">'
        f"rain garden sizing guide {i}</a>" for i in range(12))).encode(
            "utf-8")
    opener = _FakeOpener({"html.duckduckgo.com": anchors})
    monkeypatch.setattr(sf, "_proxy_mode", opener)
    out = sf._direct_search(_W3_QUERY, max_results=10)
    assert len(out) == 10
    assert all(r["source"] == "ddg-html" for r in out)
    assert sf.archive_lane_records() == [
        "archive-org: not engaged (rows already filled by earlier lanes)"]
    assert not [c for c in opener.calls if "archive.org" in c["url"]]
    assert ri._lane_keys_from_ladder(sf.archive_lane_records()) == []


def test_archive_lane_records_cleared_per_call(monkeypatch):
    """Records from a failed attempt do not survive into the next call's
    honesty lines (a fresh search starts with an empty record list)."""
    opener = _FakeOpener({})
    monkeypatch.setattr(sf, "_proxy_mode", opener)
    sf._direct_search(_W3_QUERY, max_results=5)
    assert sf.archive_lane_records()
    monkeypatch.setattr(sf, "_proxy_mode", _FakeOpener(
        {"archive.org/advancedsearch.php": _json(_ARCHIVE_DOCS)}))
    out = sf._direct_search(_W3_QUERY, max_results=3)
    assert [r["source"] for r in out] == ["archive-org"] * 3
    assert sf.archive_lane_records() == []


# ---- per-URL wayback fallback (scrape side) -------------------------------

def test_wayback_rescue_returns_archived_copy_with_provenance(monkeypatch):
    """A gated-status dead URL consults the availability API through the
    threaded mode, and a snapshot comes back as the ARCHIVED fetched copy:
    the source url stays the original, the note names 'via wayback', lane
    and domain carry provenance, metadata keeps the timestamp, and the
    scrape guard stays the unchanged direct line."""
    orig = "https://www.example-site.org/rain-garden-sizing.html"
    archived = ("https://web.archive.org/web/20250101000000/"
                "https://www.example-site.org/rain-garden-sizing.html")
    consults: list = []

    def fake_avail(url, mode=None):
        consults.append({"url": url, "mode": mode})
        return {"available": "true", "timestamp": "20250101000000",
                "url": archived}

    monkeypatch.setattr(ps, "wayback_available", fake_avail)
    fetched: list = []

    def fake_fetch(url, *a, **k):
        fetched.append(url)
        return {"ok": True, "url": url, "final_url": url,
                "title": "archived copy", "text": "archived body",
                "raw_text": "archived body", "html": "",
                "html_capped": False, "metadata": {"a": 1},
                "status_code": 200, "chars": 14,
                "links": [], "images": [], "videos": [], "tables": [],
                "headings": [], "code_blocks": [], "audios": [],
                "pdfs": [], "canvases": [], "iframes": [],
                "injection_flags": [], "injection_scanned": True,
                "elapsed_s": 0.2}

    monkeypatch.setattr(bs, "fetch_text", fake_fetch)
    mode = {"mode": "on"}
    resc, note = ps.wayback_rescue(orig, dead_status=404, mode=mode)
    assert note == "" and resc is not None
    assert resc["ok"] is True and resc["text"] == "archived body"
    assert resc["url"] == orig
    assert resc["via_wayback"] == ("via wayback: " + archived
                                   + " (timestamp 20250101000000)")
    assert resc["lane"] == "cdp-slot+wayback"
    assert resc["domain"] == "example-site.org"
    assert resc["metadata"]["via_wayback"] == archived
    assert resc["metadata"]["wayback_timestamp"] == "20250101000000"
    assert resc["scrape_transport"] == _proxy_guard()
    assert consults == [{"url": orig, "mode": mode}]
    assert fetched == [archived]


def _proxy_guard():
    return ps._proxy_mode.scrape_guard_note()


def test_wayback_available_builds_the_search_class_hop(monkeypatch):
    """The availability hop is a SEARCH-class request through the
    opener contract: it composes the wayback API URL on its own, carries
    the threaded mode, and the timeout is bounded by the knob."""
    calls: list = []

    class _Rec:
        def open_search(self, url, timeout=15, mode=None, env_file=None,
                        headers=None, data=None):
            calls.append({"url": url, "timeout": timeout, "mode": mode,
                          "headers": headers})
            return _Resp(_json({"archived_snapshots": {"closest": {
                "available": "true", "timestamp": "20241231",
                "url": "https://web.archive.org/web/20241231/x"}}}))

    monkeypatch.setattr(ps, "_proxy_mode", _Rec())
    snap = ps.wayback_available("https://x.example/rain garden",
                                mode={"mode": "on"})
    assert snap["timestamp"] == "20241231"
    assert calls == [{"url": ps._WAYBACK_AVAIL_BASE
                      + _quote_up("https://x.example/rain garden", safe=""),
                      "timeout": 12.0, "mode": {"mode": "on"},
                      "headers": ps._WAYBACK_UA}]

    def raising(url, timeout=15, mode=None, env_file=None, headers=None,
                data=None):
        raise URLError("api unreachable")

    monkeypatch.setattr(ps, "_proxy_mode", _Rec())
    monkeypatch.setattr(ps._proxy_mode, "open_search", raising)
    got = ""
    try:
        ps.wayback_available("https://x.example/y", mode={"mode": "off"})
    except Exception as e:
        got = f"{type(e).__name__}: {e}"
    assert got.startswith("URLError")
    assert "api unreachable" in got


def test_wayback_miss_states_the_honest_line(monkeypatch):
    """A walled-out availability answer is a miss, never a silent skip:
    (None, note) with the archived-attempt story named."""
    monkeypatch.setattr(ps, "wayback_available", lambda url, mode=None: None)
    resc, note = ps.wayback_rescue("https://miss.example/rain-garden",
                                   dead_status=410)
    assert resc is None and note == "miss (no archived copy)"


def test_wayback_unavailable_carries_the_class_and_one_attempt(monkeypatch):
    """An unavailable API raises through the consult and carries the
    exception class in the note; the SAME dead URL never consults twice
    (the second dead decision is the one-attempt memo's own honest line)."""
    n = {"c": 0}

    def raising(url, mode=None):
        n["c"] += 1
        raise RuntimeError("api down")

    monkeypatch.setattr(ps, "wayback_available", raising)
    dead_error = "navigation failed: PlaybackError: connection refused (2)"
    resc, note = ps.wayback_rescue("https://downapi.example/page",
                                   dead_error=dead_error)
    assert resc is None
    assert note == "unavailable (RuntimeError: api down)"
    resc2, note2 = ps.wayback_rescue("https://downapi.example/page",
                                     dead_error=dead_error)
    assert resc2 is None
    assert note2 == "skipped (already tried this run)"
    assert n["c"] == 1


def test_wayback_walls_and_gates_never_consult(monkeypatch):
    """Access walls (403/429) are not dead and never consult; an
    already-archived URL never consults (no recursion); the kill switch
    and the wall cap each state their own skipped line."""
    def boom(url, mode=None):
        raise AssertionError("consulted while gated")

    monkeypatch.setattr(ps, "wayback_available", boom)
    assert ps.wayback_rescue("https://wall.example/x",
                             dead_status=403) == (None, "")
    assert ps.wayback_rescue("https://wall.example/x",
                             dead_status=429) == (None, "")
    assert ps.wayback_rescue("https://wall.example/x",
                             dead_error="") == (None, "")
    archived_only = "https://web.archive.org/web/20250101/https://x.example/"
    assert ps.wayback_rescue(archived_only, dead_status=404) == (
        None, "skipped (already an archived copy)")
    monkeypatch.setenv("GREYSEARCH_WAYBACK", "0")
    assert ps.wayback_rescue("https://off.example/rain-garden-sizing",
                             dead_status=404) == (
        None, "skipped (wayback engine off)")
    monkeypatch.setenv("GREYSEARCH_WAYBACK", "1")
    monkeypatch.setenv("GREYSEARCH_WAYBACK_MAX_S", "0")
    assert ps.wayback_rescue("https://capped.example/rain-garden-sizing",
                             dead_status=404) == (
        None, "skipped (wayback wall cap)")


def test_wayback_gate_statuses():
    """Dead means gone: gateway failures and gone content; walls, rate
    limits and slow responses are untouched by the fallback."""
    for st in (404, 410, 500, 502, 503, 504):
        assert ps.wayback_dead_status(st) is True
    for st in (0, 200, 301, 401, 403, 407, 408, 429, 451):
        assert ps.wayback_dead_status(st) is False


class _FakeNavFailPg:
    """Slot-page stand-in whose goto raises a dead navigation error; every
    other call the fetch core needs after a return is absent on purpose."""

    def __init__(self, reason):
        self.reason = reason
        self.closed = False

    def set_default_timeout(self, timeout):
        pass

    def goto(self, url, timeout=None, wait_until=None):
        raise RuntimeError(self.reason)

    def close(self):
        self.closed = True

    def url(self):
        return ""

    def title(self):
        return ""


class _FakeGonePg:
    """Slot-page stand-in whose goto answers a page-gone status."""

    def __init__(self, status):
        self._status = status
        self.closed = False

    def set_default_timeout(self, timeout):
        pass

    def goto(self, url, timeout=None, wait_until=None):
        return types.SimpleNamespace(status=self._status)

    def close(self):
        self.closed = True

    def title(self):
        return ""

    def url(self):
        return "https://gone.example/x"

    def evaluate(self, js):
        return ""

    def wait_for_timeout(self, ms):
        pass


class _FakeCtx:
    def __init__(self, pg):
        self._pg = pg

    def new_page(self):
        return self._pg


def test_bs_fetch_text_navfail_consults_wayback_mode_rides(monkeypatch):
    """The browser fetch decision point: a dead navigation consults
    wayback_rescue with the honest error text and the threaded mode, and a
    rescue returns the ARCHIVED copy as the fetch result (ok, provenance)."""
    dead = "https://deadnav.example/rain-garden-sizing"
    resc = {"ok": True, "url": dead, "via_wayback": "via wayback: x",
            "lane": "cdp-slot+wayback", "domain": "deadnav.example"}
    got: list = []

    def fake_rescue(url, dead_status=0, dead_error="", mode=None):
        got.append({"url": url, "dead_status": dead_status,
                    "dead_error": dead_error, "mode": mode})
        return resc, "would-be note"

    monkeypatch.setattr(bs, "wayback_rescue", fake_rescue)
    pg = _FakeNavFailPg("PlaybackError: connection refused (2)")
    monkeypatch.setattr(bs, "_connection", lambda: {"ctx": _FakeCtx(pg)})
    mode = {"mode": "on"}
    r = bs.fetch_text(dead, mode=mode)
    assert got == [{"url": dead, "dead_status": 0,
                    "dead_error": ("navigation failed: RuntimeError: "
                                   "PlaybackError: connection refused (2)"),
                    "mode": mode}]
    assert r is resc
    assert resc["ok"] is True


def test_bs_fetch_text_navfail_miss_is_honest_not_silent(monkeypatch):
    """A wayback miss keeps the ORIGINAL error text byte-exact and adds the
    miss as its own separate wayback note on the same record."""
    dead = "https://deadmiss.example/rain-garden-sizing"

    def fake_rescue(url, dead_status=0, dead_error="", mode=None):
        return None, "miss (no archived copy)"

    monkeypatch.setattr(bs, "wayback_rescue", fake_rescue)
    pg = _FakeNavFailPg("PlaybackError: connection refused (2)")
    monkeypatch.setattr(bs, "_connection", lambda: {"ctx": _FakeCtx(pg)})
    r = bs.fetch_text(dead)
    assert r["ok"] is False
    assert r["error"] == ("navigation failed: RuntimeError: PlaybackError: "
                          "connection refused (2)")
    assert r["wayback"] == "miss (no archived copy)"
    assert r["lane"] == "cdp-slot"


def test_bs_fetch_text_gone_status_consults_wayback(monkeypatch):
    """A 404 the slot browser DID see is dead too: the consult carries the
    status, a rescued copy comes back stamped with the ORIGINAL dead
    status in metadata, and a miss states 'page gone' with its own wayback
    note instead of pretending the scrape succeeded."""
    dead = "https://gone.example/rain-garden-sizing"
    got: list = []

    def fake_rescue(url, dead_status=0, dead_error="", mode=None):
        got.append({"url": url, "dead_status": dead_status,
                    "mode": mode})
        return ({"ok": True, "url": url, "text": "archived body",
                 "metadata": {"a": 1}}, "")

    monkeypatch.setattr(bs, "wayback_rescue", fake_rescue)
    pg = _FakeGonePg(404)
    monkeypatch.setattr(bs, "_connection", lambda: {"ctx": _FakeCtx(pg)})
    r = bs.fetch_text(dead)
    assert got == [{"url": dead, "dead_status": 404, "mode": None}]
    assert r["ok"] is True and r["text"] == "archived body"
    assert r["metadata"]["wayback_dead_status"] == 404

    def fake_rescue_miss(url, dead_status=0, dead_error="", mode=None):
        return None, "miss (no archived copy)"

    monkeypatch.setattr(bs, "wayback_rescue", fake_rescue_miss)
    pg2 = _FakeGonePg(503)
    monkeypatch.setattr(bs, "_connection", lambda: {"ctx": _FakeCtx(pg2)})
    r2 = bs.fetch_text(dead)
    assert r2["ok"] is False
    assert r2["error"] == "http 503: page gone"
    assert r2["wayback"] == "miss (no archived copy)"


def test_research_scrape_wayback_passthroughs(monkeypatch):
    """research_scrape carries the wayback story both ways: a miss composes
    the original error plus the '; wayback: <note>' suffix on the skip
    record, a rescue's provenance rides the result, and the mode is
    threaded into fetch_url with the TypeError guard staying safe."""
    agent = ri.ResearchScraperBridge.__new__(ri.ResearchScraperBridge)
    agent.excluded_domains = set()
    agent._last_domain_hit = {}
    agent.sites_data = {"categories": {}}
    agent._politeness_wait = lambda *a, **k: None
    agent.scraper = types.SimpleNamespace()
    mode = {"mode": "on"}

    def fake_fetch(url, mode=None):
        return {"ok": False, "url": url, "error": "http 404: page gone",
                "wayback": "miss (no archived copy)"}

    agent.scraper.fetch_url = fake_fetch
    r = agent.research_scrape("https://gone.example/rain-garden", mode=mode)
    assert r["ok"] is False
    assert r["error"] == ("http 404: page gone; wayback: "
                          "miss (no archived copy)")

    def fake_fetch_resc(url, mode=None):
        return {"ok": True, "url": url, "final_url": url, "title": "t",
                "text": "body", "raw_text": "body", "html": "",
                "html_capped": False, "links": [], "images": [],
                "videos": [], "audios": [], "pdfs": [], "canvases": [],
                "iframes": [], "meta": {}, "status_code": 200,
                "tables": [], "headings": [], "code_blocks": [],
                "injection_flags": [], "injection_scanned": True,
                "elapsed_s": 0.1,
                "via_wayback": "via wayback: x (timestamp 1)"}

    agent.scraper.fetch_url = fake_fetch_resc
    r2 = agent.research_scrape("https://resc.example/rain-garden", mode=mode)
    assert r2["ok"] is True
    assert r2["via_wayback"] == "via wayback: x (timestamp 1)"


# ---- google browser rung --------------------------------------------------

def _gpage():
    class _GP:
        def __init__(self):
            self.gotos: list = []
            self.evaluate_calls: list = []
            self.closed = False

        def goto(self, url, timeout=None, wait_until=None):
            self.gotos.append(url)
            return None

        def evaluate(self, js):
            self.evaluate_calls.append(js)
            return []

        def title(self):
            return "g"

        def url(self):
            return self.gotos[-1] if self.gotos else ""

        def locator(self, sel=""):
            return self

        def inner_text(self, timeout=None):
            return ""

        def content(self):
            return ""

        def wait_for_timeout(self, ms):
            pass

        def close(self):
            self.closed = True

    return _GP()


def _gprep(monkeypatch, links=None, cycle=None):
    monkeypatch.setattr(sf, "settle_first_contact", lambda page: None)
    monkeypatch.setattr(
        sf, "_captcha_cycle", lambda page: (dict(cycle or {
            "engaged": False, "solved": False, "waited_s": 0.0}), "")
    )
    got_eval: list = []

    def fake_eval(page, js, timeout_s=0.0):
        got_eval.append(js)
        return list(links or [])

    monkeypatch.setattr(sf, "eval_with_timeout", fake_eval)
    return got_eval


def test_google_rung_attempts_google_only_through_the_slot_browser(monkeypatch):
    """The final browser base: one google.com/search goto, the existing
    settle + captcha cycle run before extraction, google ad URLs filtered,
    and delivered rows carry the google tag while the records stay empty."""
    links = [
        {"href": "https://one.example/rain-garden-sizing",
         "text": "rain garden sizing one"},
        {"href": "https://www.google.com/aclk?sa=l&ai=1",
         "text": "sponsored result"},
        {"href": "https://www.googleadservices.com/pagead/click",
         "text": "tracked click"},
        {"href": "https://two.example/rain-garden-sizing",
         "text": "rain garden sizing two"},
        {"href": "not-a-url", "text": "skip me"},
        {"href": "https://dup.example/rain-garden-sizing",
         "text": "rain garden sizing dup"},
        {"href": "https://dup.example/rain-garden-sizing",
         "text": "rain garden sizing dup again"},
    ]
    monkeypatch.setattr(sf, "_result_relevant", lambda q, r: True)
    got_eval = _gprep(monkeypatch, links=links)
    pg = _gpage()
    ctx = types.SimpleNamespace(clear_cookies=lambda: None)
    del sf._GOOGLE_LANE_RECORDS[:]
    out: list = []
    ok = sf._google_rung(_W3_QUERY, 10, pg, ctx, out, time.time() + 9)
    assert ok is True and len(out) == 3
    assert [(r["url"], r["engine"]) for r in out] == [
        ("https://one.example/rain-garden-sizing",
         "https://www.google.com"),
        ("https://two.example/rain-garden-sizing",
         "https://www.google.com"),
        ("https://dup.example/rain-garden-sizing",
         "https://www.google.com")]
    assert out[0]["content"] == ""
    assert pg.gotos == ["https://www.google.com/search?q="
                        + _quote_up(_W3_QUERY) + "&num=10"]
    assert got_eval == [sf._GOOGLE_LINKS_JS]
    assert sf.google_lane_records() == []
    assert pg.evaluate_calls == []


def test_google_rung_not_engaged_lines_and_no_attempt(monkeypatch):
    """The three honest no-attempt classes: earlier bases delivered rows,
    the browser-lane gate is off, and the lane cap already burned this
    call. No goto runs for any of them."""
    _gprep(monkeypatch)
    pg = _gpage()
    ctx = types.SimpleNamespace(clear_cookies=lambda: None)
    out = [{"url": "https://earlier.example/x"}]
    ok = sf._google_rung(_W3_QUERY, 10, pg, ctx, out, time.time() + 9)
    assert ok is False and len(out) == 1 and pg.gotos == []
    assert sf.google_lane_records() == [
        "google browser: not engaged (earlier bases delivered)"]
    del sf._GOOGLE_LANE_RECORDS[:]
    monkeypatch.setenv("GREYSEARCH_BROWSER_LANE", "0")
    ok2 = sf._google_rung(_W3_QUERY, 10, pg, ctx, [], time.time() + 9)
    assert ok2 is False and pg.gotos == []
    assert sf.google_lane_records() == [
        "google browser: not engaged (browser lane off)"]
    monkeypatch.setenv("GREYSEARCH_BROWSER_LANE", "1")
    del sf._GOOGLE_LANE_RECORDS[:]
    ok3 = sf._google_rung(_W3_QUERY, 10, pg, ctx, [], 0.0)
    assert ok3 is False and pg.gotos == []
    assert sf.google_lane_records() == [
        "google browser: not engaged (lane cap reached)"]


def test_google_rung_barren_and_failed_honest(monkeypatch):
    """A google page that yields no rows keeps the lane non-fatal with the
    honest barren line; a failed attempt carries the exception class."""
    got_eval = _gprep(monkeypatch, links=[])
    pg = _gpage()
    ctx = types.SimpleNamespace(clear_cookies=lambda: None)
    out: list = []
    ok = sf._google_rung(_W3_QUERY, 10, pg, ctx, out, time.time() + 9)
    assert ok is False and out == []
    assert sf.google_lane_records() == ["google browser: 0 results"]
    assert got_eval == [sf._GOOGLE_LINKS_JS]

    def raising_goto(url, timeout=None, wait_until=None):
        raise TimeoutError("timeout wall")

    pg2 = _gpage()
    pg2.goto = raising_goto
    del sf._GOOGLE_LANE_RECORDS[:]
    ok2 = sf._google_rung(_W3_QUERY, 10, pg2, ctx, out, time.time() + 9)
    assert ok2 is False and out == []
    recs = sf.google_lane_records()
    assert recs == ["google browser: failed/bound-out (TimeoutError: "
                    "timeout wall)"]


def test_google_rung_captcha_held_uses_the_real_machinery(monkeypatch):
    """A google page that stays captcha'd exhausts the REALLY shipped
    wait-retry cycle (the captcha-wait machinery, no new marking), leaves the
    attempt held, and states the held line; the persisted holds from that
    cycle land in the holds list as before. Extraction never runs."""
    _set_clock(monkeypatch)
    _knobs(monkeypatch, wait_s="1", poll_s="1")
    _clear_holds()
    monkeypatch.setattr(sf, "settle_first_contact", lambda page: None)
    pg = _FakePage([PRESENT])
    del sf._GOOGLE_LANE_RECORDS[:]
    out: list = []
    ok = sf._google_rung(_W3_QUERY, 10, pg,
                         types.SimpleNamespace(clear_cookies=lambda: None),
                         out, time.time() + 9)
    assert ok is False and out == []
    assert sf.google_lane_records() == [
        "google browser: 0 results (captcha held)"]
    assert sf._captcha_holds() == [
        "captcha: persisted (solver wait + retry exhausted)"]
    assert pg.sleeps_ms.count(1000) == 2


def test_google_rung_solved_attempt_proceeds(monkeypatch):
    """When the solver reports a token inside the wait the cycle leaves
    the attempt solved and the rung proceeds into extraction like any
    other base."""
    _set_clock(monkeypatch)
    _knobs(monkeypatch)
    _clear_holds()
    monkeypatch.setattr(sf, "settle_first_contact", lambda page: None)
    pg = _FakePage([PRESENT, TOKEN])
    monkeypatch.setattr(sf, "_result_is_junk", lambda q, r: False)
    monkeypatch.setattr(sf, "_result_relevant", lambda q, r: True)
    monkeypatch.setattr(sf, "eval_with_timeout",
                        lambda page, js, timeout_s=0: [
                            {"href": "https://sol.example/rain-garden-sizing",
                             "text": "rain garden sizing solved"}])
    del sf._GOOGLE_LANE_RECORDS[:]
    out: list = []
    ok = sf._google_rung(_W3_QUERY, 10, pg,
                         types.SimpleNamespace(clear_cookies=lambda: None),
                         out, time.time() + 9)
    assert ok is True
    assert [(r["url"], r["engine"]) for r in out] == [
        ("https://sol.example/rain-garden-sizing",
         "https://www.google.com")]
    assert sf.google_lane_records() == []


# ---- proxy interplay + ladder surface -------------------------------------

def _manager(monkeypatch, tmp_path):
    monkeypatch.setenv("GREYSEARCH_SKIP_LOCAL", "1")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    set_env_file(monkeypatch, tmp_path / "absent.env")
    cache_path = tmp_path / "engine-status.json"
    cache_path.write_text(json.dumps(
        {"at": time.time(), "engines": {"wikipedia": {"working": True}}}))
    monkeypatch.setattr(si, "_engine_cache_path", lambda: cache_path)
    monkeypatch.setattr(si, "_local_alive", lambda *a, **k: True)
    monkeypatch.setattr(si.urllib.request, "urlopen", _fail_urlopen)


def _fail_urlopen(req, timeout=None, **k):
    pytest.fail("a search query ran where none was allowed: "
                f"{getattr(req, 'full_url', '')}")


def _stub_fallback_w3(monkeypatch, browser_rows=None, direct_rows=None,
                      google_recs=None, archive_recs=None):
    def factory():
        def no_browser(*a, **k):
            return list(browser_rows or [])

        rows = direct_rows if direct_rows is not None else []
        return types.SimpleNamespace(
            browser_search_public=no_browser,
            _direct_search=lambda *a, **k: list(rows),
            _warm_remote_host=lambda *a, **k: None,
            _filter_instance_junk=lambda q, r, *a, **k: list(r),
            _captcha_holds=lambda: [],
            google_lane_records=lambda: list(google_recs or []),
            archive_lane_records=lambda: list(archive_recs or []),
        )

    monkeypatch.setattr(si, "_searx_fallback", factory)


def _mgr_ready(monkeypatch, tmp_path):
    _manager(monkeypatch, tmp_path)
    mgr = si.SearXNGEngineManager()
    monkeypatch.setattr(mgr, "_discover_public_instances",
                        lambda max_instances=6, mode=None: [])
    return mgr


def test_si_ladder_relays_google_and_archive_records(monkeypatch, tmp_path):
    """The surface-1 ladder: delivered google rows merge with a produced
    record, the direct +N record names the archive lane on its producing
    line, and the rung honesty lines follow the lane they belong to. The
    attribution names both new keys in first-seen order."""
    mgr = _mgr_ready(monkeypatch, tmp_path)
    gtag = si._GOOGLE_BROWSER_TAG
    _stub_fallback_w3(
        monkeypatch,
        browser_rows=[
            {"title": "rain garden sizing one",
             "url": "https://one.example/rain-garden-sizing",
             "content": "gardens", "engine": gtag},
            {"title": "rain garden sizing two",
             "url": "https://two.example/rain-garden-sizing",
             "content": "gardens", "engine": gtag},
        ],
        direct_rows=[
            {"url": "https://archive.org/details/rg-one",
             "title": "rain garden sizing archive", "source": "archive-org"},
            {"url": "https://archive.org/details/rg-two",
             "title": "rain garden sizing archive two",
             "source": "archive-org"},
        ],
        google_recs=[],
        archive_recs=["archive-org: 0 candidates"])
    res = mgr.search(_W3_QUERY, search_proxy="off")
    ladder = res["ladder"]
    assert "google browser: 2 results" in ladder
    assert "direct keyless: +2 (archive-org)" in ladder
    assert "archive-org: 0 candidates" in ladder
    gi = ladder.index("google browser: 2 results")
    di = ladder.index("direct keyless: +2 (archive-org)")
    ai = ladder.index("archive-org: 0 candidates")
    assert gi < di < ai
    assert ri._lane_keys_from_ladder(ladder) == [
        "google browser", "direct keyless", "archive-org"]


def test_si_ladder_relays_not_engaged_and_0_of_added(monkeypatch, tmp_path):
    """No google rows and a not-engaged line relays; delivered rows that
    were all deduped keep their honest '0 of N added' line; attribution
    stays clean because neither line is a producing shape."""
    mgr = _mgr_ready(monkeypatch, tmp_path)
    _stub_fallback_w3(
        monkeypatch,
        browser_rows=[],
        direct_rows=[],
        google_recs=["google browser: not engaged (earlier bases delivered)",
                     "google browser: 0 of 3 added"],
        archive_recs=["archive-org: 0 candidates"])
    res = mgr.search(_W3_QUERY, search_proxy="off")
    ladder = res["ladder"]
    assert "google browser: not engaged (earlier bases delivered)" in ladder
    assert "google browser: 0 of 3 added" in ladder
    keys = ri._lane_keys_from_ladder(ladder)
    assert "google browser" not in keys and "archive-org" not in keys


def test_si_shipped_stub_bytes_stay(monkeypatch, tmp_path):
    """Shipped stubs lack the backup-rung accessors on purpose: the getattr guards
    keep their ladder bytes exactly as shipped (no lines, no crash)."""
    from test_ladder_persistence import _stub_fallback

    mgr = _mgr_ready(monkeypatch, tmp_path)
    monkeypatch.setattr(si, "_searx_fallback", _stub_fallback())
    res = mgr.search(_W3_QUERY, search_proxy="off")
    ladder = res["ladder"]
    assert not [l for l in ladder if l.startswith("google browser")]
    assert not [l for l in ladder if l.startswith("archive-org")]
    assert not [l for l in ladder if "cdp-slot+wayback" in l]
