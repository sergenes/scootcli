"""One atomic JSON writer, shared by credentials, preferences, and sessions.

A single place that writes a JSON document safely: a uniquely named temporary file in the same
directory, fsynced, then ``os.replace``d over the target so a crash or a second process never leaves
a truncated or half-written file, and never collides on a predictable temp name. The file is created
with a restrictive mode from the start, so a secret is never briefly world-readable.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(path, obj: Any, mode: int = 0o600, dir_mode: int = 0o700) -> Path:
    """Write ``obj`` as JSON to ``path`` atomically, owner-only by default.

    The parent directory is created (and its mode tightened, best-effort). The write goes to a unique
    temp file in that directory, is flushed and fsynced, then atomically renamed over ``path``. On any
    failure the temp file is removed and the original ``path`` is left untouched.
    """
    path = Path(path)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, dir_mode)
    except OSError:
        pass  # best-effort on exotic filesystems
    fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as fh:
            json.dump(obj, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return path
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
