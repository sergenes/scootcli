"""Image descriptions for the agent: pick a vision-capable model and ask it to describe attachments.

No separate vision backend exists any more: vision is a capability of the provider layer. The model is
``config.vision_model`` when set, otherwise the best vision-capable model among those the configured
providers list (``models.VISION_TIER`` order), falling back to the session's own model.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import List, Optional

from .images import EncodedImage

# Default instruction for the describe pass (kept model-agnostic).
DESCRIBE_INSTRUCTION = (
    "You are assisting a terminal coding agent. Describe the attached image(s) precisely and concisely "
    "for a developer: UI elements, layout, visible text, code, diagrams, colours, and anything a coding "
    "assistant would need to act. If the image shows code or an error message, transcribe the relevant "
    "text verbatim. Output only the description — no preamble or commentary."
)


@dataclass
class VisionResult:
    """Neutral result of a describe call (provider-independent)."""

    text: str
    model: str = ""
    usage: dict = field(default_factory=dict)


def build_content(images: List[EncodedImage], instruction: str) -> list:
    """Build a neutral multimodal ``content`` list (text + image_url parts with data URIs)."""
    content: list = [{"type": "text", "text": instruction}]
    for img in images:
        content.append({"type": "text", "text": f"[{img.name}]"})
        content.append({"type": "image_url", "image_url": {"url": img.data_uri, "detail": "high"}})
    return content


def pick_vision_model(config, provider, fallback: str = "") -> str:
    """``config.vision_model`` if set; else the best vision-capable model the providers list; else ``fallback``."""
    from .models import is_vision_model, resolve_vision

    wanted = (getattr(config, "vision_model", "auto") or "auto").strip()
    if wanted.lower() != "auto":
        try:
            from .providers.base import qualify

            return qualify(provider.default_name, wanted) if hasattr(provider, "default_name") else wanted
        except Exception:
            return wanted
    try:
        infos = provider.list_models()
    except Exception:
        infos = []
    ids = []
    for m in infos:
        mid = getattr(m, "id", None) or (m.get("id") if isinstance(m, dict) else None)
        raw = getattr(m, "raw", None) or (m if isinstance(m, dict) else {})
        if mid and is_vision_model({**raw, "id": mid}):
            ids.append(mid)
    if not fallback:
        try:
            fallback = config.resolve_model()
        except Exception:
            fallback = ""
    return resolve_vision(ids, fallback=fallback)


def describe_images(images: List[EncodedImage], provider, model: str, instruction: str = "",
                    cancel_event=None) -> VisionResult:
    """One describe call: a non-streaming chat whose user message carries the image parts."""
    if not images:
        return VisionResult(text="")
    messages = [{"role": "user", "content": build_content(images, instruction or DESCRIBE_INSTRUCTION)}]
    result = provider.chat(messages, model=model, max_tokens=1500, cancel_event=cancel_event)
    return VisionResult(text=(result.content or "").strip(), model=result.model, usage=result.usage)


def _augment(clean_text: str, blocks: "List[tuple]") -> str:
    """Fold per-image descriptions back into the user's turn as clearly-separated, labelled text.

    ``blocks`` is a list of ``(index, EncodedImage, description)``. Each image gets its own labelled
    section (``Image N (name)``) so the model can tell them apart and diff/compare reliably.
    """
    n = len(blocks)
    header = (f"The user attached {n} image(s); a visual description of each follows "
              f"(labelled to match the [Image N] badges shown in the prompt).")
    sections = [f"[Image {idx} — {img.name}]\n{desc}" for idx, img, desc in blocks]
    body = header + "\n\n" + "\n\n".join(sections)
    return f"{clean_text}\n\n{body}" if clean_text else body


def fold_images_into_text(text, config, provider, ui=None, cancel_event=None, model: str = "") -> str:
    """Detect image paths in ``text``, describe them, and return the text augmented with the result.

    Best-effort and non-destructive: if images are disabled or none are found, ``text`` is returned
    unchanged. Once images *are* detected, the raw paths are always stripped from the returned text —
    on a describe failure a short "couldn't process" note is folded in instead of leaking the paths
    (which the agent can't read and would get confused by). A genuine user interrupt is re-raised so
    the caller can abort the turn cleanly rather than proceeding with a half-processed prompt.
    """
    if not getattr(config, "images", True) or provider is None:
        return text

    from .errors import Interrupted
    from .images import extract_image_paths, to_data_uri

    try:
        clean, paths = extract_image_paths(text, root=getattr(config, "root", None))
    except Exception:
        return text
    if not paths:
        return text

    def _note(reason: str) -> str:
        # Never echo the raw file paths back into the conversation.
        msg = f"[{len(paths)} image(s) were attached but could not be read right now ({reason}).]"
        return f"{clean}\n\n{msg}".strip() if clean else msg

    try:
        max_bytes = getattr(config, "image_max_bytes", None) or 4 * 1024 * 1024
        encoded: List[EncodedImage] = []
        for p in paths:
            try:
                encoded.append(to_data_uri(p, max_bytes=max_bytes))
            except Exception as exc:  # oversized/unreadable — skip it, keep going
                if ui is not None and hasattr(ui, "assistant"):
                    ui.assistant(f"skipping image {getattr(p, 'name', p)}: {exc}")
        if not encoded:
            return _note("encoding failed")
        vision_model = pick_vision_model(config, provider, fallback=model)
        if not vision_model:
            return _note("no vision-capable model")

        ce = cancel_event or threading.Event()

        def _describe_each():
            # Describe each image in its *own* call so a multi-image prompt gets a complete,
            # correctly-attributed description per image (one shared call tends to merge or drop some).
            out = []
            for idx, img in enumerate(encoded, 1):
                res = describe_images([img], provider, vision_model, cancel_event=ce)
                out.append((idx, img, (res.text or "").strip()))
            return out

        if ui is not None and hasattr(ui, "activity"):
            with ui.activity(f"looking at {len(encoded)} image(s)…", ce):
                described = _describe_each()
        else:
            described = _describe_each()

        blocks = [(idx, img, desc) for idx, img, desc in described if desc]
        if not blocks:
            return _note("no description returned")
        if ui is not None and hasattr(ui, "assistant"):
            ui.assistant(f"read {len(encoded)} image(s): "
                         + ", ".join(img.name for img in encoded))
        return _augment(clean, blocks)
    except Interrupted:
        raise  # a real user interrupt → let the caller abort the turn (don't leak raw paths)
    except Exception:
        return _note("vision unavailable")

