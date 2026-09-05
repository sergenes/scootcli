"""Tests for image descriptions through the provider layer.

Network-free: a fake provider stands in for the ProviderPool, so we assert the multimodal payload
shape, vision-model selection, and the best-effort fold-into-text wiring.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

from scootcli.images import EncodedImage
from scootcli.providers import ModelInfo
from scootcli.vision import build_content, describe_images, fold_images_into_text, pick_vision_model

_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
)


class _FakeResult:
    def __init__(self, content, model):
        self.content = content
        self.model = model
        self.usage = {"prompt_tokens": 10, "completion_tokens": 5}


class _FakeProvider:
    """Records the last chat() call and returns a canned description."""

    default_name = "openai"

    def __init__(self, models=None, answer="a red button on a white screen"):
        names = models or ["openai/gpt-5.3-codex", "openai/text-embedding-3-small", "ollama/llama3.2"]
        self._models = [ModelInfo(id=m, provider=m.split("/")[0], name=m.split("/")[1]) for m in names]
        self._answer = answer
        self.last = None
        self.calls = 0

    def list_models(self):
        return self._models

    def chat(self, messages, model=None, max_tokens=None, cancel_event=None, **kw):
        self.calls += 1
        self.last = {"messages": messages, "model": model, "max_tokens": max_tokens}
        return _FakeResult(self._answer, model)


class _Cfg:
    images = True
    vision_model = "auto"
    image_max_bytes = 4 * 1024 * 1024
    root = None

    def resolve_model(self):
        return "openai/gpt-5.3-codex"


def _img(name="a.png") -> EncodedImage:
    return EncodedImage(name=name, mime="image/png", data_uri="data:image/png;base64,AAAA", size=4)


def _tmp_png() -> Path:
    d = Path(tempfile.mkdtemp(prefix="scoot-vis-"))
    p = d / "shot.png"
    p.write_bytes(_TINY_PNG)
    return p


def test_build_content_shape():
    content = build_content([_img("a.png"), _img("b.png")], "describe")
    assert content[0] == {"type": "text", "text": "describe"}
    assert content[1]["text"] == "[a.png]"
    assert content[2]["type"] == "image_url"
    assert content[2]["image_url"]["detail"] == "high"
    assert content[2]["image_url"]["url"].startswith("data:image/png;base64,")
    assert len(content) == 1 + 2 * 2


def test_pick_vision_model_prefers_capable_live_model():
    assert pick_vision_model(_Cfg(), _FakeProvider()) == "openai/gpt-5.3-codex"
    # Only local models listed → the local vision model wins over a text-only one.
    p = _FakeProvider(models=["ollama/llama3.2", "ollama/qwen2.5vl:7b"])
    assert pick_vision_model(_Cfg(), p) == "ollama/qwen2.5vl:7b"


def test_pick_vision_model_respects_explicit_setting():
    class _Explicit(_Cfg):
        vision_model = "gpt-4o"

    assert pick_vision_model(_Explicit(), _FakeProvider()) == "openai/gpt-4o"  # bare name qualified

    class _Qualified(_Cfg):
        vision_model = "ollama/llama3.2-vision"

    assert pick_vision_model(_Qualified(), _FakeProvider()) == "ollama/llama3.2-vision"


def test_pick_vision_model_falls_back_when_nothing_is_capable():
    p = _FakeProvider(models=["ollama/llama3.2"])
    assert pick_vision_model(_Cfg(), p, fallback="ollama/llama3.2") == "ollama/llama3.2"


def test_describe_builds_multimodal_call():
    p = _FakeProvider()
    result = describe_images([_img()], p, "openai/gpt-5.3-codex", instruction="what is this?")
    assert result.text == "a red button on a white screen"
    assert p.last["model"] == "openai/gpt-5.3-codex"
    msg = p.last["messages"][0]
    assert msg["role"] == "user" and isinstance(msg["content"], list)
    assert any(b.get("type") == "image_url" for b in msg["content"])


def test_fold_images_describes_and_augments():
    p = _FakeProvider(answer="Login screen with an email field.")
    img = _tmp_png()
    out = fold_images_into_text(f"what is on {img}?", _Cfg(), p)
    assert out.startswith("what is on")
    assert str(img) not in out
    assert "Login screen with an email field." in out
    assert "shot.png" in out


def test_fold_images_describes_each_image_separately():
    d = Path(tempfile.mkdtemp(prefix="scoot-vis2-"))
    a, b = d / "before.png", d / "after.png"
    a.write_bytes(_TINY_PNG)
    b.write_bytes(_TINY_PNG)
    p = _FakeProvider(answer="a screen")
    out = fold_images_into_text(f"diff {a} and {b}", _Cfg(), p)
    assert p.calls == 2
    assert "[Image 1 — before.png]" in out
    assert "[Image 2 — after.png]" in out


def test_fold_images_noop_when_disabled():
    class _Off(_Cfg):
        images = False

    img = _tmp_png()
    text = f"describe {img}"
    assert fold_images_into_text(text, _Off(), _FakeProvider()) == text


def test_fold_images_noop_without_provider():
    text = "no provider, no change"
    assert fold_images_into_text(text, _Cfg(), None) == text


def test_fold_images_noop_when_no_images_present():
    text = "just a normal question about python"
    assert fold_images_into_text(text, _Cfg(), _FakeProvider()) == text


def test_fold_images_survives_vision_failure():
    class _Boom(_FakeProvider):
        def chat(self, *a, **k):
            raise RuntimeError("vision backend down")

    img = _tmp_png()
    out = fold_images_into_text(f"look at {img}", _Cfg(), _Boom())
    assert str(img) not in out and "look at" in out and "could not be read" in out


def test_fold_images_reraises_interrupt():
    from scootcli.errors import Interrupted

    class _Int(_FakeProvider):
        def chat(self, *a, **k):
            raise Interrupted("cancelled")

    img = _tmp_png()
    try:
        fold_images_into_text(f"look at {img}", _Cfg(), _Int())
        raise AssertionError("expected Interrupted to propagate")
    except Interrupted:
        pass
