"""Instance-lane tests: registry discovery
on the moved searx.space data path with the static-trio fallback, the
GREYSEARCH_INSTANCE_PROBES cap with a rotating probe order, the deep-run
/healthz pre-flight ping that pins a run's alive pool ordering, and the
greysearch_health engine-probe cache. All network lanes are stubbed;
assertions inspect records, not traffic."""
import json
import time
import types

import pytest

from urllib.error import URLError

import proxy_mode as pm
import searxng_integrated as si
import greysearch_mcp as gm
import research_integration as ri
import relevance as relevance_mod
from conftest import set_env_file

QUERY = "rain garden sizing for a driveway"

_CANDIDATES = ["https://cand.one.test", "https://cand.two.test",
               "https://cand.three.test"]


def _healthy(url):
    return {"network_type": "normal", "http": {"status_code": 200},
            "timing": {"search": {"error": None, "working_engines": 5}}}


def _registry_payload(pool=None):
    instances = {}
    for base in (pool or ["https://good.one.test", "https://good.two.test",
                          "https://good.three.test"]):
        instances[base] = _healthy(base)
    instances["http://plainscheme.test"] = _healthy("plainscheme.test")
    instances["https://tor.test"] = {**_healthy("tor.test"),
                                     "network_type": "tor"}
    instances["https://down.test"] = {**_healthy("down.test"),
                                      "http": {"status_code": 404}}
    instances["https://err.test"] = {**_healthy("err.test"),
                                     "timing": {"search": {"error": "boom",
                                                           "working_engines": 5}}}
    instances["https://zero.test"] = {**_healthy("zero.test"),
                                      "timing": {"search": {"error": None,
                                                            "working_engines": 0}}}
    return {"metadata": {"generated": "testsuite"}, "instances": instances}


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


class _StatusResponse:
    def __init__(self, status):
        self.status = status

    def read(self):
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fail_urlopen(req, timeout=None, **k):
    pytest.fail(f"a local search query ran where none was allowed: "
                f"{getattr(req, 'full_url', '')}")


def _fresh_cache(monkeypatch):
    monkeypatch.setattr(si, "_public_searx_cache", None)
    monkeypatch.setattr(si, "_public_searx_cache_at", None)


def _stub_fallback_direct():
    def factory():
        def no_browser(*args, **kwargs):
            raise RuntimeError("stubbed browser lane")
        return types.SimpleNamespace(
            browser_search_public=no_browser,
            _direct_search=lambda *a, **k: [],
            _warm_remote_host=lambda *a, **k: None,
            _filter_instance_junk=lambda q, r, *a, **k: list(r),
            _captcha_holds=lambda: [])
    return factory


def _deep_rows():
    return {"results": [
        {"url": f"https://space.row.test/rain-garden-{i}",
         "title": f"Rain garden sizing instance row {i}",
         "content": "rain garden sizing walkthrough for a driveway",
         "engines": ["duckduckgo"]} for i in range(4)]}


def _local_cover_payload():
    return {"results": [
        {"url": f"https://example.one/rain-garden-{i}",
         "title": f"Rain garden sizing example {i}",
         "content": "rain garden sizing walkthrough for a driveway",
         "engines": ["duckduckgo"]} for i in range(6)]}


def _probe_environment(monkeypatch, tmp_path, pool, cap=None):
    """Manager stubs for the searx.space probe-count tests: local lane
    healthy and covering (never real traffic), fallback lanes inert, and
    discovery serving a fixed pool through a cap recorder."""
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    monkeypatch.delenv("GREYSEARCH_SKIP_LOCAL", raising=False)
    if cap is not None:
        monkeypatch.setenv("GREYSEARCH_INSTANCE_PROBES", str(cap))
    else:
        monkeypatch.delenv("GREYSEARCH_INSTANCE_PROBES", raising=False)
    monkeypatch.setattr(si, "_ROTATION_N", {"n": 0})
    _fresh_cache(monkeypatch)
    called_urls = []
    probe_urls = []

    def _serve(req, timeout=None, **k):
        called_urls.append(getattr(req, "full_url", ""))
        return _FakeJsonResponse(_local_cover_payload())

    monkeypatch.setattr(si.urllib.request, "urlopen", _serve)
    monkeypatch.setattr(si, "_local_alive", lambda *a, **k: True)
    monkeypatch.setattr(si, "_searx_fallback", _stub_fallback_direct())
    mgr = si.SearXNGEngineManager(searxng_urls=["http://127.0.0.1:9999"])
    monkeypatch.setattr(mgr, "working_engines",
                        list(si.REQUIRED_GENERAL_ENGINES))
    monkeypatch.setattr(mgr, "get_working_engines",
                        lambda: list(si.REQUIRED_GENERAL_ENGINES))
    seen_caps = []

    def _discover(max_instances=6, mode=None):
        seen_caps.append(int(max_instances))
        return list(pool)

    monkeypatch.setattr(mgr, "_discover_public_instances", _discover)
    return mgr, called_urls, probe_urls, seen_caps


def _probe_recorder(monkeypatch):
    probe_urls = []

    def _serve_probe(url, timeout=None, mode=None, **k):
        probe_urls.append(str(url))
        return _FakeJsonResponse(_deep_rows())

    return _serve_probe, probe_urls


def _preflight_bridge(monkeypatch, tmp_path):
    """A real bridge whose instance surface is stubbed at the class level
    and whose network lanes are inert: /healthz pings ride pm.open_search
    (answer set later), /search probes deliver topical rows, the local lane
    covers from a fixture payload, scrapes are synthetic."""
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    monkeypatch.delenv("GREYSEARCH_SKIP_LOCAL", raising=False)
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(si, "_ROTATION_N", {"n": 0})
    ping_urls = []
    probe_urls = []
    sleeps = []

    def _serve_space(url, timeout=None, mode=None, **k):
        if str(url).endswith("/healthz"):
            ping_urls.append(str(url))
            return _StatusResponse(200)
        probe_urls.append(str(url))
        return _FakeJsonResponse(_deep_rows())

    monkeypatch.setattr(pm, "open_search", _serve_space)

    def discover(self, max_instances=12, mode=None):
        return list(_CANDIDATES)

    monkeypatch.setattr(si.SearXNGEngineManager,
                        "_discover_public_instances", discover)

    def _serve(req, timeout=None, **k):
        return _FakeJsonResponse(_local_cover_payload())

    monkeypatch.setattr(si.urllib.request, "urlopen", _serve)
    monkeypatch.setattr(si, "_local_alive", lambda *a, **k: True)
    monkeypatch.setattr(si, "_searx_fallback", _stub_fallback_direct())
    monkeypatch.setattr(ri, "_pace", lambda key: 0.0)
    bridge = ri.get_bridge()
    monkeypatch.setattr(bridge.searxng_manager, "working_engines",
                        list(si.REQUIRED_GENERAL_ENGINES))
    monkeypatch.setattr(bridge.searxng_manager, "get_working_engines",
                        lambda: list(si.REQUIRED_GENERAL_ENGINES))

    def _recsleep(seconds):
        sleeps.append(float(seconds))

    monkeypatch.setattr(time, "sleep", _recsleep)
    monkeypatch.setattr(bridge.scraper, "fetch_url", _fake_ok_fetch)
    return bridge, ping_urls, probe_urls, sleeps


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
            "html_capped": False,
            "links": [{"href": url + "#detail", "text": "rain garden "
                                                      "sizing"}],
            "images": [], "videos": [], "audios": [], "pdfs": [],
            "canvases": [], "iframes": [], "meta": {}, "status_code": 200,
            "tables": [], "headings": [{"level": "H1",
                                        "text": "Rain garden sizing"}],
            "code_blocks": [], "injection_flags": [],
            "injection_scanned": True, "elapsed_s": 0.1,
            "scrape_transport": "scrape direct (no proxy)"}


def _stub_bridge_no_surface(monkeypatch, ladder, engines_tried,
                            n_results=4):
    """The stub-manager deep pattern: a manager surface with NO instance
    methods (as the ladder-persistence deep tests stub it), so the pre-flight has no
    instance surface and stays silent."""
    bridge = ri.get_bridge()

    def fake_search(query=None, **kwargs):
        rows = [{"url": f"https://example.one/rain-garden-{i}",
                 "title": f"Rain garden sizing example {i}",
                 "content": "rain garden sizing walkthrough for a driveway",
                 "engine": "testsuite"} for i in range(n_results)]
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



def test_registry_data_path_constant_is_the_fetch_target(monkeypatch,
                                                         tmp_path):
    """Fix 1: instance-registry discovery fetches the moved registry data
    file and only that URL; the site root constant stays the scrape
    fallback root; the 1h module cache replays without a second fetch."""
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    _fresh_cache(monkeypatch)
    fetched = []

    def _served(url, timeout=None, mode=None, **k):
        fetched.append(str(url))
        return _FakeJsonResponse(_registry_payload())

    monkeypatch.setattr(pm, "open_search", _served)
    assert si.SEARX_SPACE_URL == "https://searx.space/"
    assert (si.SEARX_SPACE_DATA_URL
            == "https://searx.space/data/instances.json")
    mgr = si.SearXNGEngineManager(searxng_urls=["http://127.0.0.1:9999"])
    pool = mgr._discover_public_instances(max_instances=12)
    assert fetched == [si.SEARX_SPACE_DATA_URL]
    assert pool == ["https://good.one.test", "https://good.two.test",
                    "https://good.three.test"]
    pool2 = mgr._discover_public_instances(max_instances=12)
    assert pool2 == pool
    assert len(fetched) == 1
    assert (si._public_searx_cache_at is not None
            and si._public_searx_cache == pool)


def test_registry_parser_applies_the_fitness_filter():
    """The registry pool keeps only normal-network https hosts with a 200
    homepage and a working search timing; tor, down, errored, engineless
    and non-https entries are out, registry order preserved."""
    pool = si._registry_instance_urls(_registry_payload(), 12)
    assert pool == ["https://good.one.test", "https://good.two.test",
                    "https://good.three.test"]
    assert si._registry_instance_urls(None, 12) == []
    assert si._registry_instance_urls({}, 12) == []
    assert si._registry_instance_urls(
        {"instances": {"https://capped.test": _healthy("capped.test")}},
        1) == ["https://capped.test"]
    assert si._registry_instance_urls(
        {"instances": {42: _healthy("42.test")}}, 12) == []


def test_registry_failure_falls_back_to_static_trio(monkeypatch, tmp_path):
    """On any registry fetch failure the discovery falls back to today's
    chain exactly once: the searx_fallback harvest (the static trio), the
    HTML scrape of the site root never runs while the harvest delivers,
    and the pool is cached."""
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    _fresh_cache(monkeypatch)
    fetched = []
    attempts = []

    class _StubFallback:
        def __init__(self):
            self.limits = []

        def _harvest_instances(self, force=False, limit=10, mode=None):
            attempts.append(("harvest", int(limit)))
            return ["https://anonsearch.win/", "https://baresearch.org/",
                    "https://etsi.me/"]

    def _fail(url, timeout=None, mode=None, **k):
        fetched.append(str(url))
        raise URLError("registry down")

    monkeypatch.setattr(pm, "open_search", _fail)
    stub = _StubFallback()
    monkeypatch.setattr(si, "_searx_fallback", lambda: stub)
    mgr = si.SearXNGEngineManager(searxng_urls=["http://127.0.0.1:9999"])
    pool = mgr._discover_public_instances(max_instances=12)
    assert fetched == [si.SEARX_SPACE_DATA_URL]
    assert attempts == [("harvest", 12)]
    assert pool == ["https://anonsearch.win", "https://baresearch.org",
                    "https://etsi.me"]
    assert len(fetched) == 1
    pool2 = mgr._discover_public_instances(max_instances=12)
    assert pool2 == pool
    assert len(attempts) == 1



def test_probe_cap_default_12_env_override(monkeypatch):
    """The effective instance-probe cap is 12 by default; the env overrides
    it, floors at 1, and junk falls back to 12."""
    monkeypatch.delenv("GREYSEARCH_INSTANCE_PROBES", raising=False)
    assert si._instance_probes() == 12
    monkeypatch.setenv("GREYSEARCH_INSTANCE_PROBES", "5")
    assert si._instance_probes() == 5
    monkeypatch.setenv("GREYSEARCH_INSTANCE_PROBES", "1")
    assert si._instance_probes() == 1
    monkeypatch.setenv("GREYSEARCH_INSTANCE_PROBES", "0")
    assert si._instance_probes() == 1
    monkeypatch.setenv("GREYSEARCH_INSTANCE_PROBES", "junk")
    assert si._instance_probes() == 12


def test_probe_rotation_offset_changes_order_across_runs(monkeypatch, tmp_path):
    """Two searches against one pool probe from different offsets: run one
    probes the pool head, run two starts one further (the counter
    advances), the probed sets differ, the ladder lines carry the rotated
    head order, and the run's discovery was asked for the cap."""
    pool = ["https://p0.test", "https://p1.test", "https://p2.test"]
    mgr, called_urls, probe_urls, seen_caps = _probe_environment(
        monkeypatch, tmp_path, pool, cap=2)
    serve_probe, recorded = _probe_recorder(monkeypatch)
    monkeypatch.setattr(pm, "open_search", serve_probe)
    monkeypatch.setenv("GREYSEARCH_RELEVANT_TARGET", "20")
    res1 = mgr.search(QUERY, max_results=8, search_proxy="off")
    run1 = list(recorded)
    del recorded[:]
    res2 = mgr.search(QUERY, max_results=8, search_proxy="off")
    assert seen_caps == [2, 2]
    head1 = "(instances: https://p0.test,https://p1.test)"
    head2 = "(instances: https://p1.test,https://p2.test)"
    assert any(head1 in l for l in res1["ladder"]), res1["ladder"]
    assert any(head2 in l for l in res2["ladder"]), res2["ladder"]
    set1 = sorted({u.split("/search")[0] for u in run1 if "/search" in u})
    assert set1 == ["https://p0.test", "https://p1.test"]
    set2 = sorted({u.split("/search")[0] for u in recorded
                   if "/search" in u})
    assert set2 == ["https://p1.test", "https://p2.test"]


def test_probe_cap_counts_failing_probes_honestly(monkeypatch, tmp_path):
    """The raised cap is budget-counted in the ladder when nothing is
    delivered: two refused probes leave one honest record naming the count
    and the failure class, never a silent skip."""
    pool = ["https://p0.test", "https://p1.test", "https://p2.test"]
    mgr, called_urls, probe_urls, seen_caps = _probe_environment(
        monkeypatch, tmp_path, pool, cap=2)
    serve_probe, recorded = _probe_recorder_with_refusal(monkeypatch)
    res = mgr.search(QUERY, max_results=8, search_proxy="off")
    space_recs = [l for l in res["ladder"] if l.startswith("searx.space:")]
    assert space_recs == ["searx.space: tried 2 instance-probes, failed "
                          "(URLError: <urlopen error connection refused>)"]
    assert len(recorded) == 2


def _probe_recorder_with_refusal(monkeypatch):
    probe_urls = []

    def _refuse(url, timeout=None, mode=None, **k):
        probe_urls.append(str(url))
        raise URLError("connection refused")

    monkeypatch.setattr(pm, "open_search", _refuse)
    return _refuse, probe_urls


def test_pinned_run_pool_leads_probes_without_refetch(monkeypatch, tmp_path):
    """Fix 2's pin, on the probe side: once a run's alive pool ordering is
    pinned, the searx.space lane probes from it (rotated) and never asks
    discovery again, so a dead-preflight surface cannot re-probe the whole
    registry head."""
    pool = ["https://p0.test", "https://p1.test", "https://p2.test"]
    mgr, called_urls, probe_urls, seen_caps = _probe_environment(
        monkeypatch, tmp_path, pool, cap=12)

    def _no_discovery(*args, **kwargs):
        pytest.fail("discovery ran while a run pool ordering was pinned")

    monkeypatch.setattr(mgr, "_discover_public_instances", _no_discovery)
    mgr.set_run_pool_order(pool)
    serve_probe, recorded = _probe_recorder(monkeypatch)
    monkeypatch.setattr(pm, "open_search", serve_probe)
    res = mgr.search(QUERY, max_results=8, search_proxy="off")
    bases = sorted({u.split("/search")[0] for u in recorded
                    if "/search" in u})
    assert bases == pool
    assert "searx.space" in ",".join(res["ladder"])
    assert seen_caps == []



def test_preflight_alive_marks_digest_line_and_run_pool(monkeypatch, tmp_path):
    """Fix 2: a deep run pings its candidate pool on /healthz one at a
    time, at least 3 s apart; HTTP 200 marks each one alive, the run
    digest carries the line and the structured record, the alive list
    becomes the run's pool ordering, and the searches probe from it."""
    bridge, ping_urls, probe_urls, sleeps = _preflight_bridge(monkeypatch,
                                                              tmp_path)
    res = bridge.research_deep(QUERY, max_pages=2, max_total_seconds=60,
                               enable_semantic_judge=False,
                               search_proxy="off")
    pf = res["instance_preflight"]
    assert pf["alive"] == _CANDIDATES
    assert pf["dead"] == []
    assert pf["tried"] == 3
    assert pf["line"] == "instance preflight: alive 3 of 3 tried"
    assert ping_urls == [c + "/healthz" for c in _CANDIDATES]
    assert [s for s in sleeps if s] == [3.0, 3.0]
    assert all(s >= 3.0 for s in sleeps if s >= 3.0)
    rec = next(s for s in res["searches"]
               if s.get("category") == "instance-preflight")
    assert rec["ladder"] == ["instance preflight: alive 3 of 3 tried"]
    assert res["searches"][0]["category"] == "decomposition"
    assert ("instance preflight: alive 3 of 3 tried" in res["search_ladder"])
    assert (res["run_report"]["instance_preflight"]["alive"] == _CANDIDATES)
    assert (bridge.searxng_manager._run_pool_order == _CANDIDATES)
    search_bases = {u.split("/search")[0] for u in probe_urls
                    if "/search" in u}
    assert search_bases == set(_CANDIDATES), search_bases


def test_preflight_pings_healthz_not_search(monkeypatch, tmp_path):
    """Every pre-flight request is a /healthz GET against the instance
    root, never a /search request, so the ping cannot read as a search
    request in any record."""
    bridge, ping_urls, probe_urls, sleeps = _preflight_bridge(monkeypatch,
                                                              tmp_path)
    bridge.research_deep(QUERY, max_pages=1, max_total_seconds=60,
                         enable_semantic_judge=False, search_proxy="off")
    assert ping_urls and all(u.endswith("/healthz") for u in ping_urls)
    assert all("/search?" not in u for u in ping_urls)


def test_preflight_dead_keeps_run_going(monkeypatch, tmp_path):
    """Every ping failure (refusal, non-200) counts the instance dead for
    THIS run, the digest line says zero alive of Y tried, the run-pool pin
    stays empty so the lanes fall back to the discovered pool, and the run
    itself continues and still reports its lanes."""
    bridge, ping_urls, probe_urls, sleeps = _preflight_bridge(monkeypatch,
                                                              tmp_path)

    def _refuse(url, timeout=None, mode=None, **k):
        if str(url).endswith("/healthz"):
            ping_urls.append(str(url))
            raise URLError("connection refused")
        probe_urls.append(str(url))
        return _FakeJsonResponse(_deep_rows())

    monkeypatch.setattr(pm, "open_search", _refuse)
    res = bridge.research_deep(QUERY, max_pages=2, max_total_seconds=60,
                               enable_semantic_judge=False,
                               search_proxy="off")
    pf = res["instance_preflight"]
    assert pf["alive"] == [] and pf["dead"] == _CANDIDATES
    assert pf["tried"] == 3
    assert pf["line"] == "instance preflight: alive 0 of 3 tried"
    assert bridge.searxng_manager._run_pool_order == []
    space_recs = [l for l in res["search_ladder"]
                  if l.startswith("searx.space")]
    assert space_recs, res["search_ladder"]
    assert any(s.get("category") == "general" for s in res["searches"])
    assert probe_urls, "the run must continue probing after a dead pool"


def test_preflight_no_surface_stays_silent(monkeypatch, tmp_path):
    """Without an instance surface on the manager (the ladder-persistence
    deep-test stub shape) the pre-flight records an empty attempt and never adds a
    plan record, so those runs' persisted ladders stay byte-identical."""
    bridge = _stub_bridge_no_surface(monkeypatch, ["proxy mode off: direct"],
                                     [])
    res = bridge.research_deep(QUERY, max_pages=1, max_total_seconds=40,
                               enable_semantic_judge=False,
                               search_proxy="off")
    pf = res["instance_preflight"]
    assert pf["alive"] == [] and pf["tried"] == 0 and pf["line"] == ""
    assert not any(s.get("category") == "instance-preflight"
                   for s in res["searches"])
    assert not any(
        "instance preflight" in l for l in res["search_ladder"])



def _probe_class_stub(monkeypatch, probe_count):
    def discover(self, max_instances=3, mode=None):
        probe_count.append(int(max_instances))
        return [f"https://probe.test/{len(probe_count)}"]

    monkeypatch.setattr(gm.SearXNGEngineManager,
                        "_discover_public_instances", discover)


def _seed_probe_cache(monkeypatch, at, instances):
    monkeypatch.setitem(gm._PROBE_CACHE, "at", at)
    monkeypatch.setitem(gm._PROBE_CACHE, "instances", instances)


@pytest.fixture()
def _reset_cache_env(monkeypatch):
    monkeypatch.delenv("GREYSEARCH_PROBE_CACHE_S", raising=False)
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    monkeypatch.setitem(gm._PROBE_CACHE, "at", 0.0)
    monkeypatch.setitem(gm._PROBE_CACHE, "instances", None)
    yield
    gm._PROBE_CACHE["at"] = 0.0
    gm._PROBE_CACHE["instances"] = None


def test_probe_cache_replays_inside_window_and_reports_age(monkeypatch,
                                                           _reset_cache_env):
    """Fix 3: the first health probe inside the window probes and stores
    (age 0, replayed false); a second call inside the window replays the
    stored value with the reported age and never touches the manager a
    second time."""
    monkeypatch.setenv("GREYSEARCH_PROBE_CACHE_S", "600")
    probe_count = []
    _probe_class_stub(monkeypatch, probe_count)
    out1 = gm._health_instance_probe(max_instances=3)
    assert out1["instances"] == ["https://probe.test/1"]
    assert out1["replayed"] is False and out1["age_s"] == 0.0
    assert out1["window_s"] == 600.0
    monkeypatch.setitem(gm._PROBE_CACHE, "at", time.time() - 45.0)
    out2 = gm._health_instance_probe(max_instances=3)
    assert probe_count == [3]
    assert out2["replayed"] is True
    assert out2["age_s"] == pytest.approx(45.0, abs=1.0)
    assert out2["instances"] == ["https://probe.test/1"]
    assert out2["window_s"] == 600.0


def test_probe_cache_invalidated_by_window_expiry(monkeypatch,
                                                  _reset_cache_env):
    """A cache entry older than the window is never replayed: the next
    health call probes fresh and re-stores, and the call right after
    replays the new entry."""
    monkeypatch.setenv("GREYSEARCH_PROBE_CACHE_S", "1")
    probe_count = []
    _probe_class_stub(monkeypatch, probe_count)
    _seed_probe_cache(monkeypatch, time.time() - 5.0,
                      ["https://stale.test"])
    out1 = gm._health_instance_probe(max_instances=3)
    assert out1["replayed"] is False
    assert out1["instances"] == ["https://probe.test/1"]
    assert out1["window_s"] == 1.0
    out2 = gm._health_instance_probe(max_instances=3)
    assert probe_count == [3]
    assert out2["replayed"] is True
    assert out2["instances"] == ["https://probe.test/1"]


def test_probe_cache_env_override_honored(monkeypatch, _reset_cache_env):
    """The window comes from the env: a fresh entry inside a shorter
    window replays, the same entry against a zero window never does, and
    junk falls back to the 600 default."""
    probe_count = []
    _probe_class_stub(monkeypatch, probe_count)
    monkeypatch.setenv("GREYSEARCH_PROBE_CACHE_S", "30")
    _seed_probe_cache(monkeypatch, time.time() - 10.0,
                      ["https://shortwindow.test"])
    out = gm._health_instance_probe(max_instances=3)
    assert out["replayed"] is True and out["window_s"] == 30.0
    monkeypatch.setenv("GREYSEARCH_PROBE_CACHE_S", "0")
    _seed_probe_cache(monkeypatch, time.time(), ["https://disabled.test"])
    out2 = gm._health_instance_probe(max_instances=3)
    assert out2["replayed"] is False and out2["window_s"] == 0.0
    monkeypatch.setenv("GREYSEARCH_PROBE_CACHE_S", "junk")
    _seed_probe_cache(monkeypatch, time.time(), ["https://junk.test"])
    out3 = gm._health_instance_probe(max_instances=3)
    assert out3["replayed"] is True and out3["window_s"] == 600.0
    assert probe_count == [3]


def test_health_response_keeps_shape_with_probe_cache(monkeypatch,
                                                      tmp_path,
                                                      _reset_cache_env):
    """The health response's existing fields stay put across fresh and
    replayed probes: searx_space_instances keeps its value shape, the mode
    fields stay live, and the new cache facts only add keys."""
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    monkeypatch.setenv("GREYSEARCH_PROBE_CACHE_S", "600")
    probe_count = []
    _probe_class_stub(monkeypatch, probe_count)
    out1 = gm.dispatch("greysearch_health", {})
    assert out1["searx_space_instances"] == ["https://probe.test/1"]
    assert out1["instance_probe_cache"]["replayed"] is False
    assert out1["instance_probe_cache"]["window_s"] == 600.0
    assert out1["search_proxy"] in ("off", "on", "")
    assert isinstance(out1["search_proxy_note"], str)
    assert "browser_slots" in out1 and "greysearch_runs" in out1
    out2 = gm.dispatch("greysearch_health", {})
    assert out2["searx_space_instances"] == ["https://probe.test/1"]
    assert out2["instance_probe_cache"]["replayed"] is True
    assert out2["instance_probe_cache"]["age_s"] >= 0
    assert out2["search_proxy"] == out1["search_proxy"]
    assert out2["search_proxy_note"] == out1["search_proxy_note"]
    assert probe_count == [3]


def test_health_probe_error_shape_unaffected(monkeypatch, _reset_cache_env):
    """A probe error replays as today's "error: ..." string value, and a
    second call inside the window replays that string verbatim."""
    monkeypatch.setenv("GREYSEARCH_PROBE_CACHE_S", "600")

    def discover(self, max_instances=3, mode=None):
        raise RuntimeError("registry down")

    monkeypatch.setattr(gm.SearXNGEngineManager,
                        "_discover_public_instances", discover)
    out1 = gm._health_instance_probe(max_instances=3)
    assert isinstance(out1["instances"], str)
    assert out1["instances"].startswith("error: ")
    out2 = gm._health_instance_probe(max_instances=3)
    assert out2["replayed"] is True
    assert out2["instances"] == out1["instances"]
