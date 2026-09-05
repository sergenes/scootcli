#!/usr/bin/env bash
# Build scoot distribution artifacts.
#
#   dist/scoot.pyz                          single-file zipapp: runs with any Python 3, no install
#   dist/scootcli-<ver>-py3-none-any.whl    wheel for `pipx install` / PyPI
#   dist/scootcli-<ver>.tar.gz              sdist for PyPI
#
# The zipapp needs nothing beyond the stdlib. The wheel + sdist need the `build` package
# (`pip install build`); they are skipped with a note when it is missing.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p dist

echo "→ building single-file zipapp: dist/scoot.pyz"
python3 -m zipapp src \
    --main "scootcli.cli:main" \
    --python "/usr/bin/env python3" \
    --output dist/scoot.pyz
chmod +x dist/scoot.pyz
echo "  done ($(du -h dist/scoot.pyz | cut -f1))"

if python3 -c "import build" >/dev/null 2>&1; then
    echo "→ building wheel + sdist: dist/"
    python3 -m build --outdir dist >/dev/null
    ls dist/scootcli-*
else
    echo "→ skipping wheel/sdist (pip install build); the .pyz is ready"
fi

echo "✔ build complete. Artifacts in dist/"
echo "  publish: python3 -m twine check dist/scootcli-* && python3 -m twine upload dist/scootcli-*"
