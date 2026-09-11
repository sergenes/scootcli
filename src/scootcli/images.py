"""Detect image file paths dropped into a prompt and encode them as data URIs (PLAN §3 / M22).

Dragging a file into a terminal inserts its *path* as text (plain, backslash-escaped, quoted, or a
``file://`` URL) — never the bytes. This module is pure text-parsing + file reads: it pulls recognised
image paths out of a prompt and base64-encodes them for a vision model. No third-party deps.
"""

from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlparse

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".heic",
}

DEFAULT_MAX_BYTES = 4 * 1024 * 1024  # 4 MB — we can't downscale (stdlib only), so cap + warn instead.

# Anchor detection on an image extension, then expand outward to whatever path actually exists on
# disk. This is robust to spaces in the path (escaped *or* not) and to leading text, which naive
# whitespace/quote tokenising gets wrong for real drag-and-drop paths (e.g. OneDrive screenshots).
_IMAGE_EXT_RE = re.compile(r"\.(?:png|jpe?g|gif|webp|bmp|tiff?|heic)\b", re.IGNORECASE)


class ImageTooLargeError(ValueError):
    """Raised when an image exceeds the configured byte cap (we can't resize without a 3rd-party lib)."""


@dataclass(frozen=True)
class EncodedImage:
    """A base64 data-URI-encoded image ready to attach to a vision request."""

    name: str
    mime: str
    data_uri: str
    size: int


def _looks_like_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _try_path(value: str, root: Optional[Path]) -> Optional[Path]:
    """Turn a candidate string into an existing image ``Path``, or ``None``."""
    if not value:
        return None
    if value.startswith("file://"):
        value = unquote(urlparse(value).path)
    try:
        p = Path(value).expanduser()
        if not p.is_absolute() and root is not None:
            p = Path(root) / p
        if p.is_file() and _looks_like_image(p):
            return p.resolve()
    except OSError:
        return None
    return None


def _normalize(text: str) -> str:
    """Make dropped paths detectable: expand ``file://`` URLs and unescape backslash escapes."""
    text = re.sub(r"file://\S+", lambda m: unquote(urlparse(m.group(0)).path), text)
    return re.sub(r"\\ ", " ", text)  # unescape only "\ " (a dragged path's space); leave other backslashes


def _find_image_span(text: str, root: Optional[Path]) -> Optional[Tuple[int, int, Path]]:
    """Find the first ``(start, end, path)`` where ``text[start:end]`` is an existing image file.

    Anchored on an image extension; the start is the left-most ``/``/``~``/beginning such that the
    spanned substring exists on disk — so paths containing spaces are captured whole.
    """
    for m in _IMAGE_EXT_RE.finditer(text):
        end = m.end()
        # Candidate starts: string start, each path separator, and each word start (for relative
        # paths). Ascending order means we try the *longest* (left-most) span first, so a path with
        # spaces is captured whole rather than truncated at a space.
        starts = {0}
        for i, ch in enumerate(text[:end]):
            if ch in "/~":
                starts.add(i)
            elif ch.isspace() and i + 1 < end:
                starts.add(i + 1)
        for s in sorted(starts):
            cand = text[s:end].strip().strip("'\"")
            p = _try_path(cand, root)
            if p is not None:
                # Tighten the removal span: skip leading whitespace but keep a wrapping quote, and
                # swallow a trailing quote, so `'…path…'` is removed whole (no stray quotes left).
                while s < end and text[s] in " \t":
                    s += 1
                ne = end + 1 if (end < len(text) and text[end] in "'\"") else end
                return s, ne, p
    return None


def extract_image_paths(text: str, root: Optional[Path] = None) -> Tuple[str, List[Path]]:
    """Split ``text`` into ``(clean_text, [image_paths])``.

    Only substrings that resolve to an **existing** image file are treated as attachments and removed;
    everything else is left in place. Handles plain / backslash-escaped / quoted / ``file://`` paths,
    including filenames with spaces. Relative paths resolve against ``root`` when given.
    """
    work = _normalize(text)
    paths: List[Path] = []
    while True:
        span = _find_image_span(work, root)
        if span is None:
            break
        s, e, p = span
        paths.append(p)
        work = work[:s] + " " + work[e:]
    # Remove only the runs of horizontal whitespace the removal left, and keep the user's line
    # breaks: collapsing every newline used to turn a multi-line prompt into one line.
    clean = re.sub(r"[ \t]+", " ", work)
    clean = re.sub(r" *\n *", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, paths


def badge_text(text: str, root: Optional[Path] = None) -> str:
    """Return ``text`` with each detected image path replaced by a short ``[Image N]`` badge.

    For display only (e.g. the REPL echo) — the real path is handled separately by
    :func:`extract_image_paths`. If no image paths are present the original text is returned verbatim.
    """
    work = _normalize(text)
    n = 0
    while True:
        span = _find_image_span(work, root)
        if span is None:
            break
        s, e, _ = span
        n += 1
        work = work[:s] + f"[Image {n}]" + work[e:]
    if n == 0:
        return text  # nothing to badge — keep the prompt exactly as typed
    return re.sub(r"\s+", " ", work).strip()


def _sniff_mime(path: Path, raw: bytes) -> str:
    """Detect an image MIME type from magic bytes, falling back to the extension."""
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    guess = mimetypes.guess_type(str(path))[0]
    if guess and guess.startswith("image/"):
        return guess
    return "image/png"


def to_data_uri(path: Path, max_bytes: int = DEFAULT_MAX_BYTES) -> EncodedImage:
    """Read + base64-encode an image into a ``data:`` URI. Raises :class:`ImageTooLargeError` over cap."""
    path = Path(path)
    size = path.stat().st_size  # check before reading, so an oversized file is never loaded
    if max_bytes and size > max_bytes:
        raise ImageTooLargeError(
            f"{path.name} is {size // 1024} KB, over the {max_bytes // 1024} KB limit"
        )
    raw = path.read_bytes()
    mime = _sniff_mime(path, raw)
    b64 = base64.b64encode(raw).decode("ascii")
    return EncodedImage(name=path.name, mime=mime, data_uri=f"data:{mime};base64,{b64}", size=len(raw))


