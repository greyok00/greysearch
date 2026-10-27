#!/usr/bin/env bash
# Self-test for tools/release-check.sh. Builds throwaway git repos under
# mktemp, seeds known-bad content, and asserts the gate fails loudly on
# each seed while a clean control repo passes (which also proves the gate
# does not match its own assembled patterns). Seeded secret values are
# assembled from fragments and are only ever printed truncated to their
# first 8 characters.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
GATE="$HERE/release-check.sh"

scratch=$(mktemp -d "${TMPDIR:-/tmp}/release-check-test.XXXXXX") || { echo "cannot mktemp" >&2; exit 2; }
trap 'rm -rf "$scratch"' EXIT
fails=0
pass() { printf 'PASS  %s\n' "$*"; }
miss() { printf 'FAIL  %s\n' "$*"; fails=$((fails + 1)); }

# Seed values, assembled from fragments so this file never matches the
# gate's own scans if it is ever committed to the gate's repo.
HOMEPATH='/home/'"grey/private-foo"
CODENAME='grey'"hound"
BLOBSEED="${CODENAME}"'_mcp.cpython-313-x86_64-linux-gnu.so'
SEEDTOKEN='sk-realseed'"1234567890abc"

# --- seeded repo: three known-bad artifacts -------------------------------
seed="$scratch/seed"
mkdir -p "$seed/tools"
cp "$GATE" "$seed/tools/release-check.sh"
git -c init.defaultBranch=master init -q "$seed"
git -C "$seed" config user.name release
git -C "$seed" config user.email release@local
printf 'private home path: %s\n' "$HOMEPATH" > "$seed/seed-path.txt"
printf 'api token: %s\n' "$SEEDTOKEN" > "$seed/seed-token.txt"
git -C "$seed" add -A && git -C "$seed" commit -qm 'seed baseline'
printf 'legacy driver: %s\n' "$BLOBSEED" > "$seed/olddriver.txt"
git -C "$seed" add olddriver.txt && git -C "$seed" commit -qm 'add legacy driver'
git -C "$seed" rm -q olddriver.txt && git -C "$seed" commit -qm 'drop legacy driver'

seed_out=$(bash "$seed/tools/release-check.sh" 2>&1)
seed_rc=$?
printf '%s\n' "$seed_out"

[ "$seed_rc" -ne 0 ] \
    && pass "seeded repo: gate exits nonzero (exit $seed_rc)" \
    || miss "seeded repo: gate exited 0, expected FAIL"
grep -q 'private-foo' <<<"$seed_out" \
    && pass "seed 1: private home path reported" \
    || miss "seed 1: private home path not reported"
grep -qE "${CODENAME}.*history blob" <<<"$seed_out" \
    && pass "seed 2: deleted-driver codename blob reported in history" \
    || miss "seed 2: codename history blob not reported"
grep -q 'sk-reals' <<<"$seed_out" \
    && pass "seed 3: sk- token reported (redacted to 8 chars)" \
    || miss "seed 3: sk- token not reported"
grep -q "${SEEDTOKEN}" <<<"$seed_out" \
    && miss "gate echoed the full seed token" \
    || pass "gate never echoes the full seeded token"

# --- clean control repo (also proves gate self-match) ---------------------
clean="$scratch/clean"
mkdir -p "$clean/tools"
cp "$GATE" "$clean/tools/release-check.sh"
cp "$HERE/test-release-check.sh" "$clean/tools/" 2>/dev/null
printf '# greysearch\n\nClean control tree for the release-check self-test.\n' > "$clean/README.md"
printf 'placeholder binary with no embedded strings\n' > "$clean/dummy.so"
git -c init.defaultBranch=master init -q "$clean"
git -C "$clean" config user.name release
git -C "$clean" config user.email release@local
git -C "$clean" add -A && git -C "$clean" commit -qm 'clean control'

clean_out=$(bash "$clean/tools/release-check.sh" 2>&1)
clean_rc=$?
printf '%s\n' "$clean_out"

[ "$clean_rc" -eq 0 ] && grep -q 'release check: PASS' <<<"$clean_out" \
    && pass "clean control: gate passes, no self-pattern match" \
    || miss "clean control: gate failed (exit $clean_rc), expected PASS"

# --- scanner-error robustness: git missing from PATH ----------------------
nogit="$scratch/pathless-git"
mkdir -p "$nogit"
for d in /usr/local/bin /usr/bin /bin; do
    [ -d "$d" ] || continue
    for f in "$d"/*; do
        b=$(basename "$f")
        [ -e "$nogit/$b" ] || ln -s "$f" "$nogit/$b" 2>/dev/null
    done
done
rm -f "$nogit"/git* 2>/dev/null

err_out=$(PATH="$nogit" bash "$clean/tools/release-check.sh" 2>&1)
err_rc=$?
printf '%s\n' "$err_out"

if [ "$err_rc" -ne 0 ] && grep -q 'scanner error' <<<"$err_out" \
   && ! grep -q 'release check: PASS' <<<"$err_out"; then
    pass "scanner error: gate fails loudly without git, never prints clean"
else
    miss "scanner error: gate did not fail loudly without git (exit $err_rc)"
fi

printf '\n'
if [ "$fails" -eq 0 ]; then
    printf 'test-release-check: PASS\n'
else
    printf 'test-release-check: FAIL (%d missed assertion(s))\n' "$fails"
fi
exit "$fails"