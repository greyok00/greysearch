#!/usr/bin/env python3
"""patchright_headless.py - THE launcher for a greysearch task-slot browser.

One process = ONE patchright-launched STEALTH Chromium, headless, with a
persistent per-slot profile, exposing a CDP HTTP endpoint on the slot port so
every driver in this session (MCP, bridge, fallback lane) attaches to the
same browser via connect_over_cdp. The parent (browser_ports.ensure) starts
this detached and probes the port until /json/version answers; this process
stays alive for the life of the browser - nothing here ever closes it.

Default headless (standing browser rule); --headful exists ONLY as an
explicit opt-in for sites that hard-fail headless.

CAPTCHA SOLVER, NOW LOCAL: the launcher loads NOTHING at launch time - no
third-party solve addon rides the chromium argv, and the retired loader and
key-seed surface is gone from this module entirely; the vendored extension
assets are purged from the repository too, which is what closed the
2026-11-05 audit finding. Solving is local and lives in captcha_solve.py (the rung ladder:
an already-resident model, a vendored CPU classifier, a gpu rung only when
explicitly allowed) behind the ONE knob, GREYSEARCH_CAPTCHA_SOLVER. The
detached launch path (browser_ports.ensure) forwards GREYSEARCH_* env vars
to this launcher so the knob resolves in the same process that launches
chromium, and the ready line answers with captcha_solver=local when the
knob allows solving (captcha_solve.solver_enabled) or off when the knob is
switched off. The WAIT behavior feeding that solver lives in
browser_session (GREYSEARCH_CAPTCHA_WAIT_S, GREYSEARCH_CAPTCHA_POLL_S):
launching the launcher never loops; the bounded wait does.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--headful", action="store_true",
                    help="explicit opt-in; default is headless")
    a = ap.parse_args()
    try:
        from patchright.sync_api import sync_playwright
    except Exception as e:
        print(f"patchright unavailable: {e}", file=sys.stderr)
        return 2

    profile = Path(a.profile).expanduser()
    profile.mkdir(parents=True, exist_ok=True)
    import captcha_solve as _cs
    solver = "local" if _cs.solver_enabled() else "off"
    # Default-setup rung 2026-11-23: Windows core fonts visible before the
    # browser starts (fingerprint: the JS-readable font list). Idempotent,
    # best-effort; a failure here never blocks the launch.
    try:
        import windows_fonts as _wf
        _fstat, _fnote = _wf.ensure()
        print(f"slot browser fonts: {_fstat} ({_fnote})", flush=True)
    except Exception as e:
        print(f"slot browser fonts: failed ({e})", file=sys.stderr, flush=True)
    try:
        import fp_shield as _fp_shield
        ua = _fp_shield.WINDOWS_UA
    except Exception:
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/156.0.0.0 Safari/537.36")
    argv = [
        f"--remote-debugging-port={a.port}",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "--no-pings",
        "--disable-crash-reporter",
        "--disable-component-update",
        "--disable-sync",
        "--disable-background-networking",
        "--disable-domain-reliability",
        "--disable-features=OptimizationHints,MediaRouter,Translate,OptimizationGuideFetchingForDesktop",
        "--enable-unsafe-extension-debugging",
    ]
    ext_dir = Path(__file__).resolve().parent / "shield_ext"
    # Owner order 2026-11-23: UA switcher (Windows Chromium) + fingerprint
    # blocker plugin on by default. Playwright's bundled chromium and any
    # headless mode both suppress unpacked content-script injection
    # (verified 2026-11-23), so the slot browser runs the SYSTEM chromium
    # HEADFUL on its own Xvfb display and the shield rides in as a
    # MAIN-world content script installed over raw CDP.
    import os as _os
    chrome_bin = _os.environ.get("GREYSEARCH_SLOT_CHROME") or "/usr/bin/chromium"
    use_system = _os.path.exists(chrome_bin)
    launch_kwargs = {}
    if use_system:
        launch_kwargs["executable_path"] = chrome_bin
    if use_system:
        # Direct subprocess launch: patchright's DRIVER suppresses extension
        # content scripts even on the system binary (verified 2026-11-23),
        # and system-chromium HEADLESS blocks unpacked content-script
        # injection too (verified 2026-11-23; headful injects fine). So the
        # launcher spawns its own Xvfb display and runs chromium HEADFUL
        # there: the shield plugin injects, nothing appears on the real
        # desktop, and clients still attach with patchright connect_over_cdp.
        import subprocess as _sp
        xvfb_proc = None
        display = None
        if a.headful:
            display = __import__("os").environ.get("DISPLAY", ":0")
        else:
            display = _os.environ.get("GREYSEARCH_SLOT_XVFB") or ":110"
            xvfb_proc = _sp.Popen(["/usr/bin/Xvfb", display, "-screen", "0",
                                   "1920x1080x24"], stdout=_sp.DEVNULL,
                                  stderr=_sp.DEVNULL)
        # GPU default 2026-11-23: GREYSEARCH_BROWSER_GPU auto|on|off.
        # auto/on leave the GPU stack at its normal default (ANGLE -> Mesa /
        # the driver's renderer) so WebGL reports a normal vendor string;
        # "off" pins the old --disable-gpu + SwiftShader behavior. The
        # SwiftShader string itself is a known bot signal, so auto is the
        # shipping default. There is no chromium switch that picks the
        # integrated chip specifically - the driver chooses; "on" and
        # "auto" both mean whatever the machine has.
        _gpu = (_os.environ.get("GREYSEARCH_BROWSER_GPU") or "auto").lower()
        _gpu_argv = []
        if _gpu in ("off", "0", "false", "none"):
            _gpu_argv = ["--disable-gpu"]
        elif _gpu in ("auto", ""):
            # auto = guaranteed-working software WebGL (ANGLE/SwiftShader);
            # the shield extension paints a normal Intel GPU string over the
            # pages it covers, so the renderer reads like a real laptop.
            _gpu_argv = ["--use-gl=angle", "--use-angle=swiftshader"]
        # "on" (or any other value) = leave the GPU stack at chromium's
        # normal default so a real driver/GLX is used when present.
        full_argv = argv + _gpu_argv + [f"--user-agent={ua}",
                            f"--user-data-dir={profile}",
                            "--window-size=1920,1080",
                            "--lang=en-US"]
        try:
            _env = {**_os.environ, "TZ": "America/Phoenix",
                    "DISPLAY": display}
            _sp.Popen([chrome_bin] + full_argv, stdout=None, stderr=None,
                      env=_env)
        except Exception as e:
            print(f"system chromium spawn failed: {e}", file=sys.stderr)
            use_system = False
    launched = None
    if not use_system:
        pw = sync_playwright().start()
        launch_opts = dict(
            user_data_dir=str(profile),
            headless=not a.headful,
            viewport={"width": 1920, "height": 1080},
            java_script_enabled=True,
            locale="en-US",
            timezone_id="America/Phoenix",
            args=argv + ["--headless=new", f"--user-agent={ua}"],
        )
        try:
            launched = pw.chromium.launch_persistent_context(
                channel="chromium", **launch_kwargs, **launch_opts)
        except Exception as e:
            print(f"launch failed: {e}", file=sys.stderr)
            return 3
    # NOTE: the launcher deliberately does NOT call fp_shield here. Any
    # add_init_script made through the launcher's own playwright connection
    # wedges navigations issued by later connect_over_cdp clients (verified
    # by bisect 2026-11-23, empty script also fails). The ATTACHING driver
    # (browser_session) applies the shield on its own connection instead.

    if ext_dir.is_dir():
        try:
            import json as _json
            import urllib.request as _ur
            import websocket as _ws
            deadline = time.time() + 25
            meta = None
            while time.time() < deadline:
                try:
                    meta = _json.loads(_ur.urlopen(
                        f"http://127.0.0.1:{a.port}/json/version",
                        timeout=3).read())
                    break
                except Exception:
                    time.sleep(1)
            if meta is None:
                raise RuntimeError("CDP port never answered")
            sock = _ws.create_connection(meta["webSocketDebuggerUrl"],
                                         suppress_origin=True)
            sock.send(_json.dumps({"id": 1, "method": "Extensions.loadUnpacked",
                                   "params": {"path": str(ext_dir)}}))
            deadline = time.time() + 10
            while time.time() < deadline:
                r = _json.loads(sock.recv())
                if r.get("id") == 1:
                    ok = "result" in r
                    print(f"shield_ext install: {'ok' if ok else r}",
                          flush=True)
                    break
            sock.close()
        except Exception as e:
            print(f"shield_ext install skipped: {e}", flush=True)

    print(f"greysearch slot browser ready: port={a.port} "
          f"profile={profile} headless={not a.headful} "
          f"captcha_solver={solver}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0

if __name__ == "__main__":
    sys.exit(main())