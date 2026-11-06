#!/usr/bin/env python3
"""Partials-first contracts: the search NEVER reads as broken while
partial results exist. Local partials are kept and flagged (never wiped),
the upper rungs (searx.space browser lane, google/bing rungs, direct
keyless) engage while the heuristically measured relevant count is under
the floor (default 20), the engine-name poison drop is opt-in and default
OFF, the Anubis bot-check is captcha state, the solver wait runs two
bounded phases of 30 s then 60 s, auth cookies are never cleared between lane hops, the pinned first
instances always lead the browser lane, the auth settle runs on every page
load, and the result names the advisory when the floor stays unmet.
All network lanes are stubbed; assertions inspect records, not traffic."""
import base64
import json
import sys
import time
import types
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import browser_session as bs
import searx_fallback as sf
import searxng_integrated as si

_QUERY = "rain garden sizing"


def _rows(prefix, n, engine, tag="rain garden sizing {}"):
    return [{"url": f"https://{prefix}.example/{i}/rain-garden-sizing",
             "title": tag.format(i), "content": "rain garden sizing",
             "engine": engine}
            for i in range(n)]


class _JsonResponse:
    def __init__(self, payload):
        self.status = 200
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_proxy(monkeypatch):
    monkeypatch.setattr(si._proxy_mode, "prepared",
                        lambda *a, **k: {"ok": True, "mode": "off"})
    monkeypatch.setattr(si._proxy_mode, "ladder_note",
                        lambda *a, **k: "proxy mode off: direct")


def _floor_mgr(monkeypatch, fullset_rows, fullset_return, stub_urlopen=False):
    mgr = si.SearXNGEngineManager(searxng_urls=["http://127.0.0.1:9999"])

    def fake_fullset(query, cat, safe, tr, lang, merged, seen, ladder,
                     selected=(), engines_tried=None):
        for r in fullset_rows:
            if r["url"] not in seen:
                seen.add(r["url"])
                merged.append(r)
        return fullset_return

    monkeypatch.setattr(mgr, "_fullset_first", fake_fullset)
    monkeypatch.setattr(mgr, "get_working_engines", lambda: ["duckduckgo"])
    monkeypatch.setattr(mgr, "_discover_public_instances",
                        lambda max_instances=6, mode=None: [])
    if stub_urlopen:
        monkeypatch.setattr(si.urllib.request, "urlopen",
                            lambda req, timeout=None, **k:
                            _JsonResponse({"results": []}))
    return mgr


def test_poison_drop_is_opt_in_and_default_off(monkeypatch):
    """GREYSEARCH_POISON_FILTER unset: a row the instance attributes to
    google merges like any other (stop disabling search engines). With the
    env set to 1 the legacy engine-name drop returns."""
    monkeypatch.delenv("GREYSEARCH_POISON_FILTER", raising=False)
    _patch_proxy(monkeypatch)
    payload = {"results": [
        {"url": "https://g.example/rain-garden-sizing", "title":
         "rain garden sizing guide", "engines": ["google"]},
        {"url": "https://m.example/rain-garden-sizing", "title":
         "rain garden sizing two", "engines": ["mojeek"]},
        {"url": "https://w.example/rain-garden-sizing", "title":
         "rain garden sizing three", "engines": ["wikipedia"]},
        {"url": "https://d.example/rain-garden-sizing", "title":
         "rain garden sizing four", "engines": ["duckduckgo"]},
    ]}
    monkeypatch.setattr(si.urllib.request, "urlopen",
                        lambda req, timeout=None, **k: _JsonResponse(payload))
    mgr = si.SearXNGEngineManager(searxng_urls=["http://127.0.0.1:9999"])
    merged, seen, ladder = [], set(), []
    mgr._fullset_first(_QUERY, "", 0, "", "", merged, seen, ladder)
    assert any(r.get("engines") == ["google"] for r in merged)
    assert any(r.get("engines") == ["mojeek"] for r in merged)

    merged2, seen2, ladder2 = [], set(), []
    monkeypatch.setenv("GREYSEARCH_POISON_FILTER", "1")
    mgr2 = si.SearXNGEngineManager(searxng_urls=["http://127.0.0.1:9999"])
    mgr2._fullset_first(_QUERY, "", 0, "", "", merged2, seen2, ladder2)
    assert not any(r.get("engines") == ["google"] for r in merged2)
    assert len(merged2) == len(merged) - 1


def test_row_topical_and_relevant_target_knobs(monkeypatch):
    """The per-row heuristic: the row itself must carry the query's content
    tokens (two when the query has several), flagged off-topic rows never
    count, and the floor defaults to 20 with GREYSEARCH_RELEVANT_TARGET
    overriding and junk values falling back to the default."""
    assert si._row_topical(_QUERY, {"title": "rain garden sizing one",
                                    "url": "https://a/x", "content": ""})
    assert not si._row_topical(_QUERY, {"title": "cheap flights to rome",
                                        "url": "https://a/y",
                                        "content": ""})
    assert not si._row_topical(_QUERY, {"title": "rain garden sizing one",
                                        "url": "https://a/x",
                                        "_off_topic": True})
    assert si._row_topical("infostealer", {"title": "infostealer campaigns",
                                           "url": "", "content": ""})
    monkeypatch.delenv("GREYSEARCH_RELEVANT_TARGET", raising=False)
    assert si._relevant_target() == 20
    monkeypatch.setenv("GREYSEARCH_RELEVANT_TARGET", "7")
    assert si._relevant_target() == 7
    monkeypatch.setenv("GREYSEARCH_RELEVANT_TARGET", "junk")
    assert si._relevant_target() == 20


def test_owner_instances_lead_the_browser_lane():
    """The pinned first instances always come first in the browser-lane base
    order, whatever the discovery order was, with duplicates folded."""
    out = si._owner_first(["https://zz.example", "https://search.hbubli.cc/"])
    assert out == ["https://search.hbubli.cc",
                   "https://searx.ononoki.org", "https://zz.example"]
    assert sf._owner_first_urls(["https://zz.example"]) == out


def test_floor_engages_upper_rungs_and_names_the_advisory(monkeypatch):
    """Target 40 with 10 local rows: the searx.space browser lane engages
    even though local returned results, its rows split by engine tag into
    the browser/google/bing ladder lines, the direct keyless lane runs
    after, and the result carries relevant_count/relevant_target and the
    final on advisory when the floor stays unmet."""
    monkeypatch.setenv("GREYSEARCH_RELEVANT_TARGET", "40")
    _patch_proxy(monkeypatch)
    sf_stub = types.SimpleNamespace(
        browser_search_public=lambda q, mr, insts=None, need=None:
            _rows("b", 10, "https://searx.ononoki.org")
            + [{"url": "https://g.example/1", "title": "rain garden sizing",
                "content": "rain garden sizing",
                "engine": "https://www.google.com"},
               {"url": "https://bg.example/1", "title": "rain garden sizing",
                "content": "rain garden sizing",
                "engine": "https://www.bing.com"}],
        _direct_search=lambda q, max_results=10, mode=None: [],
        _captcha_holds=lambda: [],
        google_lane_records=lambda: [],
        bing_lane_records=lambda: [],
        _filter_instance_junk=lambda q, r, *a, **k: list(r))
    monkeypatch.setattr(si, "_searx_fallback", lambda: sf_stub)
    mgr = _floor_mgr(monkeypatch, _rows("a", 10, "127.0.0.1:8888"), True)
    monkeypatch.setattr(si, "_local_alive", lambda u, timeout=15.0: True)
    res = mgr.search(_QUERY, max_results=10, max_seconds=30)
    ladder = res["ladder"]
    assert any(l.startswith("relevant floor: 10/40 relevant") for l in ladder)
    assert any(l.startswith("searx.space browser: 10 results") for l in ladder)
    assert any(l.startswith("google browser: 1 results") for l in ladder)
    assert any(l.startswith("bing browser: 1 results") for l in ladder)
    assert "direct keyless: 0 results" in ladder
    assert res["relevant_target"] == 40
    assert res["relevant_count"] == 22
    assert any(l.startswith("relevant floor unmet (22/40 relevant)")
               and "search_proxy=on" in l for l in ladder)
    assert res["results"][0]["url"].startswith("https://a.example/")


def test_floor_met_lanes_do_not_hunt(monkeypatch):
    """Target 5 with 25 relevant local rows: no floor line, the browser
    lane never engages, and the direct lane records the legacy
    not-engaged line; the result still names the floor keys."""
    monkeypatch.setenv("GREYSEARCH_RELEVANT_TARGET", "5")
    _patch_proxy(monkeypatch)
    browser_calls = []

    def guard_browser(*a, **k):
        browser_calls.append(k)
        return []

    sf_stub = types.SimpleNamespace(
        browser_search_public=guard_browser,
        _direct_search=lambda *a, **k: [],
        _captcha_holds=lambda: [],
        google_lane_records=lambda: [],
        bing_lane_records=lambda: [])
    monkeypatch.setattr(si, "_searx_fallback", lambda: sf_stub)
    mgr = _floor_mgr(monkeypatch, _rows("a", 25, "127.0.0.1:8888"), True)
    monkeypatch.setattr(si, "_local_alive", lambda u, timeout=15.0: True)
    res = mgr.search(_QUERY, max_results=10, max_seconds=30)
    ladder = res["ladder"]
    assert not any(l.startswith("relevant floor") for l in ladder)
    assert browser_calls == []
    assert "direct keyless: not engaged (enough results)" in ladder
    assert res["relevant_count"] == 25
    assert res["relevant_target"] == 5


def test_partial_offtopic_rows_are_kept_flagged(monkeypatch):
    """An off-topic local set is NEVER wiped: the rows ride the result
    flagged off_topic, the floor line still fires (they count as 0
    relevant), and the browser lane hunts on."""
    monkeypatch.setenv("GREYSEARCH_RELEVANT_TARGET", "20")
    _patch_proxy(monkeypatch)
    sf_stub = types.SimpleNamespace(
        browser_search_public=lambda *a, **k:
            _rows("b", 6, "https://searx.ononoki.org"),
        _direct_search=lambda *a, **k: [],
        _captcha_holds=lambda: [],
        google_lane_records=lambda: [],
        bing_lane_records=lambda: [],
        _filter_instance_junk=lambda q, r, *a, **k: list(r))
    monkeypatch.setattr(si, "_searx_fallback", lambda: sf_stub)
    off_rows = [{"url": f"https://off.example/{i}",
                 "title": "cheap flights to rome", "content": "cheap flights"}
                for i in range(5)]
    mgr = _floor_mgr(monkeypatch, off_rows, False, stub_urlopen=True)
    monkeypatch.setattr(si, "_local_alive", lambda u, timeout=15.0: True)
    res = mgr.search(_QUERY, max_results=10, max_seconds=30)
    ladder = res["ladder"]
    assert any(l.startswith("relevant floor: 0/20 relevant") for l in ladder)
    assert any(l.startswith("searx.space browser: 6 results") for l in ladder)
    flagged = [r for r in res["results"] if r.get("off_topic")]
    assert flagged, "off-topic rows must ride the result flagged"
    assert all(r["url"].startswith("https://off.example/") for r in flagged)


def test_local_lane_always_attempts_no_env_gate(monkeypatch):
    """sf.search no longer carries the dead GREYSEARCH_LOCAL_SEARX gate:
    with a failing local fetch the ladder still runs the exact static lane
    keys (local always attempted), and a working local lane returns its
    rows."""
    monkeypatch.setattr(sf, "_harvest_instances", lambda **k: [])
    monkeypatch.setattr(sf, "_direct_search", lambda *a, **k: [])
    monkeypatch.setattr(sf, "browser_search_public", lambda *a, **k: [])
    monkeypatch.setattr(sf._proxy_mode, "prepared",
                        lambda *a, **k: {"ok": True, "mode": "off"})
    monkeypatch.setattr(sf._proxy_mode, "ladder_note",
                        lambda *a, **k: "proxy mode off: direct")

    def raising_fetch(*a, **k):
        raise ConnectionError("stubbed local lane")

    monkeypatch.setattr(sf, "_fetch", raising_fetch)
    res = sf.search(_QUERY)
    assert res["ok"] is False
    assert res["ladder"][0] == "proxy mode off: direct"
    assert "local" in res["ladder"]

    payload = {"results": [
        {"url": "https://a.example/rain-garden-sizing", "title":
         "rain garden sizing one"}] * 3}
    monkeypatch.setattr(sf, "_fetch",
                        lambda *a, **k: _JsonResponse(payload))
    res2 = sf.search(_QUERY)
    assert res2["ok"] is True
    assert res2["source"] == "local:8888"
    assert len(res2["results"]) == 3


def test_browser_lane_keeps_cookies_and_owner_first(monkeypatch):
    """No hop in the browser lane (instances, google rung, bing rung)
    clears the context cookie jar anymore, and the base order starts with
    the pinned first instances."""
    cleared = []
    visits = []

    class _Ctx:
        browser = None

        def clear_cookies(self):
            cleared.append(True)

    class _P:
        def goto(self, url, *a, **k):
            visits.append(url.split("/")[2] if url.count("/") >= 2 else url)
            raise TimeoutError("stubbed wall")

    monkeypatch.setattr(sf, "_tuning", types.SimpleNamespace(
        get=lambda *a, **k: 0.0, scaled=lambda *a, **k: 0.0))
    monkeypatch.setattr(sf, "pace_wait", lambda *_: None)
    monkeypatch.setattr(sf, "_warm_remote_host", lambda *a, **k: None)
    monkeypatch.setattr(sf, "ensure_instance_ready", lambda *a, **k: None)
    monkeypatch.setattr(sf, "_auth_settle", lambda *a, **k: None)
    monkeypatch.setattr(sf, "_gs_window", lambda: _P())
    monkeypatch.setattr(sf, "_GS_WINDOW",
                        {"proc": None, "cdp": None, "ctx": _Ctx(),
                         "page": None})
    sf._LAST_BROWSER_SEARCH = time.time()
    res = sf.browser_search_public(_QUERY, max_results=10)
    assert res == []
    assert cleared == []
    assert visits[:2] == ["search.hbubli.cc", "searx.ononoki.org"]
    records = sf.google_lane_records() + sf.bing_lane_records()
    assert len(records) == 2
    assert all(r.find("browser: failed/bound-out") != -1 for r in records)


def test_auth_settle_runs_every_load_with_env_and_cap(monkeypatch):
    """The auth settle waits 30 s by default, honors GREYSEARCH_AUTH_SETTLE_S
    (45 s), and hard caps at 60 s however large the env is."""
    slept = []

    class _P:
        def wait_for_timeout(self, ms):
            slept.append(ms)

    monkeypatch.delenv("GREYSEARCH_AUTH_SETTLE_S", raising=False)
    sf._auth_settle(_P())
    assert slept == [30000]
    monkeypatch.setenv("GREYSEARCH_AUTH_SETTLE_S", "45")
    sf._auth_settle(_P())
    assert slept[-1] == 45000
    monkeypatch.setenv("GREYSEARCH_AUTH_SETTLE_S", "90")
    sf._auth_settle(_P())
    assert slept[-1] == 60000


def test_anubis_is_captcha_state_and_wait_defaults_thirty_sixty(monkeypatch):
    """The Anubis bot-check page ("making sure you're not a bot", anubis
    markers) is captcha state so the bounded solver wait engages on it, and
    the wait defaults are the documented WAIT 30-60 s doctrine: phase one 30 s
    (GREYSEARCH_CAPTCHA_WAIT_MIN_S), phase two 60 s
    (GREYSEARCH_CAPTCHA_WAIT_MAX_S); the single-length pin default stays 45
    s for the older GREYSEARCH_CAPTCHA_WAIT_S contract."""
    monkeypatch.delenv("GREYSEARCH_CAPTCHA_WAIT_S", raising=False)
    monkeypatch.delenv("GREYSEARCH_CAPTCHA_WAIT_MIN_S", raising=False)
    monkeypatch.delenv("GREYSEARCH_CAPTCHA_WAIT_MAX_S", raising=False)
    assert bs._CAPTCHA_DEFAULT_WAIT_S == 45.0
    assert bs._CAPTCHA_WAIT_MIN_DEFAULT_S == 30.0
    assert bs._CAPTCHA_WAIT_MAX_DEFAULT_S == 60.0
    assert bs.captcha_phase_waits() == (30.0, 60.0)
    assert bs._ANUBIS_TEXT_RE.search(
        "Making sure you're not a bot.\nThis works because your browser")
    assert bs._ANUBIS_TEXT_RE.search("making sure you are not a bot")
    assert "making sure you('re| are) not a bot" in bs._CAPTCHA_STATE_JS
    assert r"\banubis\b/i" in bs._CAPTCHA_STATE_JS


def test_bing_rung_decode_and_not_engaged_records(monkeypatch):
    """The bing rung exists, decodes /ck/a?u=a1<base64url> tracking links
    to their real targets, rejects non-URL decodes, and carries the same
    not-engaged record family as the google rung."""
    monkeypatch.delenv("GREYSEARCH_BROWSER_LANE", raising=False)
    del sf._CAPTCHA_HOLDS[:]
    target = "https://example.org/deep/page?q=1"
    u = "a1" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    href = f"https://www.bing.com/ck/a?!&p=7&u={u}&ntb=1"
    assert sf._bing_decode_url(href) == target
    assert sf._bing_decode_url("https://plain.example/page") == ""
    assert callable(sf._bing_rung) and callable(sf.bing_lane_records)
    assert "b_algo" in sf._BING_LINKS_JS

    def no_goto(*a, **k):
        raise AssertionError("no goto expected")

    pg = types.SimpleNamespace(goto=no_goto)
    del sf._BING_LANE_RECORDS[:]
    ok = sf._bing_rung(_QUERY, 10, pg, None, [{"url": "https://x/1"}],
                       time.time() + 9)
    assert ok is False
    assert sf.bing_lane_records() == [
        "bing browser: not engaged (earlier bases delivered)"]
    del sf._BING_LANE_RECORDS[:]
    ok2 = sf._bing_rung(_QUERY, 10, pg, None, [], time.time() + 9, need=5)
    assert ok2 is False
    assert sf.bing_lane_records()[0].startswith(
        "bing browser: failed/bound-out")
