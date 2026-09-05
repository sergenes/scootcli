"""LLM providers behind one protocol.

Consumers speak scoot's neutral conversation format (the OpenAI chat shape: ``system``/``user``/
``assistant``/``tool`` messages, ``tool_calls`` on assistant messages, ``{"type": "function", ...}`` tool
schemas, ``image_url`` parts with data URIs). Each wire adapter translates that to a vendor API at the
edge. Models are addressed as ``provider/model`` (``openai/gpt-5.3-codex``, ``ollama/llama3.2``).

Public surface:
  * :class:`~scootcli.providers.base.ChatResult` / :class:`~scootcli.providers.base.ChatRequest`
  * :class:`~scootcli.providers.base.ProviderSpec` (a registry row) and :func:`registry.register`
  * :class:`~scootcli.providers.registry.ProviderPool`: what the app holds; dispatches by model prefix.
"""

from .base import ChatRequest, ChatResult, ModelInfo, ProviderSpec, qualify, split_model_id
from .registry import ProviderPool, default_provider_name, fallback_model, make_provider

__all__ = [
    "ChatRequest", "ChatResult", "ModelInfo", "ProviderSpec", "ProviderPool",
    "default_provider_name", "fallback_model", "make_provider", "qualify", "split_model_id",
]
