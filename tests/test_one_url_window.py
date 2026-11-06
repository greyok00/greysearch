"""Regression tests for the one-URL reply window tunable.

Background: Medium-family pages legitimately spend navigate 60 s +
two-phase captcha wait 30+60 s + extraction timeouts before answering, but
the reply window was hardcoded 180 s. The driver saw busy while the job kept
running, and later rows waited behind the still-running scrape ("one-URL
scrape waited >180s behind an active run" cascade). The window is now the
GREYSEARCH_ONE_URL_REPLY_S knob (default 300 s, floor 30 s, junk falls back).

Runs in its own SUBPROCESS loading the server SOURCE from the repo tree
explicitly (an installed copy of the server module must not win the import,
and the pytest process must stay free of server singletons).
"""
from pathlib import Path
import os
import subprocess
import sys

REPO = str(Path(__file__).resolve().parents[1])
_MODULE = str(Path(REPO) / "greysearch_mcp.py")

_CODE = '''
import importlib.util, sys

spec = importlib.util.spec_from_file_location(
    "greysearch_mcp_source", sys.argv[-1])
gm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gm)

scenario = sys.argv[1]
val = gm._one_url_reply_s()
if scenario == "default":
    assert val == 300.0, f"default should be 300.0, got {val}"
elif scenario == "override":
    assert val == 420.0, f"override should be 420.0, got {val}"
elif scenario == "junk":
    assert val == 300.0, f"junk env should fall back to 300.0, got {val}"
elif scenario == "floor":
    assert val == 30.0, f"below-floor value should clamp to 30.0, got {val}"
else:
    raise SystemExit(f"unknown scenario {scenario}")
print("OK")
'''


def _run(scenario, env_val=None):
    env = dict(os.environ)
    env.pop("GREYSEARCH_ONE_URL_REPLY_S", None)
    if env_val is not None:
        env["GREYSEARCH_ONE_URL_REPLY_S"] = env_val
    r = subprocess.run([sys.executable, "-c", _CODE, scenario, _MODULE], env=env,
                       cwd=REPO, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2500:]


def test_window_default_300():
    _run("default")


def test_window_env_override():
    _run("override", "420")


def test_window_junk_env_falls_back():
    _run("junk", "not-a-number")


def test_window_clamps_to_floor():
    _run("floor", "5")
