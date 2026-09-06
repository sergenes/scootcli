"""0.6.0 small items: cost per model, the opt-in update check, the Ctrl-N note, install.sh --uninstall."""

from __future__ import annotations

import io
import json
import subprocess
import threading
from pathlib import Path

from scootcli import pricing, updates
from scootcli.config import Config


# ── pricing ────────────────────────────────────────────────────────────────────
def test_price_lookup_longest_prefix_and_free_local():
    assert pricing.price_for("openai/gpt-5.3-codex") == (1.75, 0.175, 14.00)
    assert pricing.price_for("openai/gpt-5-mini") == (0.25, 0.025, 2.00)   # not the gpt-5 row
    assert pricing.price_for("gpt-5") == (1.25, 0.125, 10.00)
    assert pricing.price_for("anthropic/claude-opus-5") == (5.00, 0.50, 25.00)
    assert pricing.price_for("anthropic/claude-haiku-4-5-20251001") == (1.00, 0.10, 5.00)
    assert pricing.price_for("ollama/llama3.2:latest") == (0.0, 0.0, 0.0)
    assert pricing.price_for("openai/some-future-model") is None


def test_cost_math_with_cached_input():
    # 1M fresh input + 1M output on codex = 1.75 + 14
    assert abs(pricing.cost("openai/gpt-5.3-codex", 1_000_000, 1_000_000) - 15.75) < 1e-9
    # cached input is charged at the cached rate
    assert abs(pricing.cost("openai/gpt-5.3-codex", 1_000_000, 0, cached=1_000_000) - 0.175) < 1e-9
    assert pricing.cost("ollama/llama3.2", 5000, 500) == 0.0
    assert pricing.cost("openai/unknown", 1, 1) is None
    assert pricing.cached_tokens({"cache_read_input_tokens": 12}) == 12
    assert pricing.cached_tokens({"input_tokens_details": {"cached_tokens": 7}}) == 7
    assert pricing.cached_tokens({}) == 0
    assert pricing.fmt(None) == "?" and pricing.fmt(0) == "$0" and pricing.fmt(0.0042) == "$0.0042" and pricing.fmt(1.5) == "$1.50"


def test_session_tracks_cached_tokens_and_cost(tmp_path):
    from scootcli.repl import ReplSession

    s = ReplSession(Config().override(root=str(tmp_path)), provider=None)
    s.account({"prompt_tokens": 1000, "completion_tokens": 100, "cache_read_input_tokens": 400}, model="anthropic/claude-opus-5")
    s.account({"prompt_tokens": 2000, "completion_tokens": 10}, model="ollama/llama3.2")
    u = s.usage_by_model["anthropic/claude-opus-5"]
    assert u == {"prompt": 1000, "completion": 100, "cached": 400, "calls": 1}
    expected = (600 * 5.00 + 400 * 0.50 + 100 * 25.00) / 1_000_000
    assert abs(s.session_cost() - expected) < 1e-12
    s.account({"prompt_tokens": 1, "completion_tokens": 1}, model="openai/mystery")
    assert s.session_cost() is None  # one unknown price makes the total unknown, never a guess


# ── update check ───────────────────────────────────────────────────────────────
def test_is_newer_and_latest_version_with_fake_opener():
    assert updates.is_newer("0.6.0", "0.5.0") and not updates.is_newer("0.5.0", "0.5.0") and not updates.is_newer(None, "0.5.0")
    assert updates.is_newer("1.0.0", "0.9.9") and not updates.is_newer("0.5.0", "0.10.0")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    ok = lambda req, timeout: _Resp(json.dumps({"info": {"version": "9.9.9"}}).encode())
    assert updates.latest_version(opener=ok) == "9.9.9"
    boom = lambda req, timeout: (_ for _ in ()).throw(OSError("offline"))
    assert updates.latest_version(opener=boom) is None
    assert not updates.enabled()


def test_check_update_flag(monkeypatch, capsys):
    from scootcli import cli

    monkeypatch.setattr(updates, "latest_version", lambda timeout=2.0, opener=None: "9.9.9")
    assert cli.main(["--check-update"]) == 0
    out = capsys.readouterr().out
    assert "newer release available: 9.9.9" in out and "pipx upgrade scootcli" in out
    monkeypatch.setattr(updates, "latest_version", lambda timeout=2.0, opener=None: None)
    assert cli.main(["--check-update"]) == 1
    assert "could not reach PyPI" in capsys.readouterr().out


def test_status_bar_shows_update_segment(tmp_path):
    from scootcli.panel import build_status_text
    from scootcli.repl import ReplSession

    s = ReplSession(Config().override(root=str(tmp_path)), provider=None)
    s.id = "20260906-000000-ab12"
    assert "⬆" not in build_status_text(s, user="x")
    s.update_available = "9.9.9"
    assert "⬆ 9.9.9" in build_status_text(s, user="x")


# ── Ctrl-N note ────────────────────────────────────────────────────────────────
def test_listener_sets_the_note_event_on_ctrl_n(monkeypatch):
    from scootcli import keys

    section = keys.InterruptibleSection(threading.Event(), note_event=threading.Event())
    section.enabled = True
    section._fd = 0
    fed = [keys.NOTE_KEY, b"x", b"\x1b"]
    monkeypatch.setattr(keys.select, "select", lambda r, w, x, t=None: ([1], [], []) if fed else ([], [], []))
    monkeypatch.setattr(keys.os, "read", lambda fd, n: fed.pop(0) if fed else b"")
    section._listen()
    assert section.note_event.is_set() and section.cancel_event.is_set()


def test_take_note_queues_for_the_next_model_call(monkeypatch, capsys):
    from scootcli.repl import ReplUI

    ui = ReplUI()
    session = type("S", (), {})()
    ui.take_note(session)  # nothing requested → nothing asked
    ui.note_requested.set()
    monkeypatch.setattr("builtins.input", lambda prompt="": "prefer short answers")
    ui.take_note(session)
    assert session.pending_notes == ["prefer short answers"] and not ui.note_requested.is_set()
    assert "noted" in capsys.readouterr().out


def test_agent_takes_the_note_before_the_model_call(tmp_path):
    from scootcli.agent import Agent
    from scootcli.providers import ChatResult

    seen = []

    class _P:
        def chat(self, messages, **k):
            seen.append(list(messages))
            return ChatResult(content="ok", model="m")

    class _UI:
        def __init__(self):
            self.events = []

        def take_note(self, session):
            session.pending_notes = ["note from the user"]

        def assistant(self, t):
            pass

        def tool_result(self, *a):
            pass

        from contextlib import contextmanager

        @contextmanager
        def activity(self, message, cancel_event):
            yield

    class _S:
        def __init__(self):
            self.messages = [{"role": "user", "content": "hi"}]
            self.model = self.active_model = "m"
            self.bad_models = set()
            self.config = Config().override(root=str(tmp_path), stream=False, workspace_context=False)

        def resolved_model(self):
            return "m"

        def available_models(self):
            return ["m"]

        def account(self, usage, model=""):
            pass

    s = _S()
    Agent(s.config, _P()).run_turn(s, _UI())
    assert any(m["role"] == "user" and "note from the user" in m["content"] for m in seen[0])


# ── install.sh --uninstall ─────────────────────────────────────────────────────
def test_install_script_uninstall(tmp_path):
    script = Path(__file__).resolve().parents[1] / "install.sh"
    (tmp_path / "scoot").write_text("#!/bin/sh\n")
    out = subprocess.run(["bash", str(script), "--uninstall"], capture_output=True, text=True,
                         env={"SCOOT_INSTALL_DIR": str(tmp_path), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert out.returncode == 0 and "removed" in out.stdout and not (tmp_path / "scoot").exists()
    out = subprocess.run(["bash", str(script), "--uninstall"], capture_output=True, text=True,
                         env={"SCOOT_INSTALL_DIR": str(tmp_path), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert "nothing to remove" in out.stdout
