"""One path policy: which workspace entries the agent's own discovery may show or scan.

`search` (both backends), the workspace map in the prompt, the `/init` scan, and Tab completion all
ask here, so a name is hidden or visible everywhere at once. The policy covers what scoot finds by
itself. A file the model names in `read_file` is a separate, explicit request with its own approval.

Three groups are kept out:

- noise: VCS internals, caches, virtual environments, dependency trees, and scoot's own `.scoot/`;
- secrets: files and directories that usually hold credentials (`.env`, private keys, `.netrc`);
- symlinks: never followed and never read, so a link cannot lead a scan out of the tree it started in.

Hidden names (a leading dot) are otherwise visible, so `.github/` and `.gitlab-ci.yml` are found.
`hidden=False` (``SCOOT_HIDDEN_FILES=0``) restores the older rule: no dot-names at all.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Iterator, List, Tuple

# Directories never entered: nothing in them describes the project, and most are large.
NOISE_DIRS = frozenset({
    ".git", ".hg", ".svn", ".bzr",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache", ".tox", ".nox",
    ".hypothesis", ".ipynb_checkpoints", ".eggs", ".gradle", ".terraform", ".next", ".nuxt",
    ".parcel-cache", ".turbo", ".dart_tool", ".direnv", ".bundle",
    ".venv", "venv", "node_modules",
    ".idea", ".scoot",
})
_NOISE_DIR_GLOBS = ("*.egg-info",)

# Build output and editor settings: left out of the map and the /init scan, still searchable.
OUTPUT_DIRS = frozenset({"dist", "build", ".vscode"})

NOISE_FILES = frozenset({".DS_Store", "Thumbs.db", ".coverage"})

# Directories that hold credentials (reachable when a search starts outside a repository).
SECRET_DIRS = frozenset({".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker"})

SECRET_FILES = frozenset({
    ".env", ".netrc", "_netrc", ".pgpass", ".npmrc", ".pypirc", ".htpasswd", ".git-credentials",
    "credentials", "credentials.json", "secrets.json", "client_secret.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
})
_SECRET_GLOBS = (
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.kdbx",
    "service-account*.json", "client_secret*.json", "*credentials*.json",
    "*recovery-codes*", "*recovery_codes*",
)
# `.env.example` and friends document settings and hold no values.
_ENV_EXAMPLES = ("example", "sample", "template", "dist", "defaults")


def is_secret_file(name: str) -> bool:
    """True for a file name that usually holds credentials."""
    low = name.lower()
    if low in SECRET_FILES:
        return True
    if low.startswith(".env."):
        return low.split(".")[-1] not in _ENV_EXAMPLES
    return any(fnmatch.fnmatchcase(low, g) for g in _SECRET_GLOBS)


def skip_dir(name: str, hidden: bool = True, outputs: bool = False) -> bool:
    """True when a directory with this name is not entered. ``outputs`` also drops build output."""
    if name in NOISE_DIRS or name in SECRET_DIRS:
        return True
    if any(fnmatch.fnmatchcase(name, g) for g in _NOISE_DIR_GLOBS):
        return True
    if outputs and name in OUTPUT_DIRS:
        return True
    return not hidden and name.startswith(".")


def skip_file(name: str, hidden: bool = True) -> bool:
    """True when a file with this name is neither shown nor read."""
    if name in NOISE_FILES or is_secret_file(name):
        return True
    return not hidden and name.startswith(".")


def allows(rel, hidden: bool = True) -> bool:
    """True when the file at relative path ``rel`` (a string or parts) passes the policy.

    Every directory on the way is checked as well as the file name, so this is the filter for paths
    another program (ripgrep) reports.
    """
    parts = [p for p in (rel.replace("\\", "/").split("/") if isinstance(rel, str) else list(rel))
             if p not in ("", ".")]
    if not parts:
        return False
    if any(skip_dir(p, hidden) for p in parts[:-1]):
        return False
    return not skip_file(parts[-1], hidden)


def ripgrep_globs() -> List[str]:
    """Exclusion globs that keep ripgrep out of the directories it would otherwise scan with
    ``--hidden``. They save work only: results are still filtered with :func:`allows`."""
    names = sorted(NOISE_DIRS | SECRET_DIRS) + list(_NOISE_DIR_GLOBS)
    return [f"!{n}" for n in names]


def walk(root, hidden: bool = True, outputs: bool = False) -> Iterator[Tuple[str, List[str], List[str]]]:
    """``os.walk`` under ``root`` with the policy applied: ``(dirpath, dirnames, filenames)``, sorted.

    Symlinks are dropped from both lists (``os.walk`` would not descend into a linked directory, but
    it would still be listed), and only regular files are kept, so a FIFO or device is never opened.
    """
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = sorted(
            d for d in dirnames
            if not skip_dir(d, hidden, outputs) and not os.path.islink(os.path.join(dirpath, d))
        )
        files = []
        for name in sorted(filenames):
            if skip_file(name, hidden):
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            files.append(name)
        yield dirpath, list(dirnames), files


def iter_files(root, hidden: bool = True) -> Iterator[Path]:
    """Every regular file under ``root`` that passes the policy."""
    for dirpath, _dirs, files in walk(root, hidden):
        for name in files:
            yield Path(dirpath) / name


def hidden_enabled(config) -> bool:
    """The ``hidden_files`` setting of a config object; on when the object does not carry it."""
    return bool(getattr(config, "hidden_files", True))

