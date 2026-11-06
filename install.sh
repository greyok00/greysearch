#!/usr/bin/env bash
# Greysearch installer.
#
# Checks the interpreter version and platform, installs runtime
# dependencies, downloads the Chromium build patchright drives, and runs the
# self-check. Nothing here needs root, and nothing is written outside the
# directory you run it from except the pip and patchright caches.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

fail() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
note() { printf '\033[36m%s\033[0m\n' "$*"; }

PY="${PYTHON:-python3}"

# Greysearch is plain Python source that requires Python 3.13 or newer on
# Linux. An incompatible interpreter produces confusing ImportErrors later,
# so refuse early with a clear message.
"$PY" - <<'PYEOF' || fail "Greysearch requires Python 3.13 or newer on Linux"
import platform, sys
ok = sys.version_info >= (3, 13) \
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
ok = sys.implementation.name == "cpython" and sys.version_info >= (3, 13) \
     and sys.platform.startswith("linux")
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
    note "    GREYSEARCH_LOCAL_SEARX=1 (points at http://127.0.0.1:8888)"
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
        note "could not render the systemd user unit (non-fatal); start the"
        note "  portal with the command above instead"
    fi
fi

# ---- captcha models (optional, pinned) --------------------------------------
# The vendored classifier weights are FRESH DATA, not source: install.sh
# fetches the hcaptcha-challenger weight library pinned in models/SOURCE,
# one sha256 pin per file in models/CHECKSUMS. Every failure path is
# silent-honest - a box without weights still installs and runs; the
# captcha ladder's cpu/gpu rungs simply deactivate (the solver records the
# skip honestly). A file that has drifted from its pin (upstream refreshes
# the release's assets in place) is REMOVED, never kept half-verified.
# The weights are git-ignored; this block is what ships. Fetch runs a few
# downloads in parallel; the full library (grid + yolo detectors + clip
# pairs) is ~5 GB, so on a default install only the grid library is
# fetched - GREYSEARCH_CAPTCHA_VENDOR=all pulls the detector and clip
# pins too. The two non-release pins come from their upstream repos:
#   models/yolo/objects2023.yaml      raw.githubusercontent.com (GPL repo,
#                                     fetched like the weights, rung data
#                                     only)
#   models/clip/bpe_simple_vocab...gz github.com/openai/CLIP (MIT)
vendor_captcha_model() {
    tree_root=$(cd "$(dirname "$0")" && pwd)
    sums="$tree_root/models/CHECKSUMS"
    [ -f "$sums" ] || { note "no CHECKSUMS pin; captcha solver runs llm-rung only"; return 0; }
    vendor_all=${GREYSEARCH_CAPTCHA_VENDOR:-grid}
    case "$vendor_all" in all|ALL|1|on) vendor_all=all ;; *) vendor_all=grid ;; esac
    mkdir -p "$tree_root/models/hcaptcha"
    # Pass 1: verify what is on disk, collect (hash, pin-path, URL) triples
    # to fetch. Each pin prefix maps to its upstream source (see
    # models/SOURCE): the weight assets ride the pinned hcaptcha-challenger
    # release, the label table rides upstream's src tree, the BPE
    # vocabulary rides the OpenAI CLIP repo.
    : > "$sums.work"
    while IFS= read -r line; do
        case "$line" in ''|\#*) continue ;; esac
        set -- $line
        hash="$1"; rel="$2"; file="$tree_root/$rel"
        case "$rel" in
            models/clip/bpe_simple_vocab_16e6.txt.gz)
                url="https://github.com/openai/CLIP/raw/main/clip/$(basename "$rel")" ;;
            models/yolo/objects2023.yaml)
                url="https://raw.githubusercontent.com/QIN2DIM/hcaptcha-challenger/main/src/objects2023.yaml" ;;
            *)
                [ "$vendor_all" = all ] || continue
                url="https://github.com/QIN2DIM/hcaptcha-challenger/releases/download/model/$(basename "$rel")" ;;
        esac
        if [ -f "$file" ] && [ "$(sha256sum "$file" | cut -d' ' -f1)" = "$hash" ]; then
            continue
        fi
        [ -f "$file" ] && note "captcha model drift for $rel; refetching (upstream refreshed it)"
        printf '%s %s %s\n' "$hash" "$rel" "$url" >> "$sums.work"
    done < "$sums"
    # Verify-and-note what the grid default skipped: yolo/clip weight
    # files already on disk are NEVER deleted by a vendor run - they are
    # the owner's staged data. Their pin verification simply does not run
    # here (the rung reports it), and GREYSEARCH_CAPTCHA_VENDOR=all is
    # what fetches + verifies them.
    if [ "$vendor_all" != all ]; then
        skipping=0
        while IFS= read -r line; do
            case "$line" in ''|\#*) continue ;; esac
            set -- $line
            rel="$2"
            case "$rel" in
                models/hcaptcha/*|models/yolo/objects2023.yaml|models/clip/bpe_simple_vocab_16e6.txt.gz) continue ;;
            esac
            if [ -f "$tree_root/$rel" ]; then
                skipping=$((skipping + 1))
            fi
        done < "$sums"
        if [ "$skipping" -gt 0 ]; then
            note "yolo/clip weight pins present but unverified (vendor=grid; set GREYSEARCH_CAPTCHA_VENDOR=all to fetch+verify them)"
        fi
        ok_grid=1
        while IFS= read -r line; do
            case "$line" in ''|\#*) continue ;; esac
            set -- $line
            hash="$1"; rel="$2"
            case "$rel" in models/hcaptcha/*) ;; *) continue ;; esac
            file="$tree_root/$rel"
            if [ -f "$file" ] && [ "$(sha256sum "$file" | cut -d' ' -f1)" = "$hash" ]; then
                continue
            fi
            ok_grid=0
        done < "$sums"
        [ "$ok_grid" = 1 ] && note "captcha grid library verified"
        rm -f "$sums.work"
        return 0
    fi
    # Pass 2 (GREYSEARCH_CAPTCHA_VENDOR=all): parallel fetch, four at a
    # time, each from its pin's own source URL, so URL/asset drift cannot
    # write into a library under the wrong name or from the wrong origin.
    if [ -s "$sums.work" ]; then
        note "fetching captcha weights (pinned; $(wc -l < "$sums.work") file(s))"
        tr -s ' \t' ' ' < "$sums.work" \
            | awk -v root="$tree_root" '{print root "/" $2, $3}' \
            | xargs -P 4 -n 2 -r curl -fsSL --max-time 1800 --retry 2 -o \
            || true
    fi
    rm -f "$sums.work"
    # Pass 3: verify everything against the pin; a mismatch is removed, a
    # failed fetch is reported quiet-honest - the rung skips instead.
    ok=1
    while IFS= read -r line; do
        case "$line" in ''|\#*) continue ;; esac
        set -- $line
        hash="$1"; file="$tree_root/$2"
        if [ -f "$file" ] && [ "$(sha256sum "$file" | cut -d' ' -f1)" = "$hash" ]; then
            continue
        fi
        if [ -f "$file" ]; then
            note "captcha model checksum MISMATCH for $2; removing"
            rm -f "$file"
        else
            note "captcha model fetch missing for $2 (runs llm-rung only)"
        fi
        ok=0
    done < "$sums"
    [ "$ok" = 1 ] && note "captcha weight library verified"
    return 0
}
vendor_captcha_model
