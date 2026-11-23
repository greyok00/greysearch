"""Regression tests for the browser-slot launch path.

The real-world failure: Chromium died without removing its profile singleton
files. The next launch saw the stale lock, refused to start, and the slot was
reported broken even though the port and code were unchanged.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import browser_ports as bp


def _profile_with_lock(tmp_path: Path, pid: int) -> Path:
    profile = tmp_path / "profile"
    profile.mkdir()
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket",
                 "DevToolsActivePort"):
        (profile / name).symlink_to(f"parrot-{pid}")
    return profile


def test_dead_profile_lock_is_removed(tmp_path):
    profile = _profile_with_lock(tmp_path, 99999999)
    removed = bp._clean_stale_profile_locks(str(profile))
    assert "SingletonLock" in removed
    assert not (profile / "SingletonLock").exists()


def test_live_profile_lock_is_kept(tmp_path):
    profile = _profile_with_lock(tmp_path, os.getpid())
    removed = bp._clean_stale_profile_locks(str(profile))
    assert removed == []
    assert (profile / "SingletonLock").is_symlink()
