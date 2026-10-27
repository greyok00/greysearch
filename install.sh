#!/usr/bin/env bash
# Greysearch installer.
#
# Checks the interpreter matches the compiled modules, installs runtime
# dependencies, downloads the Chromium build patchright drives, and runs the
# self-check. Nothing here needs root, and nothing is written outside the
# directory you run it from except the pip and patchright caches.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

fail() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
note() { printf '\033[36m%s\033[0m\n' "$*"; }

PY="${PYTHON:-python3}"

# The .so files are built for CPython 3.13 x86-64. An ABI mismatch produces a
# confusing ImportError later, so refuse early with a clear message.
"$PY" - <<'PYEOF' || fail "Greysearch needs CPython 3.13 on x86-64 Linux"
import platform, sys
ok = sys.version_info[:2] == (3, 13) and platform.machine() in ("x86_64", "AMD64") \
     and sys.platform.startswith("linux")
print(f"interpreter: {sys.version.split()[0]} {platform.machine()} {sys.platform}")
sys.exit(0 if ok else 1)
PYEOF

# A directory named .venv is not proof of a usable venv: if .venv exists but
# .venv/bin/python does not, activating it falls through to the system
# interpreter and pip would install there. Test for an EXECUTABLE
# .venv/bin/python instead; anything less (empty dir, half-deleted venv, wrong
# layout) gets freshly created. python3 -m venv on an existing directory only
# rewrites the venv metadata in place, it does not delete user content, so a
# malformed venv is repaired rather than wiped. A venv that DOES have
# bin/python but was built for a different interpreter still activates fine
# and is caught by the revalidation block below, which tells the user rm it.
if [ ! -x .venv/bin/python ]; then
    note "creating virtual environment in .venv"
    "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate

# The check above validated the bootstrap interpreter, not the venv's. A venv
# created by an older run (or a different Python) can still poison imports, so
# revalidate the interpreter that is now active.
python - <<'PYEOF' || fail "existing .venv was built for a different interpreter.
Delete it and rerun: rm -rf .venv && PYTHON=${PYTHON:-python3} ./install.sh"
import platform, sys
ok = sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 13) \
     and platform.machine() in ("x86_64", "AMD64") and sys.platform.startswith("linux")
print(f"venv interpreter: {sys.executable} {sys.version.split()[0]} {platform.machine()}")
sys.exit(0 if ok else 1)
PYEOF

note "installing runtime dependencies"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

note "installing the Chromium build patchright drives"
python -m patchright install chromium

note "running self-check"
python bin/greysearch-doctor.py || {
    printf '\n'
    note "doctor reported problems above. Most are fixable:"
    note "  - missing browser: python -m patchright install chromium"
    note "  - search lanes blocked: rerun later, or use a local SearXNG with"
    note "    GREYSEARCH_LOCAL_SEARX=1 (points at http://127.0.0.1:8890)"
    exit 1
}

printf '\n\033[32minstalled.\033[0m Start the portal with:\n'
printf '  . .venv/bin/activate && python bin/greysearch-portal.py\n'

# A systemd user unit must run the portal through THIS install's venv
# interpreter at THIS install's path. Render the template accordingly
# instead of shipping a hardcoded ~/greysearch copy (best-effort: a host
# without systemd user support just skips this and uses the line above).
if [ -d "${HOME}/.config" ] && command -v systemctl >/dev/null 2>&1; then
    UNIT_DIR="${HOME}/.config/systemd/user"
    if mkdir -p "$UNIT_DIR" \
       && HERE_SAFED=$(printf '%s' "$HERE" | sed 's/[&|\\]/\\&/g') \
       && sed "s|%h/greysearch|$HERE_SAFED|g" portal/greysearch-portal.service \
            > "$UNIT_DIR/greysearch-portal.service"; then
        note "rendered systemd user unit: $UNIT_DIR/greysearch-portal.service"
        note "  start it with: systemctl --user daemon-reload \\
  && systemctl --user enable --now greysearch-portal"
    else
        note "could not render the systemd user unit (non-fatal) — start the"
        note "  portal with the command above instead"
    fi
fi
