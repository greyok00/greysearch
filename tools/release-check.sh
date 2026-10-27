#!/usr/bin/env bash
# Release hygiene gate. Run before every push:
#
#   bash tools/release-check.sh
#
# It fails if private paths, personal identifiers, secret tokens, or the
# retired project codename appear in the working tree, in ANY commit
# reachable from any ref, in the shipped binaries, or in packaged release
# artifacts. Patterns are assembled from fragments so this file does not
# match its own scan. A crashed scan stage (git, strings, tar, dpkg-deb,
# mktemp) is reported as a scanner error and forces the gate to FAIL: no
# clean ok line appears once a scan stage has crashed, and the final
# verdict can never be PASS. Reported matches are redacted so secret
# values are never echoed whole.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
scan_fail=0
bad() { printf 'FAIL  %s\n' "$*"; fail=1; }
# A crashed scanner must never pass silently: it marks the run, suppresses
# every later ok line, and its section guard skips the rest of the check.
scan_err() { bad "scanner error in $*"; scan_fail=1; }
ok()  { [ "$scan_fail" -eq 0 ] && printf 'ok    %s\n' "$*"; return 0; }
# Keep only 8 characters of any long token so a full secret never lands in
# the log; URLs with embedded credentials lose the password entirely.
redact() {
    LC_ALL=C sed -E \
        -e 's#(https?://[^/[:space:]]+:)[^/[:space:]]+@#\1[REDACTED]@#g' \
        -e 's/([A-Za-z0-9_-]{8})[A-Za-z0-9_-]{6,}/\1[REDACTED]/g'
}

if ! command -v git >/dev/null 2>&1 || ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    scan_err "git unavailable or not a repository; gate cannot run"
    printf '\nrelease check: FAIL\n'
    exit "$fail"
fi

P_HOME='/ho'"me/[A-Za-z0-9._-]+"
P_USERS='/Us'"ers/[A-Za-z0-9._-]+"
P_SRC='greysearch-'"src"
P_BUILD='greysearch-'"build/"
P_CODENAME='grey'"hound"
# Secrets: hosting tokens, app tokens, cloud keys, chat tokens, private
# keys, and URLs with embedded credentials.
P_SECRET='ghp_[A-Za-z0-9]{36}|gho_'"[A-Za-z0-9]{36}|gh[u,s]_[A-Za-z0-9]{36,}|AKIA[A-Z0-9]{16}|xox[bap]-"'[A-Za-z0-9-]{10,}|sk-'"[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"'|https?://[^/[:space:]:@]+:[^/[:space:]]+@'
P_PII="$P_HOME|$P_USERS|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"

# 1. Only the launchers, the llm package init, the kit build scripts, and
#    tools/ test fixtures may ship as .py. Everything else is compiled.
#    (Test fixtures are never implementation source — the pattern still
#    keeps any compiled-module .py from shipping.)
allowed='^(bin/greysearch-[a-z-]+\.py|llm/__init__\.py|assets/kit-source/[a-z_]+\.py|tools/test-[a-z-]+\.py)$'
unexpected=0
pyfiles=""
if ! pyfiles=$(git ls-files '*.py' 2>/dev/null); then
    scan_err "source inventory (git ls-files)"
else
    if [ -n "$pyfiles" ]; then
        while read -r f; do
            [[ $f =~ $allowed ]] || { bad "unexpected tracked source: $f"; unexpected=1; }
        done <<<"$pyfiles"
    fi
    [ "$unexpected" = 0 ] && ok "source inventory"
fi

# 2. Private paths and personal identifiers in tracked text.
rc=0
hits=""
hits=$(git grep -nIE "$P_PII" -- . ':(exclude)*.so' 2>/dev/null) || rc=$?
if [ "$rc" -gt 1 ]; then
    scan_err "tracked text scan (git grep)"
else
    [ -n "$hits" ] && hits=$(printf '%s\n' "$hits" | grep -vE '127\.0\.0\.1|0\.0\.0\.0') || hits=""
    if [ -n "$hits" ]; then
        bad "PII in tracked text"
        printf '%s\n' "$hits" | redact | head -20
    else
        ok "tracked text clean"
    fi
fi

# 2b. A packaging Maintainer field is the usual way a real person's name ends
#     up in a release doc; only the project identity is allowed.
rc=0
mhits=""
mhits=$(git grep -nE '^Maintainer:' -- . ':(exclude)*.so' 2>/dev/null) || rc=$?
if [ "$rc" -gt 1 ]; then
    scan_err "maintainer scan (git grep)"
else
    if [ -n "$mhits" ] && printf '%s\n' "$mhits" | grep -v 'Greysearch Maintainers' >/dev/null 2>&1; then
        bad "personal maintainer name"
        printf '%s\n' "$mhits" | grep -v 'Greysearch Maintainers' | head -10
    else
        ok "maintainer identity"
    fi
fi

# 3. Every commit and every blob reachable from any ref: private paths,
#    secrets, and the retired codename all fail the release.
leaky=0
if ! revs=$(git rev-list --objects --all 2>/dev/null | awk '{print $1}' | sort -u); then
    scan_err "git history scan (rev-list)"
else
    for b in $revs; do
        t=$(git cat-file -t "$b" 2>/dev/null) || { scan_err "git history scan (cat-file -t $b)"; continue; }
        [ "$t" = blob ] || continue
        # Pre-flight the blob so a corrupt object cannot masquerade as clean
        # below; scans are pipes so binary blobs never hit command
        # substitution (bash would strip NULs and warn).
        if ! git cat-file -p "$b" >/dev/null 2>&1; then
            scan_err "git history scan (cat-file -p $b)"; continue
        fi
        if git cat-file -p "$b" | grep -aE "$P_HOME|$P_USERS|$P_SRC|$P_BUILD" >/dev/null; then
            bad "private path in history blob $b"
            leaky=1
        fi
        if git cat-file -p "$b" | grep -aE "$P_SECRET" >/dev/null; then
            bad "secret pattern in history blob $b"
            git cat-file -p "$b" | grep -aE "$P_SECRET" | redact | head -5
            leaky=1
        fi
        if git cat-file -p "$b" | grep -aiE "$P_CODENAME" >/dev/null; then
            bad "$P_CODENAME string in history blob $b"
            git cat-file -p "$b" | grep -aiE "$P_CODENAME" | redact | head -5
            leaky=1
        fi
        if git cat-file -p "$b" | grep -aE '^Maintainer:' | grep -qv 'Greysearch Maintainers'; then
            bad "personal maintainer name in history blob $b"
            leaky=1
        fi
    done
    [ "$leaky" = 0 ] && ok "git history clean"
fi

# 4. Commit identities: only the project identity may appear. The local git
#    user is often a real person; committing under it puts a name and a
#    hostname into history that a history rewrite is then needed to remove.
rc=0
badid=""
badid=$(git log --all --format='%an <%ae>
%cn <%ce>' | sort -u | grep -vE '^release <release@local>$') || rc=$?
if [ "$rc" -gt 1 ]; then
    scan_err "commit identity scan (git log)"
elif [ -n "$badid" ]; then
    bad "unexpected commit identity"
    printf '%s\n' "$badid" | head -10
else
    ok "commit identities"
fi

# 5. Shipped binaries: private paths, secrets, and the retired codename.
shopt -s nullglob
so_files=( *.so llm/*.so )
shopt -u nullglob
if [ ${#so_files[@]} -eq 0 ]; then
    ok "binaries clean (none present)"
else
    if ! command -v strings >/dev/null 2>&1; then
        scan_err "strings not available for .so scan"
    elif ! strings -a "${so_files[@]}" >/dev/null 2>&1; then
        scan_err "strings over shipped .so files"
    else
        bin_bad=0
        binhits=""
        binhits=$(strings -a "${so_files[@]}" 2>/dev/null | grep -aE "$P_HOME|$P_USERS|$P_SRC|$P_BUILD|/tmp/tmp\.|$P_SECRET") || binhits=""
        if [ -n "$binhits" ]; then
            printf '%s\n' "$binhits" | redact | sort -u | head -20
            bad "private path or secret in shipped .so"
            bin_bad=1
        fi
        sohound=""
        sohound=$(strings -a "${so_files[@]}" 2>/dev/null | grep -aiE "$P_CODENAME") || sohound=""
        if [ -n "$sohound" ]; then
            printf '%s\n' "$sohound" | redact | sort -u | head -20
            bad "$P_CODENAME in shipped .so"
            bin_bad=1
        fi
        [ "$bin_bad" = 0 ] && ok "binaries clean"
    fi
fi

# 6. Secrets: hosting tokens, cloud keys, chat tokens, private keys, and
#    URLs with embedded credentials, in tracked text.
rc=0
hits=""
hits=$(git grep -nIE "$P_SECRET" -- . ':(exclude)*.so' 2>/dev/null) || rc=$?
if [ "$rc" -gt 1 ]; then
    scan_err "secret scan of tracked text"
elif [ -n "$hits" ]; then
    bad "secret pattern in tracked text"
    printf '%s\n' "$hits" | redact | head -20
else
    ok "no secrets in tracked text"
fi

# 6b. The retired codename must not appear in any tracked text file.
rc=0
hits=""
hits=$(git grep -nIiE "$P_CODENAME" -- . ':(exclude)*.so' 2>/dev/null) || rc=$?
if [ "$rc" -gt 1 ]; then
    scan_err "codename scan of tracked text"
elif [ -n "$hits" ]; then
    bad "$P_CODENAME in tracked text"
    printf '%s\n' "$hits" | redact | head -20
else
    ok "codename absent from tracked text"
fi

# 7. Packaged release artifacts in the tree (repo root or dist/): file names
#    and contents must carry no trace of the retired codename. Tarballs are
#    listed and extracted to a scratch dir; debs are streamed through
#    dpkg-deb and strings.
arch_bad=0
shopt -s nullglob
archives=( *.tar.gz dist/*.tar.gz *.deb dist/*.deb )
shopt -u nullglob
if [ ${#archives[@]} -eq 0 ]; then
    ok "packaged artifacts clean (none present)"
else
    for a in "${archives[@]}"; do
        case "$a" in
        *.tar.gz)
            if ! tar -tzf "$a" >/dev/null 2>&1; then
                scan_err "tar -tzf on $a"; continue
            fi
            names=""
            names=$(tar -tzf "$a" 2>/dev/null) || names=""
            if printf '%s\n' "$names" | grep -qiE "$P_CODENAME"; then
                printf '%s\n' "$names" | grep -iE "$P_CODENAME" | head -10
                bad "$P_CODENAME in $a file names"
                arch_bad=1
            fi
            xdir=""
            if ! xdir=$(mktemp -d); then
                scan_err "mktemp for $a extraction"; continue
            fi
            if ! tar -xzf "$a" -C "$xdir" 2>/dev/null; then
                rm -rf "$xdir"; scan_err "tar -xzf on $a"; continue
            fi
            grc=0
            grep -raqiE "$P_CODENAME" "$xdir" || grc=$?
            if [ "$grc" -gt 1 ]; then
                rm -rf "$xdir"; scan_err "content grep on $a"; continue
            fi
            if [ "$grc" -eq 0 ]; then
                grep -raiE "$P_CODENAME" "$xdir" | redact | sort -u | head -10
                bad "$P_CODENAME in $a contents"
                arch_bad=1
            fi
            rm -rf "$xdir"
            ;;
        *.deb)
            if ! command -v dpkg-deb >/dev/null 2>&1; then
                scan_err "dpkg-deb not available for $a"; continue
            fi
            if ! command -v strings >/dev/null 2>&1; then
                scan_err "strings not available for $a"; continue
            fi
            if ! dpkg-deb --fsys-tarfile "$a" >/dev/null 2>&1; then
                scan_err "dpkg-deb --fsys-tarfile on $a"; continue
            fi
            if dpkg-deb --fsys-tarfile "$a" 2>/dev/null | strings -a | grep -qiE "$P_CODENAME"; then
                dpkg-deb --fsys-tarfile "$a" 2>/dev/null | strings -a | grep -iE "$P_CODENAME" | redact | sort -u | head -10
                bad "$P_CODENAME in debian package $a"
                arch_bad=1
            fi
            ;;
        esac
    done
    [ "$arch_bad" = 0 ] && ok "packaged artifacts clean"
fi

[ "$fail" = 0 ] && printf '\nrelease check: PASS\n' || printf '\nrelease check: FAIL\n'
exit "$fail"