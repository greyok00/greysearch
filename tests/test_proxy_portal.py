"""Portal + MCP surface tests for manual search-proxy mode (no network,
no live service, no runs/ or guides/ writes: row writes and threads are
stubbed)."""
import json
import re
import threading
import types
from pathlib import Path

import pytest

import proxy_mode as pm
import portal_server as ps
from conftest import write_fixture_env, set_env_file


@pytest.fixture()
def client(monkeypatch, tmp_path):
    set_env_file(monkeypatch, tmp_path / "proxy.env")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    rows = []
    threads = []

    class FakeThread(threading.Thread):
        def __init__(self, target=None, args=(), **kw):
            threads.append({"target": target, "args": list(args)})

        def start(self):
            pass

    monkeypatch.setattr(ps, "threading", thread_stub_module(FakeThread))
    monkeypatch.setattr(ps, "_row_write",
                        lambda run_id, run: rows.append(
                            (run_id, dict(run))))
    return ps.app.test_client(), threads, rows


def thread_stub_module(fake_thread_cls):
    """A minimal stand-in for the threading module used by /api/research."""
    stub = types.ModuleType("threading")
    stub.Thread = fake_thread_cls
    stub.Lock = threading.Lock
    stub.Queue = None
    return stub


def _post(client, payload):
    return client.post("/api/research", json=payload)


# ---- validation -------------------------------------------------------

def test_unknown_mode_is_rejected_with_bad_mode(client):
    c, threads, rows = client
    r = _post(c, {"query": "testsuite question", "search_proxy": "proxy9"})
    assert r.status_code == 400
    body = json.loads(r.data)
    assert body["code"] == "BAD_MODE"
    assert threads == [] and rows == []


def test_on_alias_maps_to_on(client, tmp_path):
    c, threads, rows = client
    write_fixture_env(tmp_path / "proxy.env")
    r = _post(c, {"query": "testsuite question", "search_proxy": "on"})
    assert r.status_code == 200
    body = json.loads(r.data)
    assert body["search_proxy"] == "on"


def test_off_default_is_explicit(client):
    c, threads, rows = client
    r = _post(c, {"query": "testsuite question", "search_proxy": "off"})
    assert r.status_code == 200
    body = json.loads(r.data)
    assert body["search_proxy"] == "off"
    run_id = body["run_id"]
    assert threads[0]["args"][6] == "off"
    with ps._RUNS_LOCK:
        assert ps._RUNS[run_id]["search_proxy"] == "off"
    assert rows and rows[0][1]["search_proxy"] == "off"


def test_unset_defaults_to_off_body_row_and_thread(client):
    c, threads, rows = client
    r = _post(c, {"query": "testsuite question"})
    assert r.status_code == 200
    body = json.loads(r.data)
    assert body["search_proxy"] == "off"
    with ps._RUNS_LOCK:
        assert ps._RUNS[body["run_id"]]["search_proxy"] == "off"
    # unset + no env override resolves to off at the endpoint, and the
    # runner records the same resolved mode on the row + the start event.
    assert threads[0]["args"][6] == "off"


def test_on_accepted_with_fixture_env_file(client, tmp_path):
    c, threads, rows = client
    write_fixture_env(tmp_path / "proxy.env")
    r = _post(c, {"query": "testsuite question", "search_proxy": "on"})
    assert r.status_code == 200
    body = json.loads(r.data)
    assert body["search_proxy"] == "on"
    run_id = body["run_id"]
    with ps._RUNS_LOCK:
        live = dict(ps._RUNS[run_id])
    assert live["search_proxy"] == "on"
    assert not live.get("search_proxy_error")
    assert threads[0]["args"][6] == "on"
    assert rows and rows[0][1]["search_proxy"] == "on"
    r2 = c.get(f"/api/run/{run_id}")
    assert r2.status_code == 200


def test_unset_with_env_override_echoes_on(client, tmp_path,
                                                monkeypatch):
    c, threads, rows = client
    write_fixture_env(tmp_path / "proxy.env")
    monkeypatch.setenv("GREYSEARCH_SEARCH_PROXY", "on")
    r = _post(c, {"query": "testsuite question"})
    assert r.status_code == 200
    body = json.loads(r.data)
    assert body["search_proxy"] == "on"
    with ps._RUNS_LOCK:
        assert ps._RUNS[body["run_id"]]["search_proxy"] == "on"
    assert threads[0]["args"][6] == "on"


def test_on_missing_env_file_structured_503(client, tmp_path):
    c, threads, rows = client
    (tmp_path / "proxy.env").unlink(missing_ok=True)
    r = _post(c, {"query": "testsuite question", "search_proxy": "on"})
    assert r.status_code == 503
    body = json.loads(r.data)
    assert body["code"] == "MISSING_ENVFILE"
    assert "held" in body["error"] or "unavailable" in body["error"]
    assert threads == [] and rows == []


def test_on_env_file_without_proxy_values_503(client, tmp_path):
    c, threads, rows = client
    (tmp_path / "proxy.env").write_text("# empty\n", encoding="utf-8")
    r = _post(c, {"query": "testsuite question", "search_proxy": "on"})
    assert r.status_code == 503
    assert json.loads(r.data)["code"] == "NO_PROXY_VALUES"


HERE = Path(__file__).resolve().parent.parent
PS_SRC = HERE / "portal_server.py"
PX_HTML = HERE / "portal" / "index.html"


def test_row_meta_search_proxy_key_is_recorded():
    """Static check: the row meta carries search_proxy (defaults to off)."""
    text = PS_SRC.read_text(encoding="utf-8")
    assert '"search_proxy": run.get("search_proxy") or "off"' in text


def test_runner_resolves_mode_and_seeds_row(tmp_path, monkeypatch):
    """_runner records the RESOLVED mode on the row + the start event."""
    import queue
    run_id = "testsuite-id-003"
    with ps._RUNS_LOCK:
        ps._RUNS[run_id] = {"status": "running", "events": [], "epoch": 99,
                            "queue": queue.Queue(),
                            "result": None, "query": "q"}
    set_env_file(monkeypatch, tmp_path / "proxy.env")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    emitted = []

    def fake_emit(rid, stage, data):
        emitted.append((stage, dict(data or {})))
        # a stale epoch marks the attempt abandoned as soon as it emits,
        # so no browser/research phase ever starts
        raise RuntimeError("stop here")

    monkeypatch.setattr(ps, "_emit", fake_emit)
    try:
        ps._runner(run_id, "testsuite question", "standard", 4,
                   None, None, "on")
    except RuntimeError:
        pass
    with ps._RUNS_LOCK:
        row = ps._RUNS[run_id]
    assert row["search_proxy"] == "on"
    assert emitted and emitted[0][0] == "start"
    assert emitted[0][1]["search_proxy"] == "on"
    with ps._RUNS_LOCK:
        ps._RUNS.pop(run_id, None)


def test_runner_env_override_reflected_in_row(tmp_path, monkeypatch):
    import queue
    run_id = "testsuite-id-004"
    with ps._RUNS_LOCK:
        ps._RUNS[run_id] = {"status": "running", "events": [], "epoch": 99,
                            "queue": queue.Queue(),
                            "result": None, "query": "q"}
    path = tmp_path / "proxy.env"
    write_fixture_env(path)
    set_env_file(monkeypatch, path)
    monkeypatch.setenv("GREYSEARCH_SEARCH_PROXY", "on")

    def stop_emit(rid, stage, data):
        raise RuntimeError("stop here")

    monkeypatch.setattr(ps, "_emit", stop_emit)
    try:
        try:
            ps._runner(run_id, "testsuite question", "standard", 4,
                       None, None, None)
        except RuntimeError:
            pass
        with ps._RUNS_LOCK:
            row = ps._RUNS[run_id]
        assert row["search_proxy"] == "on"
    finally:
        with ps._RUNS_LOCK:
            ps._RUNS.pop(run_id, None)


def test_runner_resolved_mode_wins_when_arg_off_but_override_on(
        tmp_path, monkeypatch):
    import queue
    run_id = "testsuite-id-005"
    with ps._RUNS_LOCK:
        ps._RUNS[run_id] = {"status": "running", "events": [], "epoch": 99,
                            "queue": queue.Queue(),
                            "result": None, "query": "q"}
    path = tmp_path / "proxy.env"
    write_fixture_env(path)
    monkeypatch.setattr(pm, "ENV_FILE", str(path))
    monkeypatch.setenv("GREYSEARCH_SEARCH_PROXY", "on")

    def stop_emit(rid, stage, data):
        raise RuntimeError("stop here")

    monkeypatch.setattr(ps, "_emit", stop_emit)
    try:
        try:
            ps._runner(run_id, "testsuite question", "standard", 4,
                       None, None, "off")
        except RuntimeError:
            pass
        with ps._RUNS_LOCK:
            row = ps._RUNS[run_id]
        assert row["search_proxy"] == "on"
    finally:
        with ps._RUNS_LOCK:
            ps._RUNS.pop(run_id, None)


# ---- UI control presence (static check of portal/index.html) ----------

def test_ui_proxy_control_present():
    text = PX_HTML.read_text(encoding="utf-8")
    assert 'class="pxy sel" data-proxy="off"' in text
    assert 'class="pxy" data-proxy="on"' in text
    assert "Proxy: off | on" in text
    # separate handler class: the .pxy picker must not be selected by
    # the .dep sweep (the length picker wipes .dep selections only)
    dep_sweep = re.search(r'querySelectorAll\("\.dep"\)', text)
    assert dep_sweep
    assert text.index("greysearch-search-proxy") > 0
    assert "PXY_KEY = \"greysearch-search-proxy\"" in text
    assert "localStorage.setItem(PXY_KEY, _proxy)" in text


def test_ui_submit_body_carries_search_proxy():
    text = PX_HTML.read_text(encoding="utf-8")
    assert "search_proxy: _proxy" in text


def test_ui_start_line_shows_the_mode():
    text = PX_HTML.read_text(encoding="utf-8")
    assert '(proxy: ' in text and 'search_proxy' in text


def test_ux_note_says_searches_only_no_em_dashes():
    text = PX_HTML.read_text(encoding="utf-8")
    note = re.search(r"id=\"pxy-note\">([^<]*)", text)
    assert note and note.group(1).startswith("Proxy mode applies to "
                                             "SEARCHES only")
    assert chr(0x2014) not in note.group(1)


# ---- greysearch_mcp surface -------------------------------------------

def test_mcp_dispatch_validates_bad_mode():
    import greysearch_mcp as gm
    res = gm.dispatch("greysearch",
                      {"query": "testsuite question",
                       "search_proxy": "proxy9"})
    assert res["ok"] is False
    assert res["code"] == pm.BAD_MODE
    assert res["accepted"] == ["off", "on"]


def test_mcp_tool_schema_declares_search_proxy():
    import greysearch_mcp as gm
    tool = next(t for t in gm.TOOLS
                if t.get("name") == "greysearch")
    prop = tool["inputSchema"]["properties"]["search_proxy"]
    assert prop["enum"] == ["off", "on"]
    assert isinstance(prop.get("description"), str)


def test_mcp_health_reports_the_mode(monkeypatch, tmp_path):
    import greysearch_mcp as gm
    import searxng_integrated as si
    from conftest import set_env_file
    set_env_file(monkeypatch, tmp_path / "health-absent.env")
    monkeypatch.delenv("GREYSEARCH_SEARCH_PROXY", raising=False)
    monkeypatch.delenv("GREYSEARCH_INSTANCE_PROBES", raising=False)
    monkeypatch.setitem(gm._PROBE_CACHE, "at", 0.0)
    monkeypatch.setitem(gm._PROBE_CACHE, "instances", None)
    monkeypatch.setattr(
        si.SearXNGEngineManager, "_discover_public_instances",
        lambda self, max_instances=3, mode=None: [])
    res = gm.dispatch("greysearch_health", {})
    assert "search_proxy" in res
    assert res.get("search_proxy", "") in ("off", "on", "")
    assert isinstance(res.get("search_proxy_note"), str)
