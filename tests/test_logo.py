"""Mascot art, state eyes, labels, and their wiring into the status bar / config."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from scootcli import logo
from scootcli.config import Config
from scootcli.panel import build_status_text


def _cols(row: str, glyph: str):
    return [i for i, ch in enumerate(row) if ch == glyph]


def test_every_variant_is_rectangular_and_same_pose():
    for variant in logo.VARIANTS:
        rows = logo.mascot("idle", variant)
        assert len({len(r) for r in rows}) == 1, variant  # padded to a common width
        assert rows[-1].rstrip().startswith("(o)") or rows[-1].strip().startswith("(o)"), variant
        assert rows[-1].rstrip().endswith("(o)"), variant  # two wheels on the deck


def test_legs_land_on_the_deck_joints():
    for variant in ("banner", "lean"):
        rows = logo.mascot("idle", variant)
        legs, deck = rows[3], rows[4]
        assert _cols(legs, "┬") == _cols(deck, "╧"), variant


def test_ascii_and_banner_share_the_same_footprint():
    assert len(logo.mascot("idle", "ascii")[0]) == len(logo.mascot("idle", "banner")[0])


def test_eyes_follow_state():
    assert "│o o│" in logo.mascot("idle")[1]
    assert "│> >│" in logo.mascot("thinking")[1]
    assert "│- -│" in logo.mascot("stopped")[1]
    assert "╭o_o╮" in logo.mascot("idle", "micro")[0]
    assert "|>_>|" in logo.mascot("thinking", "ascii")[1]
    assert logo.mascot("garbage")[1] == logo.mascot("idle")[1]  # unknown state → idle


def test_face_and_label():
    assert logo.face() == "╭o o╮"
    assert logo.face("thinking") == "╭> >╮"
    assert logo.label() == "🛴 scoot"
    assert logo.label(emoji=False) == "⏺ scoot"


def test_compose_pads_the_shorter_side():
    art = ["ab", "cd", "ef"]
    out = logo.compose(art, ["one"], gap=1)
    assert out == ["ab one", "cd", "ef"]
    out = logo.compose(["ab"], ["one", "two"], gap=1)
    assert out == ["ab one", "   two"]


class _Session:
    def __init__(self, logo_on=True, state="idle"):
        self.config = Config().override(root="/proj/x", logo=logo_on)
        self.id = "20260905-120000-ab12"
        self.model = "gpt-5"
        self.active_model = "gpt-5"
        self.approval_mode = "always"
        self.total_prompt = self.total_completion = 0
        self.mascot_state = state

    def resolved_model(self):
        return self.model


def test_status_bar_face_tracks_state_and_honours_logo_setting():
    assert build_status_text(_Session()).startswith("╭o o╮")
    assert build_status_text(_Session(state="thinking")).startswith("╭> >╮")
    assert build_status_text(_Session(state="stopped")).startswith("╭- -╮")
    assert "╭" not in build_status_text(_Session(logo_on=False))


def test_config_logo_precedence_env_then_preference_then_default():
    with tempfile.TemporaryDirectory() as d:
        saved = {k: os.environ.get(k) for k in ("SCOOT_CONFIG_DIR", "SCOOT_LOGO", "SCOOT_EMOJI")}
        os.environ["SCOOT_CONFIG_DIR"] = d
        os.environ.pop("SCOOT_LOGO", None)
        os.environ.pop("SCOOT_EMOJI", None)
        try:
            from scootcli import preferences

            assert Config.load(cwd=Path(d)).logo is True
            assert Config.load(cwd=Path(d)).emoji is True
            preferences.set_logo(False)
            assert Config.load(cwd=Path(d)).logo is False
            os.environ["SCOOT_LOGO"] = "1"
            assert Config.load(cwd=Path(d)).logo is True  # env beats the saved preference
            os.environ["SCOOT_EMOJI"] = "0"
            assert Config.load(cwd=Path(d)).emoji is False
            preferences.set_logo(None)
            assert preferences.get_logo() is None
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def test_logo_command_persists_and_swaps_live_config():
    with tempfile.TemporaryDirectory() as d:
        old = os.environ.get("SCOOT_CONFIG_DIR")
        os.environ["SCOOT_CONFIG_DIR"] = d
        try:
            from scootcli import commands, preferences

            commands.load_builtins()
            cmd = commands.get("logo")
            assert cmd is not None
            session = _Session()
            cmd.handler(session, "off")
            assert session.config.logo is False
            assert preferences.get_logo() is False
            cmd.handler(session, "on")
            assert session.config.logo is True
        finally:
            if old is None:
                os.environ.pop("SCOOT_CONFIG_DIR", None)
            else:
                os.environ["SCOOT_CONFIG_DIR"] = old


def test_fit_and_tilde():
    import os

    assert logo.fit("short", 20) == "short"
    out = logo.fit("abcdefghijklmnopqrstuvwxyz", 11)
    assert len(out) == 11 and out.startswith("abcde") and out.endswith("vwxyz") and "…" in out
    assert logo.fit("abcdef", 3) == "abc"
    home = os.path.expanduser("~")
    assert logo.tilde(home + "/code/x") == "~/code/x"
    assert logo.tilde("/opt/x") == "/opt/x"
