"""Regression tests for the MCP reply writer.

Background: during first-spawn scrapes something in the runtime can flip fd 1
to non-blocking, under which a large reply died with BlockingIOError on flush,
killing the whole server mid-session. reply() must deliver the full frame
under ANY fd-1 blocking mode and pipe pressure without raising, and the frame
must stay a single JSON line with a trailing newline.

Each scenario runs in its own SUBPROCESS loading the server SOURCE from the
repo tree explicitly (an installed copy of the server module must not win
the import): importing the server inside the pytest process leaked
module-level singleton state into later test files, so the runner is
isolated per scenario by construction.
"""
import subprocess
import sys
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[1])
_MODULE = str(Path(REPO) / "greysearch_mcp.py")

_CODE = '''
import importlib.util, json, os, sys, threading, time, types
kind = sys.argv[1]
nonblocking = sys.argv[2] == "True"

spec = importlib.util.spec_from_file_location(
    "greysearch_mcp_source", sys.argv[-1])
gm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gm)

rfd, wfd = os.pipe()
if nonblocking:
    os.set_blocking(wfd, False)
os.set_blocking(rfd, False)

read = {"buf": b""}

def _reader():
    while True:
        try:
            chunk = os.read(rfd, 65536)
        except BlockingIOError:
            time.sleep(0.01)
            continue
        except OSError:
            break
        if not chunk:
            break
        read["buf"] += chunk

if kind == "result":
    payload = {"ok": True, "text": "y" * 300000}
else:
    payload = None

t = threading.Timer(0.4, _reader)
t.start()
old_stdout = sys.stdout
sys.stdout = types.SimpleNamespace(fileno=lambda: wfd)
try:
    if payload is None:
        gm.reply(9, error={"code": -32000, "message": "boom" * 100})
    else:
        gm.reply(7, result=payload)
finally:
    sys.stdout = old_stdout
    os.close(wfd)
t.join(timeout=30)
os.close(rfd)

buf = read["buf"]
frames = [ln for ln in buf.split(b"\\n") if ln]
assert len(frames) == 1, f"expected one frame, got {len(frames)}"
frame = json.loads(frames[0])
if payload is None:
    assert frame["id"] == 9 and frame["error"]["code"] == -32000
else:
    assert frame["id"] == 7 and frame["result"]["ok"] is True
    assert len(frame["result"]["text"]) == 300000
assert buf.endswith(b"\\n")
print("OK")
'''


def _run(kind, nonblocking):
    r = subprocess.run([sys.executable, "-c", _CODE, kind, str(nonblocking),
                        _MODULE],
                       cwd=REPO, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2500:]


def test_reply_result_frame_blocking_pipe_pressure():
    _run("result", False)


def test_reply_result_frame_nonblocking_pipe_pressure():
    _run("result", True)


def test_reply_error_frame_nonblocking_pipe_pressure():
    _run("error", True)
