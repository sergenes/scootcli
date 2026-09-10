"""Rendering helpers: the full-width reverse-video band for user prompts (0.10.0)."""

from __future__ import annotations

import re

from scootcli import rendering

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _visible(line: str) -> str:
    return _ANSI.sub("", line)


def test_user_band_is_full_width_reverse_video(monkeypatch):
    monkeypatch.setattr(rendering, "_COLOR_ENABLED", True)
    band = rendering.user_band("fix the parser", 30)
    lines = band.split("\n")
    assert len(lines) == 1
    assert lines[0].startswith("\x1b[7m") and lines[0].endswith("\x1b[0m")
    visible = _visible(lines[0])
    assert visible == "❯ fix the parser" + " " * (30 - len("❯ fix the parser"))
    assert len(visible) == 30  # padded to the full width


def test_user_band_wraps_long_and_multiline_prompts(monkeypatch):
    monkeypatch.setattr(rendering, "_COLOR_ENABLED", True)
    long_lines = rendering.user_band("x" * 50, 20).split("\n")
    assert len(long_lines) == 3
    assert all(len(_visible(l)) == 20 for l in long_lines)  # every band spans the width
    assert all(l.startswith("\x1b[7m") and l.endswith("\x1b[0m") for l in long_lines)

    multi = rendering.user_band("first\nsecond", 16).split("\n")
    assert len(multi) == 2
    assert _visible(multi[0]).startswith("❯ first")
    assert _visible(multi[1]).startswith("  second")  # continuation aligns under the gutter
    assert all(len(_visible(l)) == 16 for l in multi)


def test_user_band_falls_back_to_plain_without_color(monkeypatch):
    monkeypatch.setattr(rendering, "_COLOR_ENABLED", False)
    band = rendering.user_band("hello", 40)
    assert band == "❯ hello"
    assert "\x1b" not in band


def test_user_band_survives_a_tiny_width(monkeypatch):
    monkeypatch.setattr(rendering, "_COLOR_ENABLED", True)
    band = rendering.user_band("hi", 1)  # narrower than the gutter: must not crash or produce empty bands
    for line in band.split("\n"):
        assert line.startswith("\x1b[7m") and line.endswith("\x1b[0m")
        assert len(_visible(line)) >= len("❯ ")


# ── 0.11.0: untrusted terminal control sequences are neutralised (review R21) ────
def test_strip_controls_removes_escapes_and_keeps_text():
    from scootcli.rendering import strip_controls

    assert strip_controls("hello") == "hello"
    assert strip_controls("keep\ttab and\nnewline") == "keep\ttab and\nnewline"
    assert strip_controls("\x1b[31mred\x1b[0m") == "red"                 # full ANSI colour removed
    assert strip_controls("a\x1b]0;title\x07b") == "ab"                  # OSC title-set removed
    assert strip_controls("over\rwrite") == "overwrite"                  # carriage return dropped
    assert strip_controls("bell\x07 and null\x00") == "bell and null"    # C0 bytes dropped
    assert "\x1b" not in strip_controls("split\x1b")                     # a lone/split ESC is dropped


def test_unified_diff_sanitizes_file_content(monkeypatch):
    from scootcli import rendering

    monkeypatch.setattr(rendering, "_COLOR_ENABLED", False)  # isolate content from scoot's own colour
    diff = rendering.unified_diff("old line\n", "new\x1b[31mline\x07\n", "f.txt")
    assert "\x1b" not in diff and "\x07" not in diff
    assert "newline" in diff  # the injected escape is gone, the surrounding text stays
