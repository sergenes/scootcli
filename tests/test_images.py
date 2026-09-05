"""Tests for image path extraction + data-URI encoding (PLAN §3 / M22).

Network-free: writes tiny real images to a temp dir and exercises the pure parsing/encoding helpers.

Run: PYTHONPATH=src python3 tests/test_images.py
"""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

from scootcli.images import (
    ImageTooLargeError,
    extract_image_paths,
    to_data_uri,
)

# A real 1x1 PNG (67 bytes), same asset the reference probe uses.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
)


def _png(dirpath: Path, name: str) -> Path:
    p = dirpath / name
    p.write_bytes(_TINY_PNG)
    return p


def _tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="scoot-img-"))


def test_extract_plain_path():
    d = _tmpdir()
    img = _png(d, "shot.png")
    clean, paths = extract_image_paths(f"explain {img} please")
    assert clean == "explain please"
    assert paths == [img.resolve()]


def test_extract_quoted_path_with_spaces():
    d = _tmpdir()
    img = _png(d, "my shot.png")
    clean, paths = extract_image_paths(f"look at '{img}' now")
    assert clean == "look at now"
    assert paths == [img.resolve()]


def test_extract_backslash_escaped_spaces():
    d = _tmpdir()
    img = _png(d, "my shot.png")
    escaped = str(img).replace(" ", "\\ ")
    clean, paths = extract_image_paths(f"check {escaped} thanks")
    assert clean == "check thanks"
    assert paths == [img.resolve()]


def test_extract_file_url():
    d = _tmpdir()
    img = _png(d, "a.png")
    clean, paths = extract_image_paths(f"file://{img}")
    assert clean == ""
    assert paths == [img.resolve()]


def test_extract_multiple_images():
    d = _tmpdir()
    a, b = _png(d, "a.png"), _png(d, "b.jpg")
    b.write_bytes(b"\xff\xd8\xff\xe0stub-jpeg")
    clean, paths = extract_image_paths(f"compare {a} and {b}")
    assert clean == "compare and"
    assert paths == [a.resolve(), b.resolve()]


def test_non_image_and_missing_tokens_are_left_alone():
    d = _tmpdir()
    real = _png(d, "real.png")
    missing = d / "nope.png"
    textfile = d / "notes.txt"
    textfile.write_text("hi")
    text = f"see {real} and {missing} and {textfile} end"
    clean, paths = extract_image_paths(text)
    assert paths == [real.resolve()]
    # The missing image and the .txt stay in the text verbatim.
    assert str(missing) in clean
    assert str(textfile) in clean


def test_no_paths_returns_text_unchanged():
    clean, paths = extract_image_paths("just a normal prompt, don't touch it")
    assert paths == []
    assert clean == "just a normal prompt, don't touch it"


def test_trailing_punctuation_glued_to_path_is_tolerated():
    d = _tmpdir()
    img = _png(d, "shot.png")
    clean, paths = extract_image_paths(f"what is on {img}?")
    assert paths == [img.resolve()]
    assert clean == "what is on ?"  # the path is removed; the '?' stays


def test_path_at_line_start_with_trailing_instruction():
    # Mirrors a real drag-drop: absolute path first, then the instruction typed after it.
    d = _tmpdir()
    img = _png(d, "a.png")
    clean, paths = extract_image_paths(f"{img} describe what you see on this image")
    assert paths == [img.resolve()]
    assert clean == "describe what you see on this image"


def test_filename_with_spaces_even_when_partly_unescaped():
    # OneDrive/Finder screenshots often contain spaces; drag-drop escaping can be inconsistent.
    d = _tmpdir()
    img = _png(d, "Screenshot 2026-08-31 at 1.40.14 PM.png")
    escaped = str(img).replace(" ", "\\ ")
    # Only some spaces escaped (like the reported bug), still resolves via existence check.
    messy = escaped.replace("1.40.14\\ PM", "1.40.14 PM")
    clean, paths = extract_image_paths(f"{messy} describe what you see on this image")
    assert paths == [img.resolve()]
    assert clean == "describe what you see on this image"


def test_relative_path_resolves_against_root():
    d = _tmpdir()
    _png(d, "rel.png")
    clean, paths = extract_image_paths("open rel.png", root=d)
    assert paths == [(d / "rel.png").resolve()]
    assert clean == "open"


def test_to_data_uri_encodes_png():
    d = _tmpdir()
    img = _png(d, "a.png")
    enc = to_data_uri(img)
    assert enc.mime == "image/png"
    assert enc.data_uri.startswith("data:image/png;base64,")
    assert enc.name == "a.png"
    assert enc.size == len(_TINY_PNG)
    # Round-trips back to the original bytes.
    payload = enc.data_uri.split(",", 1)[1]
    assert base64.b64decode(payload) == _TINY_PNG


def test_to_data_uri_sniffs_jpeg_by_magic_bytes():
    d = _tmpdir()
    p = d / "weird.png"  # wrong extension on purpose
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 20)
    enc = to_data_uri(p)
    assert enc.mime == "image/jpeg"  # magic bytes win over the extension


def test_to_data_uri_rejects_oversized():
    d = _tmpdir()
    img = _png(d, "a.png")
    try:
        to_data_uri(img, max_bytes=10)
        raise AssertionError("expected ImageTooLargeError")
    except ImageTooLargeError:
        pass


# ── M22: badge display (hide long paths in the UI) ──────────────────────────────────


def test_badge_text_replaces_paths_with_numbered_badges():
    from scootcli.images import badge_text

    d = _tmpdir()
    a, b = _png(d, "a.png"), _png(d, "b.png")
    out = badge_text(f"compare {a} and {b} closely")
    assert out == "compare [Image 1] and [Image 2] closely"


def test_badge_text_single_image_at_start():
    from scootcli.images import badge_text

    d = _tmpdir()
    img = _png(d, "shot.png")
    out = badge_text(f"{img} describe what you see")
    assert out == "[Image 1] describe what you see"


def test_badge_text_leaves_normal_prompt_verbatim():
    from scootcli.images import badge_text

    text = "explain this backslash \\ and don't touch it"
    assert badge_text(text) == text  # no images → unchanged, even with a stray backslash


if __name__ == "__main__":
    import types

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")

