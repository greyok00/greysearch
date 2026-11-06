"""Regression tests for the self-claim slot rule.

Failure mode under regression: a driver whose MCP child re-bound its OWN slot reaped its OWN
browser (the busy check reaped "answered port + registered entry" without
checking whose the entry is), so every other URL call died with
TargetClosedError in an alternating ok/err ping-pong. Now a claim whose pid is
this process is ours and free: busy="", and no reap may ever fire on it.

Each scenario runs in its own SUBPROCESS loading the server SOURCE from the
repo tree explicitly (an installed copy of the server module must not win
the import): importing the server inside the pytest process leaked
module-level singleton state into later test files in the same run, so
isolation is per-scenario by construction.
"""
from pathlib import Path
import subprocess
import sys

REPO = str(Path(__file__).resolve().parents[1])
_MODULE = str(Path(REPO) / "greysearch_mcp.py")

_CODE = '''
import importlib.util, os, sys, traceback

spec = importlib.util.spec_from_file_location(
    "greysearch_mcp_source", sys.argv[-1])
gm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gm)

calls = []
gm._reap_orphan_browser = lambda tid, port: calls.append((tid, port)) or True
gm._slot_reg_entry = lambda tid: {"pid": int(os.environ["CLAIM_PID"]),
                                  "owner_session": "unknown-session",
                                  "port": 9353, "task": "task-3"}
gm._cdp_answers = lambda port: True

def entry(pid):
    return {"pid": pid, "owner_session": "unknown-session",
            "port": 9353, "task": "task-3"}

scenario = sys.argv[1]
if scenario == "self":
    # Our own slot claim, our own live pid, browser answers: free, no reap.
    gm._slot_reg_entry = lambda tid: entry(os.getpid())
    gm._cdp_answers = lambda port: True
    reason = gm._slot_busy_reason("task-3", 9353)
    assert reason == "", f"self claim not free: {reason!r}"
    assert calls == [], f"our own slot/browser was reaped: {calls}"
elif scenario == "foreign":
    # A live foreign session still owns it: refused, never reaped.
    gm._slot_reg_entry = lambda tid: entry(99999999)
    gm._cdp_answers = lambda port: True
    gm.os_kill_probe = None
    real_kill = os.kill
    os.kill = lambda pid, sig: None
    try:
        reason = gm._slot_busy_reason("task-3", 9353)
    finally:
        os.kill = real_kill
    assert "owned by live session" in reason, f"foreign not refused: {reason!r}"
    assert calls == [], f"live foreign slot was reaped: {calls}"
elif scenario == "dead":
    # Control: dead-owner claim with an answering browser is an orphan; the
    # reaper fires and success unblocks.
    def _raise(pid, sig):
        raise ProcessLookupError()
    gm._slot_reg_entry = lambda tid: entry(99999999)
    gm._cdp_answers = lambda port: True
    real_kill = os.kill
    os.kill = _raise
    try:
        reason = gm._slot_busy_reason("task-3", 9353)
    finally:
        os.kill = real_kill
    assert reason == "", f"dead-owner orphan not reaped: {reason!r}"
    assert calls == [("task-3", 9353)], f"reaper not invoked: {calls}"
else:
    raise SystemExit(f"unknown scenario {scenario}")
print("OK")
'''


def _run(scenario):
    r = subprocess.run([sys.executable, "-c", _CODE, scenario, _MODULE],
                       cwd=REPO, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2500:]


def test_self_claim_is_free_and_never_reaped():
    _run("self")


def test_live_foreign_owner_refused():
    _run("foreign")


def test_dead_owner_with_live_browser_reaped():
    _run("dead")
