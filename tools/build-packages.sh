#!/usr/bin/env bash
# Build the release tarball and deb from the clean checkout. Not for dev use.
#
#   bash tools/build-packages.sh
#
# Produces dist/greysearch-v${VERSION}.tar.gz (the exact tracked tree) and
# dist/greysearch_${VERSION}_amd64.deb (compiled modules + license text +
# vendored runtime dependencies + the /usr/bin/greysearch launcher). Both
# artifacts are reproducible from any clean checkout of the tagged commit.
set -euo pipefail

VERSION=1.0.0
REL="greysearch-v${VERSION}"
PY="${PYTHON:-python3}"

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.."

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p dist "$STAGE/$REL" "$STAGE/debroot/DEBIAN" "$STAGE/debroot/usr/bin"

# --- tarball: the exact tracked tree -----------------------------------
git archive --format=tar.gz --prefix="$REL/" -o "dist/$REL.tar.gz" HEAD

# --- deb payload: the full tracked tree at /usr/lib/greysearch ---------
# Runtime data lands next to the compiled modules the way they expect to
# find it: mcp reads research/data/ at import, the portal serves
# portal/index.html, doctor runs its kit check against assets/, and llm/
# carries its start script. The tracked tree holds no top-level
# implementation .py — only bin launchers, llm/__init__.py, and the kit
# build scripts under assets/kit-source.
git archive HEAD | tar -x -C "$STAGE/$REL"
DEBLIB="$STAGE/debroot/usr/lib/greysearch"
mkdir -p "$DEBLIB"
cp -a "$STAGE/$REL/." "$DEBLIB/"

# --- vendored runtime dependencies from a fresh venv -------------------
VENV="$STAGE/venv"
"$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r requirements.txt

SP="$DEBLIB/lib/python3.13/site-packages"
mkdir -p "$SP"
cp -a "$VENV/lib/python3.13/site-packages/." "$SP/"
# Interpreter tooling is not part of the runtime: drop pip and any seeding
# leftovers. The application's own modules all live as compiled extensions.
rm -rf "$SP/pip" "$SP"/pip-*.dist-info "$SP/setuptools" \
    "$SP"/setuptools-*.dist-info "$SP/pkg_resources" "$SP/_distutils_hack" \
    "$SP"/distutils-precedence*.pth
find "$SP" -name '__pycache__' -type d -prune -exec rm -rf {} +

# Console entry points ship with a fixed system-interpreter shebang so they
# work without the throwaway venv, mirroring the reference package layout.
if [ -d "$SP/../.." ] && [ -d "$VENV/bin" ]; then
    mkdir -p "$SP/bin"
    for s in "$VENV/bin"/*; do
        case "$(basename "$s")" in
            python*|pip*|activate*|Activate*) continue ;;
        esac
        [ -f "$s" ] || continue
        [ "$(head -c 2 "$s")" = '#!' ] || continue
        sed '1s|.*|#!/usr/bin/python3|' "$s" > "$SP/bin/$(basename "$s")"
        chmod 0755 "$SP/bin/$(basename "$s")"
    done
fi

# Wheel build headers are kept for parity with the reference layout.
if [ -d "$VENV/include/site/python3.13" ]; then
    mkdir -p "$SP/include/python"
    cp -a "$VENV/include/site/python3.13/." "$SP/include/python/"
fi

# --- control file --------------------------------------------------------
cat > "$STAGE/debroot/DEBIAN/control" <<CONTROL
Package: greysearch
Version: $VERSION
Section: utils
Priority: optional
Architecture: amd64
Depends: python3 (>= 3.13)
Maintainer: Greysearch Maintainers <release@local>
Description: GreySearch - a local-first web research tool
 GreySearch performs autonomous research passes entirely on your local
 machine using headless Chromium and direct search APIs. It produces
 a citable Master Guide, static HTML site, or PDF.
 .
 This package includes the compiled extension modules and Python
 dependencies, requiring only a system Python 3.13 installation.
CONTROL

# --- launcher ------------------------------------------------------------
cat > "$STAGE/debroot/usr/bin/greysearch" <<'LAUNCHER'
#!/bin/sh
# Greysearch command launcher. Loads the compiled extension modules shipped
# under /usr/lib/greysearch plus the bundled site-packages; the package
# contains no top-level .py implementations.
LIBDIR="/usr/lib/greysearch"
export PYTHONPATH="${LIBDIR}:${LIBDIR}/lib/python3.13/site-packages${PYTHONPATH:+:$PYTHONPATH}"

CMD="${1:-}"
if [ -z "$CMD" ]; then
    echo "Usage: $0 {doctor|research|portal|build-site|mcp} [args...]" >&2
    exit 2
fi
shift

case "$CMD" in
    doctor)
        ENTRY="import doctor; sys.exit(doctor.main())"
        ;;
    research)
        ENTRY="import research; sys.exit(research.main(sys.argv[1:]))"
        ;;
    build-site)
        ENTRY="import greysearch_site_builder; sys.exit(greysearch_site_builder.render())"
        ;;
    mcp)
        ENTRY="import greysearch_mcp; greysearch_mcp.main()"
        ;;
    portal)
        ENTRY="import portal_server; portal_server.app.run(host=portal_server.HOST, port=portal_server.PORT, threaded=True)"
        ;;
    *)
        echo "Unknown command: $CMD" >&2
        echo "Usage: $0 {doctor|research|portal|build-site|mcp} [args...]" >&2
        exit 2
        ;;
esac

# python3 -c puts the user args in sys.argv[1:]; point sys.argv[0] at the
# command so argparse usage lines identify the invocation correctly. The -c
# form (rather than a heredoc) keeps stdin free for the MCP stdio server.
# For -c, sys.path[0] is the CURRENT DIRECTORY, so a doctor.py (etc.) planted
# in the caller's cwd would shadow the shipped compiled modules. Drop every
# sys.path entry that resolves to the cwd: sys.path[0] itself is '', and a
# caller's PYTHONPATH with a leading/trailing/doubled ':' injects further cwd
# entries — CPython 3.13 turns those into the cwd's ABSOLUTE path at startup,
# so ''-only filtering is not enough. Then point sys.path[0] at LIBDIR so the
# shipped modules come before anything a caller-supplied PYTHONPATH adds.
exec python3 -c "
import os, sys
sys.path[:] = [p for p in sys.path if p and os.path.abspath(p) != os.getcwd()]
sys.path[0] = '/usr/lib/greysearch'
sys.argv[0] = 'greysearch ${CMD}'
${ENTRY}
" "$@"
LAUNCHER
chmod 0755 "$STAGE/debroot/usr/bin/greysearch"

# --- build the package ---------------------------------------------------
dpkg-deb --build --root-owner-group "$STAGE/debroot" "dist/greysearch_${VERSION}_amd64.deb"

echo "built:"
echo "  dist/$REL.tar.gz"
echo "  dist/greysearch_${VERSION}_amd64.deb"