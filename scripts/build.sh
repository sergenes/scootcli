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
# Stage only the package (no __pycache__, .DS_Store, egg-info from a developer checkout) and write
# our own entry point: zipapp's generated one calls main() without passing its return value to
# sys.exit, so a handled error would exit 0.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
python3 - "$STAGE" <<'PY'
import pathlib, shutil, sys

stage = pathlib.Path(sys.argv[1])
shutil.copytree("src/scootcli", stage / "scootcli",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store", "*.egg-info"))
(stage / "__main__.py").write_text("import sys\nfrom scootcli.cli import main\n\nsys.exit(main())\n")
PY
python3 -m zipapp "$STAGE" \
    --python "/usr/bin/env python3" \
    --compress \
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
