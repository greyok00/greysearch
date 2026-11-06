#!/usr/bin/env python3
import sys
path = sys.argv[1]
src = open(path, encoding='utf-8').read()
applied = []

def replace(name, old, new, expect=1):
    global src
    if new.split('\n')[0] in src or old not in src:
        if old not in src:
            applied.append(f'{name}: SKIP (already applied or absent)')
            return
    count = src.count(old)
    assert count == expect, f'{name}: expected {expect} match(es), found {count}'
    src = src.replace(old, new)
    applied.append(f'{name}: OK')
replace('slots-12', '_TASK_RE = re.compile(r"^(?:task-)?([1-4])$", re.IGNORECASE)', '_TASK_RE = re.compile(r"^(?:task-)?([1-9]|1[0-2])$", re.IGNORECASE)')
replace('slot-order-12', '_SLOT_ORDER = ("task-1", "task-2", "task-3", "task-4")', '_SLOT_ORDER = tuple(f"task-{n}" for n in range(1, 13))')
if 'def _reap_orphan_browser' not in src:
    old_marker = 'def _slot_busy_reason(tid: str, port: int) -> str:'
    new_block = 'def _reap_orphan_browser(tid: str, port: int) -> bool:\n    """Kill a dead-session slot browser. Scoped: only processes whose cmdline\n    carries THIS slot\'s profile dir or this slot\'s port. Never touches another\n    slot or another session\'s browser. True when the port stops answering."""\n    pids: set = set()\n    profile_dir = os.path.join(str(Path.home()), ".cache", "greysearch",\n                               f"{tid}-profile")\n    for pattern in (profile_dir, f"remote-debugging-port={port}"):\n        try:\n            r = subprocess.run(["pgrep", "-f", pattern],\n                               capture_output=True, text=True, timeout=10)\n            for line in r.stdout.split("\\n"):\n                line = line.strip()\n                if line.isdigit():\n                    pids.add(int(line))\n        except Exception:\n            pass\n    pids.discard(os.getpid())\n    if not pids:\n        return not _cdp_answers(port)\n    for pid in sorted(pids, reverse=True):\n        try:\n            os.kill(pid, 15)\n        except Exception:\n            continue\n    time.sleep(1.0)\n    survivors = []\n    for pid in sorted(pids, reverse=True):\n        try:\n            os.kill(pid, 0)\n            survivors.append(pid)\n        except Exception:\n            pass\n    for pid in survivors:\n        try:\n            os.kill(pid, 9)\n        except Exception:\n            pass\n    time.sleep(0.5)\n    return not _cdp_answers(port)\n\n\ndef _slot_busy_reason(tid: str, port: int) -> str:'
    assert src.count(old_marker) == 1
    src = src.replace(old_marker, new_block)
    applied.append('reaper-fn: OK')
else:
    applied.append('reaper-fn: present')
replace('slot-busy-reap', '    if not entry and _cdp_answers(port):\n        return f"a browser is already running on {tid} port {port} (unregistered)"\n    return ""', '    if not entry and _cdp_answers(port):\n        return f"a browser is already running on {tid} port {port} (unregistered)"\n    if entry and _cdp_answers(port):\n        if not _reap_orphan_browser(tid, port):\n            return (f"a browser is still answering on {tid} port {port} "\n                    f"after an orphan-reap attempt (registered owner is dead)")\n    return ""')
replace('bind-docstring', 'f"bad task_id {task_id!r}: use \'task-1\'..\'task-4\' (or \'1\'..\'4\')"', 'f"bad task_id {task_id!r}: use \'task-1\'..\'task-12\' (or \'1\'..\'12\')"')
if 'runs_dir = Path(__file__).resolve().parent' not in src:
    old = '        if len(_RUNS) > _RUNS_KEEP:\n            old = sorted(_RUNS, key=lambda k: _RUNS[k].get("started_ts", 0))[:-_RUNS_KEEP]\n            for rid in old:\n                _RUNS.pop(rid, None)\n'
    new = '        if len(_RUNS) > _RUNS_KEEP:\n            old = sorted(_RUNS, key=lambda k: _RUNS[k].get("started_ts", 0))[:-_RUNS_KEEP]\n            for rid in old:\n                _RUNS.pop(rid, None)\n        try:\n            _runs_dir = Path(__file__).resolve().parent / "runs"\n            _runs_dir.mkdir(exist_ok=True)\n            (_runs_dir / f"{run_id}.json").write_text(\n                json.dumps(d, default=str), encoding="utf-8")\n        except Exception:\n            pass\n'
    assert src.count(old) == 1
    src = src.replace(old, new)
    applied.append('set-run-persist: OK')
else:
    applied.append('set-run-persist: present')
if 'thread_name_prefix="deepwatch"' not in src:
    old = '    try:\n        result = _execute_deep(query, categories, max_results, budget, pinned=pinned)\n        result = _attach_guide(result, query, write_path)'
    new = '    try:\n        _watch = ThreadPoolExecutor(max_workers=1, thread_name_prefix="deepwatch")\n        _sub = _watch.submit(_execute_deep, query, categories, max_results, budget, pinned)\n        try:\n            result = _sub.result(timeout=float(budget) + 240.0)\n        except _FutTimeout:\n            try:\n                _m = _TASK_RE.search(str(slot_note))\n                if _m:\n                    _rt = f"task-{_m.group(1)}"\n                    import browser_ports as _bp\n                    _rp = int(_bp.resolve(task=_rt)["port"])\n                    _reap_orphan_browser(_rt, _rp)\n            except Exception:\n                pass\n            try:\n                _watch.shutdown(wait=False)\n            except Exception:\n                pass\n            _set_run(run_id, status="failed",\n                     error="watchdog: deep run over budget +240s (lane wedged); "\n                           "slot browser reaped; the leaked worker thread exits "\n                           "with the process",\n                     elapsed_s=round(time.time() - started, 1))\n            return\n        finally:\n            _watch.shutdown(wait=False)\n        result = _attach_guide(result, query, write_path)'
    assert src.count(old) == 1
    src = src.replace(old, new)
    applied.append('watchdog: OK')
else:
    applied.append('watchdog: present')
if '_RUNS[run_id] = {' not in src:
    applied.append('dispatch-start-record: present')
else:
    old = '        run_id = f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"\n        with _RUN_GUARD:\n            _RUNS[run_id] = {\n                "run_id": run_id,\n                "query": query,\n                "status": "running",\n                "started_ts": time.time(),\n                "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),\n                "guide_path": str(write_path),\n                "slot": slot_note,\n            }'
    new = '        run_id = f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"\n        _set_run(run_id, run_id=run_id, query=query, status="running",\n                 started_ts=time.time(),\n                 started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),\n                 guide_path=str(write_path), slot=slot_note)'
    assert src.count(old) == 1
    src = src.replace(old, new)
    applied.append('dispatch-start-record: OK')
open(path, 'w', encoding='utf-8').write(src)
print('\n'.join(applied))
print('PATCHED:', path)
