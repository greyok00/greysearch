#!/usr/bin/env python3
"""windows_fonts.py - make the slot browser see Windows type by default.

The slot browser must fingerprint like a Windows Chrome, and the font list
canvas/JS can read is part of that. This module is the ONE default-setup
rung for fonts: it verifies the Microsoft core fonts (Arial, Times New
Roman, Verdana, Georgia, Trebuchet, Courier New, Comic Sans, Impact, Web
dings, Arial Black) are visible to fontconfig; when they are not, it
downloads the freely-redistributable corefonts archives from their
official source (sourceforge corefonts project), prints the EULA notice
instead of burying it, extracts them into ~/.local/share/fonts (a
per-user path fontconfig scans with no root and no system changes), and
refreshes the cache. Nothing is vendored or redistributed by this repo -
the archives are fetched from their own source at install time.

Knobs:
  GREYSEARCH_WINDOWS_FONTS  off/0/false = never touch anything (default on)
Return: (status, note) - status "ok" (fonts present), "installed",
"skipped", or "failed" (network/tool missing; never fatal to the caller).
"""
from __future__ import annotations

import os
import shutil
import subprocess

_FONTS = (
    "arial32.exe", "arialb32.exe", "comic32.exe", "courie32.exe",
    "georgi32.exe", "impact32.exe", "times32.exe", "trebuc32.exe",
    "verdan32.exe", "webdin32.exe",
)
_BASE = "https://downloads.sourceforge.net/project/corefonts/the%20fonts/final/"
_MARKERS = ("arial.ttf", "times.ttf", "verdana.ttf", "georgia.ttf")
_INSTALL_DIR = os.path.join(os.path.expanduser("~"), ".local/share/fonts/greysearch-windows")
_EULA = ("Microsoft core fonts: these files are fetched from the corefonts "
         "sourceforge project under the Microsoft core fonts EULA, which "
         "permits redistribution of the original installers; by continuing "
         "the install you accept that license on this machine.")


def _fonts_visible() -> bool:
    try:
        r = subprocess.run(["fc-list"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=30)
    except Exception:
        return False
    text = r.stdout.decode(errors="ignore")
    return sum(1 for m in _MARKERS if m in text) >= 4


def ensure() -> tuple[str, str]:
    """Idempotent: verify-then-maybe-install. Never raises."""
    if os.environ.get("GREYSEARCH_WINDOWS_FONTS", "").strip().lower() in \
            ("off", "0", "false", "no"):
        return "skipped", "GREYSEARCH_WINDOWS_FONTS off"
    if _fonts_visible():
        return "ok", "core fonts already visible"

    try:
        os.makedirs(_INSTALL_DIR, exist_ok=True)
        print(_EULA, flush=True)
        have_cab = bool(shutil.which("cabextract") or shutil.which("bsdtar"))
        if not have_cab:
            return "failed", "no cabextract/bsdtar to unpack the font archives"
        import urllib.request
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            for name in _FONTS:
                dest = os.path.join(td, name)
                try:
                    urllib.request.urlretrieve(_BASE + name, dest)
                except Exception as exc:
                    return "failed", f"download {name}: {exc}"
                cab = subprocess.run(
                    ["cabextract", "-q", "-L", "-d", td, dest],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=120)
                if cab.returncode != 0:
                    return "failed", f"unpack {name} failed"
            for f in os.listdir(td):
                if f.lower().endswith((".ttf", ".ttc")):
                    shutil.copy2(os.path.join(td, f),
                                 os.path.join(_INSTALL_DIR, f))
        subprocess.run(["fc-cache", "-f", _INSTALL_DIR],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=300)
        if _fonts_visible():
            return "installed", f"core fonts in {_INSTALL_DIR}"
        return "failed", "installed but fontconfig still cannot see them"
    except Exception as exc:
        return "failed", str(exc)[:200]


if __name__ == "__main__":
    status, note = ensure()
    print(f"{status}: {note}")
