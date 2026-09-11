"""Shared test setup: isolate every test from the developer's machine and from other tests.

* The environment is snapshotted and restored around each test (``Config.load`` imports ``.env``
  values into ``os.environ`` and ``monkeypatch.delenv`` does not record absent variables).
* ``XDG_CONFIG_HOME``, ``SCOOT_CONFIG_DIR`` and ``SCOOT_STATE_DIR`` point at a fresh temporary
  directory, so a real ``~/.config/scoot/.env``, saved keys, preferences, or sessions never leak in.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest

# Every provider key variable and every proxy variable (both cases), so a developer's real
# environment cannot influence a test through any of them.
_PROVIDER_KEYS = ("OPENAI_API_KEY", "OLLAMA_API_KEY", "ANTHROPIC_API_KEY")
_PROXY_KEYS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
               "NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy")


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch):
    saved = dict(os.environ)
    tmp = tempfile.mkdtemp(prefix="scoot-test-")
    try:
        # Run every test from an empty directory: Config.load walks up from the cwd looking for a
        # project .env, and the repo's own .env (a developer's real keys) must never reach a test.
        work = os.path.join(tmp, "cwd")
        os.makedirs(work)
        monkeypatch.chdir(work)
        # Clear every scoot/provider/proxy variable up front, so a stray SCOOT_APPROVAL, SCOOT_ROUTER,
        # lowercase proxy, or the like from the developer's shell never leaks into a test.
        for key in list(os.environ):
            if key.startswith("SCOOT_") or key in _PROVIDER_KEYS or key in _PROXY_KEYS:
                os.environ.pop(key, None)
        # Then point config/state at the throwaway dir (set after the clear so they survive it).
        os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "xdg")
        os.environ["SCOOT_CONFIG_DIR"] = os.path.join(tmp, "config")
        os.environ["SCOOT_STATE_DIR"] = os.path.join(tmp, "state")
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)
        shutil.rmtree(tmp, ignore_errors=True)  # no more leaked temp dirs
