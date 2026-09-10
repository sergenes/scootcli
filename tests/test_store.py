"""The shared atomic JSON writer (0.11.0)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from scootcli.store import write_json_atomic


def test_write_json_atomic_writes_owner_only_and_leaves_no_temp():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "sub" / "f.json"
        write_json_atomic(path, {"a": 1})
        assert json.loads(path.read_text()) == {"a": 1}
        assert (path.stat().st_mode & 0o777) == 0o600
        assert [p.name for p in path.parent.iterdir()] == ["f.json"]  # no temp file left behind


def test_write_json_atomic_replaces_and_keeps_old_file_on_failure():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "f.json"
        write_json_atomic(path, {"v": 1})

        class _Unserializable:
            pass

        try:
            write_json_atomic(path, {"bad": _Unserializable()})
            assert False, "expected a serialization error"
        except TypeError:
            pass
        assert json.loads(path.read_text()) == {"v": 1}  # the old file survived
        assert [p.name for p in path.parent.iterdir()] == ["f.json"]  # temp cleaned up
