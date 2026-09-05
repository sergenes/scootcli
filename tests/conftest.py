"""Shared test setup: isolate every test from the developer's machine and from other tests.

* The environment is snapshotted and restored around each test (``Config.load`` imports ``.env``
  values into ``os.environ`` and ``monkeypatch.delenv`` does not record absent variables).
* ``XDG_CONFIG_HOME``, ``SCOOT_CONFIG_DIR`` and ``SCOOT_STATE_DIR`` point at a fresh temporary
  directory, so a real ``~/.config/scoot/.env``, saved keys, preferences, or sessions never leak in.
"""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _isolated_environment():
    saved = dict(os.environ)
    tmp = tempfile.mkdtemp(prefix="scoot-test-")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "xdg")
    os.environ["SCOOT_CONFIG_DIR"] = os.path.join(tmp, "config")
    os.environ["SCOOT_STATE_DIR"] = os.path.join(tmp, "state")
    for key in ("OPENAI_API_KEY", "OLLAMA_API_KEY", "ANTHROPIC_API_KEY", "SCOOT_PROVIDER", "SCOOT_MODEL",
                "SCOOT_EFFORT", "HTTPS_PROXY", "NO_PROXY"):
        os.environ.pop(key, None)
    yield
    os.environ.clear()
    os.environ.update(saved)
