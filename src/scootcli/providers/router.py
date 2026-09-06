"""Routing for the ``auto`` model alias: pick a ``provider/model`` per turn.

``auto`` is opt-in (``SCOOT_MODEL=auto`` or ``/model auto``) because routing costs a decision and,
with a classifier, an extra model call. The default preference stays the default provider's preferred
model. A turn is routed once, at its first model call, and stays on that model: switching mid-turn
would break the replay of reasoning and thinking items across tool calls.

Rules come from ``~/.config/scoot/router.json`` (or the file named by ``SCOOT_ROUTER``), ordered,
first match wins, a rule whose target provider is not configured is skipped:

    {
      "rules": [
        {"when": {"has_images": true},        "use": "anthropic/claude-opus-5"},
        {"when": {"complex": true},           "use": "openai/gpt-5.3-codex"},
        {"when": {"est_tokens_over": 60000},  "use": "anthropic/claude-sonnet-5"},
        {"when": {"prompt_matches": "(?i)translate|summari[sz]e"}, "use": "ollama/qwen3"}
      ],
      "default": "ollama/llama3.2",
      "classifier": {
        "model": "ollama/llama3.2",
        "tiers": {"simple": "ollama/llama3.2", "coding": "openai/gpt-5.3-codex", "hard": "anthropic/claude-opus-5"}
      }
    }

Without a file, the built-in rule set is the heuristic scoot always had: a strong coding model for
multi-step or editing prompts, a cheaper one for short questions, chosen from the live model list.
A classifier, when configured, asks a small model one question ("simple, coding, or hard?") and maps
the answer to a tier; on any failure the rules decide.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..errors import ScootError
from .base import split_model_id

_TIERS = ("simple", "coding", "hard")
_CLASSIFY_PROMPT = (
    "You route requests sent to a coding agent to one of three tiers. Answer with exactly one word.\n"
    "simple: a question, a short reply, a one-line lookup, a chat message; nothing in the repository changes.\n"
    "coding: the agent must create, edit, or delete files, add features or tests, fix a known bug, or run commands.\n"
    "hard: debugging or design that spans many files or components, performance or concurrency problems, "
    "architecture, migrations.\n\n"
    "Examples:\n"
    "Request: what does this function return? -> simple\n"
    "Request: reply with the word pong -> simple\n"
    "Request: add a --verbose flag to cli.py and a test for it -> coding\n"
    "Request: rename the User class to Account everywhere -> coding\n"
    "Request: the scheduler and the worker pool deadlock under load; find out why and propose a redesign -> hard\n"
    "Request: migrate the storage layer from SQLite to Postgres without downtime -> hard\n\n"
    "Request: "
)


@dataclass
class Decision:
    model: str
    reason: str
    rule: Optional[dict] = None


@dataclass
class RouterConfig:
    rules: List[dict] = field(default_factory=list)
    default: str = ""
    classifier: Optional[dict] = None
    source: str = "built-in"
    error: str = ""


def config_path() -> Path:
    override = os.environ.get("SCOOT_ROUTER", "").strip()
    if override:
        return Path(override).expanduser()
    from ..config import _config_home

    return _config_home() / "router.json"


def load_config() -> RouterConfig:
    """The router file if present and valid; otherwise the built-in heuristic (with the error noted)."""
    path = config_path()
    if not path.is_file():
        return RouterConfig()
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("top level must be an object")
        rules = data.get("rules") or []
        if not isinstance(rules, list) or not all(isinstance(r, dict) and r.get("use") for r in rules):
            raise ValueError("rules must be a list of {\"when\": {...}, \"use\": \"provider/model\"}")
        classifier = data.get("classifier")
        if classifier is not None and not (isinstance(classifier, dict) and classifier.get("model")
                                           and isinstance(classifier.get("tiers"), dict)):
            raise ValueError("classifier needs a model and a tiers object")
        return RouterConfig(rules=rules, default=str(data.get("default") or ""), classifier=classifier,
                            source=str(path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return RouterConfig(source=str(path), error=str(exc))


# ── hints ──────────────────────────────────────────────────────────────────────
def compute_hints(session) -> dict:
    """What the rules can see about the turn that is about to start."""
    from ..context import estimate_messages_tokens
    from ..models import looks_complex

    messages = list(getattr(session, "messages", []) or [])
    last_user = ""
    has_images = False
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                last_user = content
                has_images = "[Image 1" in content or "image(s) were attached" in content
            elif isinstance(content, list):
                last_user = " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("text")).strip()
                has_images = any(p.get("type") == "image_url" for p in content if isinstance(p, dict))
            break
    return {
        "task_text": last_user,
        "complex": looks_complex(last_user),
        "has_images": has_images,
        "needs_tools": True,
        "est_tokens": estimate_messages_tokens(messages),
        "turns": sum(1 for m in messages if m.get("role") == "user"),
    }


def _matches(when: dict, hints: dict) -> bool:
    for key, want in (when or {}).items():
        if key == "est_tokens_over":
            if not int(hints.get("est_tokens", 0)) > int(want):
                return False
        elif key == "prompt_matches":
            try:
                if not re.search(str(want), hints.get("task_text", "")):
                    return False
            except re.error:
                return False
        elif key in ("complex", "has_images", "needs_tools"):
            if bool(hints.get(key)) != bool(want):
                return False
        else:
            return False  # unknown condition never matches, so a typo cannot silently route everything
    return True


# ── the router ─────────────────────────────────────────────────────────────────
class Router:
    def __init__(self, config: Optional[RouterConfig] = None):
        self.config = config or load_config()
        self.last: Optional[Decision] = None

    def _provider_ok(self, model: str) -> bool:
        from ..auth import is_configured
        from . import registry

        head, _ = split_model_id(model)
        spec = registry.get(head) if head else None
        return spec is not None and is_configured(spec)

    def choose(self, session, hints: dict, available: List[str], fallback: str,
               classify=None) -> Decision:
        """Pick the model for this turn. ``classify(model, text) -> str`` is injected for testability."""
        decision = self._choose(session, hints, available, fallback, classify)
        self.last = decision
        try:
            session.route_reason = decision.reason
        except Exception:
            pass
        return decision

    def _choose(self, session, hints, available, fallback, classify) -> Decision:
        cfg = self.config
        if cfg.classifier and classify is not None:
            tier = self._classify(classify, hints.get("task_text", ""))
            if tier:
                target = cfg.classifier["tiers"].get(tier, "")
                if target and self._provider_ok(target):
                    return Decision(target, f"classifier ({cfg.classifier['model']}) said {tier}")
        for rule in cfg.rules:
            if _matches(rule.get("when") or {}, hints) and self._provider_ok(rule["use"]):
                return Decision(rule["use"], f"rule {json.dumps(rule.get('when') or {})}", rule)
        if cfg.default and self._provider_ok(cfg.default):
            return Decision(cfg.default, "router default")
        from ..models import resolve_auto

        picked = resolve_auto(hints.get("task_text", ""), available, fallback=fallback)
        why = "strong tier" if hints.get("complex") else "cheap tier"
        return Decision(picked, f"built-in heuristic: {why} from the live model list")

    def _classify(self, classify, text: str) -> Optional[str]:
        model = self.config.classifier["model"]
        if not self._provider_ok(model):
            return None
        try:
            answer = (classify(model, _CLASSIFY_PROMPT + text[:4000] + "\nTier:") or "").strip().lower()
        except ScootError:
            return None
        except Exception:
            return None
        for tier in _TIERS:
            if tier in answer:
                return tier
        return None


def describe(router: Router) -> List[str]:
    """Lines for ``/route``: where rules come from, the rules, and the last decision."""
    cfg = router.config
    lines = [f"rules: {cfg.source}" + (f"  (invalid, using built-in: {cfg.error})" if cfg.error else "")]
    if cfg.classifier:
        lines.append(f"classifier: {cfg.classifier['model']} → " + ", ".join(
            f"{k}={v}" for k, v in cfg.classifier["tiers"].items()))
        if str(cfg.classifier["model"]).startswith("ollama/"):
            lines.append("  note: small local models are rough judges here (about half right in our test); "
                         "a hosted mini model such as openai/gpt-5-mini classifies better for a few hundred tokens")
    for i, rule in enumerate(cfg.rules, 1):
        lines.append(f"  {i}. when {json.dumps(rule.get('when') or {})} → {rule['use']}")
    if cfg.default:
        lines.append(f"  default → {cfg.default}")
    if not cfg.rules and not cfg.default:
        lines.append("  built-in heuristic: complex prompts → strong tier, short questions → cheap tier")
    if router.last:
        lines.append(f"last decision: {router.last.model}  ({router.last.reason})")
    return lines
