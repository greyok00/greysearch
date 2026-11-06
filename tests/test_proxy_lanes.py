"""Search-lane proxy behavior tests (no network): every external search
hop resolves its opener per call from the mode, off mode explicitly
bypasses ambient proxies, an unhonorable on holds external lanes
back instead of going direct silently, and the scrape transports record
the "scrape direct (no proxy)" guard."""
import types

import pytest

import proxy_mode as pm
import searx_fallback as sf
import searxng_integrated as si
from conftest import write_fixture_env, set_env_file


class _FakeResponse:
    """Stands in for a urlopen response; .read() raises so lanes fail fast
    and the ladder (not the network) is what the test inspects."""

    def read(self, *a, **k):
        raise RuntimeError("tests must not read the network")


def _fail_open_search(calls):
    def fake_open_search(url, timeout=15, mode=None, env_file=None,
                         headers=None, data=None):
        calls.append({"url": url, "timeout": timeout, "mode": mode,
                      "headers": dict(headers or {})})
        return _FakeResponse()
    return fake_open_search


def _no_harvest(calls):
    def fake_harvest(force=False, limit=10, mode=None):
        calls.append(("harvest", dict(mode or {})))
        return []
    return fake_harvest


def _no_harvest_strict():
    def fake_harvest(force=False, limit=10, mode=None):
        raise AssertionError("external lane must not run when the proxy "
                             "mode cannot be honoured")
    return fake_harvest


# ---- searx_fallback.search ------------------------------------------------

def test_fallback_off_mode_records_note_and_bypasses_ambient(
        monkeypatch, tmp_path):
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    set_env_file(monkeypatch, tmp_path / "absent.env")
    calls = []
    monkeypatch.setattr(pm, "open_search", _fail_open_search(calls))
    monkeypatch.setattr(sf, "_harvest_instances", _no_harvest(calls))
    monkeypatch.setattr(sf, "browser_search_public",
                        lambda *a, **k: [])
    res = sf.search("testsuite question", search_proxy="off")
    assert res["ladder"][0] == pm.OFF_LADDER_NOTE
    probe_urls = {c["url"] for c in calls
                  if isinstance(c, dict) and "html.duckduckgo" in c["url"]}
    assert probe_urls, "direct keyless lanes must still be tried in off mode"
    for call in calls:
        if isinstance(call, dict) and call.get("mode") is not None:
            rec = call["mode"]
            assert rec["mode"] == "off" and rec["proxy"] is None
    assert "search_proxy" not in res or res["search_proxy"] == "off"


def test_fallback_on_with_fixture_rides_proxy_record(
        monkeypatch, tmp_path):
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    path = tmp_path / "proxy.env"
    val = write_fixture_env(path)
    set_env_file(monkeypatch, path)
    calls = []
    monkeypatch.setattr(pm, "open_search", _fail_open_search(calls))
    monkeypatch.setattr(sf, "_harvest_instances", _no_harvest(calls))
    monkeypatch.setattr(sf, "browser_search_public",
                        lambda *a, **k: [])
    res = sf.search("testsuite question", search_proxy="on")
    assert res["ladder"][0] == pm.ON_LADDER_NOTE
    harvest_modes = [c for c in calls
                     if isinstance(c, tuple) and c[0] == "harvest"]
    assert harvest_modes == [("harvest",
                              {"ok": True, "mode": "on",
                               "proxy": {"http": val, "https": val},
                               "error": "", "env_file": str(path)})]
    ddg = [c for c in calls if isinstance(c, dict)
           and "html.duckduckgo" in c["url"]]
    assert ddg and all(c["mode"] == {"ok": True, "mode": "on",
                                     "proxy": {"http": val, "https": val},
                                     "error": "", "env_file": str(path)}
                       for c in ddg)
    assert "SECRET" not in str(res)
    assert val.startswith("http://testsuite:")


def test_fallback_on_missing_file_holds_external_lanes_back(
        monkeypatch, tmp_path):
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.setattr(sf, "_harvest_instances", _no_harvest_strict())
    monkeypatch.setattr(sf, "_fetch",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ConnectionError("stubbed local lane")))
    monkeypatch.setattr(sf, "_direct_search",
                        lambda *a, **k: pytest.fail(
                            "direct lanes must not run when the proxy mode "
                            "cannot be honoured"))
    monkeypatch.setattr(sf, "browser_search_public",
                        lambda *a, **k: pytest.fail(
                            "browser lane must not run when the proxy mode "
                            "cannot be honoured"))
    res = sf.search("testsuite question", search_proxy="on")
    assert res["ok"] is False
    assert res["source"] == "none"
    assert res["error"] == "MISSING_ENVFILE"
    assert res["ladder"][0].startswith("search proxy: on requested "
                                       "but the env file is missing")
    assert "held back" in res["ladder"][0]
    assert res["ladder"][1:] == ["local", "searx.space", "direct"]


def test_fallback_env_override_decides_when_arg_unset(
        monkeypatch, tmp_path):
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.setenv("GREYSEARCH_SEARCH_PROXY", "on")
    calls = []
    monkeypatch.setattr(pm, "open_search", _fail_open_search(calls))
    monkeypatch.setattr(sf, "_harvest_instances", _no_harvest_strict())
    res = sf.search("testsuite question")
    assert res["error"] == "MISSING_ENVFILE"
    assert res["ladder"][0].startswith("search proxy: on requested")


# ---- openers really are per-call (urllib level, mocked) -------------------

def test_fallback_each_lane_builds_a_fresh_opener(monkeypatch, tmp_path):
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    builds = []

    class FakeProxyHandler:
        def __init__(self, cfg=None):
            self.cfg = cfg

    class FakeOpener:
        def open(self, req, timeout=None):
            raise RuntimeError("no network in tests")

    def fake_build(*handlers):
        builds.append(handlers[0].cfg if handlers else None)
        return FakeOpener()

    monkeypatch.setattr(pm.urllib.request, "ProxyHandler", FakeProxyHandler)
    monkeypatch.setattr(pm.urllib.request, "build_opener", fake_build)
    direct = sf._direct_search("testsuite question", 3, mode="off")
    assert direct == []
    assert builds == [{}, {}, {}, {}]


# ---- searxng_integrated manager ---------------------------------------

def _fake_fallback_factory(calls):
    def factory():
        def no_browser(*a, **k):
            raise RuntimeError("browser lane must be mocked out in "
                               "lane tests")
        return types.SimpleNamespace(
            browser_search_public=no_browser,
            _direct_search=lambda *a, **k: (
                calls.append("direct") or []),
            _warm_remote_host=lambda *a, **k: None,
            _filter_instance_junk=lambda *a, **k: [])
    return factory


def _patched_manager(monkeypatch, calls, discover=lambda *a, **k: []):
    mgr = si.SearXNGEngineManager(searxng_urls=["http://127.0.0.1:8888"])
    monkeypatch.setattr(si, "_local_alive", lambda *a, **k: False)
    monkeypatch.setattr(mgr, "get_working_engines", lambda: [])
    monkeypatch.setattr(mgr, "_discover_public_instances",
                        lambda max_instances=6, mode=None: (
                            calls.append(("discover", dict(mode or {}))) or
                            discover(max_instances, mode)))
    monkeypatch.setattr(si, "_searx_fallback", _fake_fallback_factory(calls))
    return mgr


def test_manager_off_mode_ladder_note_and_record(
        monkeypatch, tmp_path):
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    set_env_file(monkeypatch, tmp_path / "absent.env")
    calls = []
    mgr = _patched_manager(monkeypatch, calls)
    res = mgr.search("testsuite question", search_proxy="off")
    assert res["ladder"][0] == "proxy mode off: direct"
    assert res["search_proxy"] == "off"
    assert "search_proxy_error" not in res
    assert ("discover", {"ok": True, "mode": "off", "proxy": None,
                         "error": "", "env_file":
                         str(tmp_path / "absent.env")}) in calls
    assert calls.count("direct") == 1


def test_manager_on_fixture_values_reach_lane_openers(
        monkeypatch, tmp_path):
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    path = tmp_path / "proxy.env"
    val = write_fixture_env(path)
    set_env_file(monkeypatch, path)
    calls = []
    mgr = _patched_manager(monkeypatch, calls)
    res = mgr.search("testsuite question", search_proxy="on")
    assert res["ladder"][0] == pm.ON_LADDER_NOTE
    assert res["search_proxy"] == "on"
    assert ("discover", {"ok": True, "mode": "on",
                         "proxy": {"http": val, "https": val},
                         "error": "", "env_file": str(path)}) in calls
    assert calls.count("direct") == 1


def test_manager_on_missing_file_holds_all_external_lanes(
        monkeypatch, tmp_path):
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    set_env_file(monkeypatch, tmp_path / "absent.env")
    calls = []
    mgr = _patched_manager(monkeypatch, calls)
    res = mgr.search("testsuite question", search_proxy="on")
    assert res["ok"] is False
    assert res["search_proxy"] == "on"
    assert res["search_proxy_error"] == "MISSING_ENVFILE"
    assert "searx.space: held back (search proxy not honoured)" in res["ladder"]
    assert ("direct keyless: held back (search proxy not honoured)"
            in res["ladder"])
    assert ("searx.space browser: held back (search proxy not honoured)"
            in res["ladder"])
    assert not [c for c in calls if c and c[0] == "discover"]
    assert calls.count("direct") == 0
    ladder_text = " | ".join(res["ladder"])
    assert "SECRET" not in ladder_text


def test_manager_init_honors_env_override(monkeypatch, tmp_path):
    path = tmp_path / "proxy.env"
    val = write_fixture_env(path)
    monkeypatch.setattr(pm, "ENV_FILE", str(path))
    monkeypatch.setenv("GREYSEARCH_SEARCH_PROXY", "on")
    mgr = si.SearXNGEngineManager()
    assert mgr._proxy_res == {"ok": True, "mode": "on",
                              "proxy": {"http": val, "https": val},
                              "error": "", "env_file": str(path)}
    monkeypatch.setenv("GREYSEARCH_SEARCH_PROXY", "off")
    mgr = si.SearXNGEngineManager()
    assert mgr._proxy_res["mode"] == "off"


def test_manager_init_bad_arg_is_structured_record(monkeypatch):
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    mgr = si.SearXNGEngineManager(search_proxy="bogus-mode")
    assert mgr._proxy_res["ok"] is False
    assert mgr._proxy_res["error"] == pm.BAD_MODE


def test_manager_recheck_recovers_after_env_file_appears(
        monkeypatch, tmp_path):
    set_env_file(monkeypatch, tmp_path / "absent.env")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    mgr = si.SearXNGEngineManager(search_proxy="on")
    assert mgr._proxy_res["error"] == "MISSING_ENVFILE"
    path = tmp_path / "proxy.env"
    val = write_fixture_env(path)
    monkeypatch.setattr(pm, "ENV_FILE", str(path))
    res = mgr._proxy_resolution()
    assert res["ok"] is True
    assert res["proxy"] == {"http": val, "https": val}


# ---- scrape transports: never the proxy -----------------------------------

def test_bridge_scrape_result_carries_scrape_guard(monkeypatch):
    import research_integration as ri
    bridge = ri.get_bridge()
    monkeypatch.setattr(
        bridge.scraper, "fetch_url",
        lambda url: {"ok": True, "url": url, "final_url": url,
                     "title": "t", "text": "hello world content",
                     "raw_text": "hello world content", "html": "",
                     "html_capped": False, "links": [], "images": [],
                     "videos": [], "audios": [], "pdfs": [],
                     "canvases": [], "iframes": [], "meta": {},
                     "status_code": 200, "tables": [], "headings": [],
                     "code_blocks": [], "injection_flags": [],
                     "injection_scanned": True, "elapsed_s": 0.1})
    res = bridge.research_scrape("https://example.com/guide")
    assert res["scrape_transport"] == "scrape direct (no proxy)"


def test_scrape_guard_string_is_the_brief_line():
    assert pm.scrape_guard_note() == "scrape direct (no proxy)"


def test_bad_mode_is_a_structured_record_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    set_env_file(monkeypatch, tmp_path / "absent.env")
    calls = []
    mgr = _patched_manager(monkeypatch, calls)
    res = mgr.search("testsuite question", search_proxy="proxy9")
    assert res["search_proxy"] == ""
    assert res["search_proxy_error"] == pm.BAD_MODE
    assert ("search proxy: unavailable (BAD_MODE): external search lanes "
            "held back") == res["ladder"][0]
    assert calls.count("direct") == 0


def test_research_search_threads_mode_and_mirrors_result(monkeypatch):
    import research_integration as ri
    bridge = ri.get_bridge()
    captured = {}

    def fake_search(query=None, **kwargs):
        captured.update(kwargs)
        captured["query"] = query
        return {"ok": True, "results": [
            {"url": "https://example.one/proxy-lanes",
             "title": "proxy lanes",
             "content": "proxy lanes search-only transports",
             "engine": "testsuite"}] * 3,
            "ladder": ["search proxy: on "
                       "(external search lanes ride the proxy)"],
            "search_proxy": "on"}
    monkeypatch.setattr(bridge, "searxng_manager",
                        types.SimpleNamespace(search=fake_search,
                                              get_working_engines=lambda: []))
    res = bridge.research_search("proxy lanes testsuite",
                                 search_proxy="on")
    assert captured.get("search_proxy") == "on"
    assert res["search_proxy"] == "on"


def test_research_scrape_respects_upstream_transport_record(monkeypatch):
    import research_integration as ri
    bridge = ri.get_bridge()
    monkeypatch.setattr(
        bridge.scraper, "fetch_url",
        lambda url: {"ok": True, "scrape_transport":
                     "scrape direct (no proxy)"})
    res = bridge.research_scrape("https://example.com/again")
    assert res["scrape_transport"] == "scrape direct (no proxy)"
