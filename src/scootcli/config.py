"""Configuration: ``.env`` files + environment + defaults with a clear precedence.

Precedence (highest wins):
  CLI flag → environment variable → project ``.env`` → global ``~/.config/scoot/.env`` → default.
The model has two more layers between the ``.env`` files and the default: the model saved for this
workspace root, then the one saved for every folder (see ``preferences.py``); ``model_source`` says
which layer answered.

Two optional ``.env`` files are read: the global one in scoot's config home, and the nearest ``.env``
found walking up from the current directory (so a repo-root file applies from any subdirectory).
Only scoot's own keys are imported from them: ``SCOOT_*``, the providers' API-key variables
(``OPENAI_API_KEY``, ...), and the proxy variables. A project's other secrets never enter scoot's
process through a ``.env`` file.

The CLI-flag layer is applied by the caller (``cli.py``) via :meth:`Config.override`; this module
handles the lower layers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import List, Optional, Tuple

# No proxy by default (set HTTPS_PROXY / --proxy when one is required).
DEFAULT_PROXY = ""


# scoot's own config dir + securely-stored provider API keys (written by ``scoot auth set``).
SCOOT_CONFIG_DIR = Path.home() / ".config/scoot"
CREDENTIALS_FILE = SCOOT_CONFIG_DIR / "credentials.json"
# Persisted user preferences (e.g. the chosen model) — survives across launches.
PREFERENCES_FILE = SCOOT_CONFIG_DIR / "preferences.json"

# scoot session persistence: auto-saved conversations for --continue / --resume (see PLAN §14).
STATE_DIR = Path.home() / ".local/state/scoot"
SESSION_RETENTION = 20  # keep only the most recent N sessions on disk


def _config_home() -> Path:
    """User config dir for scoot (honours XDG_CONFIG_HOME)."""
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "scoot"


ENV_FILE_NAME = ".env"
_PROXY_KEYS = ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy")


def global_env_path() -> Path:
    """``~/.config/scoot/.env`` (honours ``XDG_CONFIG_HOME``): the stable place for keys."""
    return _config_home() / ENV_FILE_NAME


def find_project_env(start: Path) -> Optional[Path]:
    """The nearest ``.env`` in ``start`` or an ancestor directory, or ``None``."""
    home = _config_home()
    for directory in (start, *start.parents):
        candidate = directory / ENV_FILE_NAME
        if candidate.is_file() and candidate != home / ENV_FILE_NAME:
            return candidate
    return None


def env_files(start: Path) -> List[Path]:
    """Existing env files in *load order*: project first, then global.

    Loading never overrides a variable that is already set, so the project file wins over the global
    one, and the real environment wins over both.
    """
    files: List[Path] = []
    project = find_project_env(start)
    if project is not None:
        files.append(project)
    glob = global_env_path()
    if glob.is_file() and glob not in files:
        files.append(glob)
    return files


def allowed_env_key(key: str) -> bool:
    """Whether a ``.env`` key is scoot's business: ``SCOOT_*``, provider API keys, proxy settings."""
    if key.startswith("SCOOT_") or key in _PROXY_KEYS:
        return True
    try:
        from .providers import registry

        return any(key in spec.key_env for spec in registry.all_specs())
    except Exception:
        return False


def load_dotenv(env_path: Path, allow=allowed_env_key) -> List[str]:
    """Import allowed ``KEY=VALUE`` lines into ``os.environ`` (never overriding); return imported keys.

    Accepts an optional ``export`` prefix and single or double quotes around the value.
    """
    imported: List[str] = []
    try:
        lines = env_path.read_text().splitlines()
    except OSError:
        return imported
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not allow(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            imported.append(key)
    return imported


def _as_bool(value: str) -> bool:
    return value.strip().lower() not in ("false", "0", "no", "off", "")


DEFAULT_MODEL_ALIAS = "default"  # the default provider's preferred model (gpt-5.3-codex on OpenAI)
AUTO_MODEL_ALIAS = "auto"        # opt-in: pick a model per turn from the live list (routing seed)


def is_model_alias(model: Optional[str]) -> bool:
    return (model or "").strip().lower() in (DEFAULT_MODEL_ALIAS, AUTO_MODEL_ALIAS, "")


MODEL_SOURCES = ("flag", "env", "folder", "everywhere", "default")


def _resolve_model_setting(model_env: Optional[str], root=None) -> Tuple[str, str]:
    """``(model, source)`` below the CLI flag (applied later): SCOOT_MODEL → the model saved for
    ``root`` → the model saved for every folder → default."""
    if model_env:
        return model_env, "env"
    try:
        from .preferences import get_model, model_source

        saved = get_model(root)
        if saved:
            return saved, model_source(root) or "everywhere"
    except Exception:
        pass
    return DEFAULT_MODEL_ALIAS, "default"


def _resolve_logo_setting(logo_env: Optional[str]) -> bool:
    """Mascot on/off precedence (below the --no-logo flag): SCOOT_LOGO env → saved pref → on."""
    if logo_env is not None and logo_env.strip() != "":
        return _as_bool(logo_env)
    try:
        from .preferences import get_logo

        saved = get_logo()
        return True if saved is None else saved
    except Exception:
        return True


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration."""

    proxy: str = DEFAULT_PROXY
    provider: str = ""  # default provider name; empty → first provider with a key, else ollama
    model: str = DEFAULT_MODEL_ALIAS  # "default" | "auto" | a model id, ideally provider/model
    model_source: str = "default"  # where ``model`` came from: flag | env | folder | everywhere | default
    effort: str = "medium"  # reasoning effort for models that take it: low | medium | high | xhigh
    timeout: int = 120
    max_steps: int = 50
    compact_at: int = 100000
    approval: str = "yolo"
    root: Path = Path.cwd()
    verbose: bool = False
    stream: bool = True  # stream tokens live (SSE) when the UI/terminal supports it
    panel: bool = True  # show the persistent bottom status bar in the REPL (TTY only)
    dock: bool = True  # pin the prompt to a fixed bottom row; output scrolls above it (TTY only)
    resume: str = "auto"  # launch resume policy: hint (show last) | auto (reload last) | off
    workspace_context: bool = True  # inject a compact repo map (git + tree) into the agent prompt
    labels: bool = True  # show role labels/gutters (❯ you / ⏺ scoot) in the REPL transcript
    verbosity: str = "full"  # feed detail: full (tool log) | compact (transient tool line) | quiet (no reasoning)
    images: bool = True  # detect dropped image paths + describe them with a vision model (M22)
    vision_model: str = "auto"  # provider/model for image descriptions, or "auto" → pick a capable one
    image_max_bytes: int = 4 * 1024 * 1024  # per-image cap (no downscale without a 3rd-party lib)
    editor: str = "idea"  # external editor for the open_editor tool: idea | vscode
    scope: str = "workspace"  # where file tools may go: workspace (ask once outside it) | anywhere
    env_files: Tuple[str, ...] = ()  # the .env files that were read, in load order (shown by /status)
    logo: bool = True  # mascot in the launch banner + status-bar face (--no-logo / SCOOT_LOGO / /logo)
    emoji: bool = True  # 🛴 transcript label; off → ⏺ for terminals without an emoji font (--no-emoji)

    # ── Loading ────────────────────────────────────────────────────────────────
    @classmethod
    def load(cls, cwd: Optional[Path] = None) -> "Config":
        """Load config from the ``.env`` files + environment variables, falling back to defaults."""
        cwd = (cwd or Path.cwd()).resolve()
        files = env_files(cwd)
        for env_file in files:
            load_dotenv(env_file)

        get = os.environ.get
        root = Path(get("SCOOT_ROOT", str(cwd))).resolve()
        model, model_source = _resolve_model_setting(get("SCOOT_MODEL"), root)
        return cls(
            proxy=get("HTTPS_PROXY") or get("https_proxy") or DEFAULT_PROXY,
            env_files=tuple(str(f) for f in files),
            provider=get("SCOOT_PROVIDER", "").strip().lower(),
            model=model,
            model_source=model_source,
            effort=get("SCOOT_EFFORT", "medium").strip().lower(),
            timeout=int(get("SCOOT_TIMEOUT", "120")),
            max_steps=int(get("SCOOT_MAX_STEPS", "50")),
            compact_at=int(get("SCOOT_COMPACT_AT", "100000")),
            approval=get("SCOOT_APPROVAL", "yolo"),
            root=root,
            verbose=_as_bool(get("SCOOT_VERBOSE", "false")),
            stream=_as_bool(get("SCOOT_STREAM", "true")),
            panel=_as_bool(get("SCOOT_PANEL", "true")),
            dock=_as_bool(get("SCOOT_DOCK", "true")),
            resume=get("SCOOT_RESUME", "auto").strip().lower(),
            workspace_context=_as_bool(get("SCOOT_WORKSPACE_CONTEXT", "true")),
            labels=_as_bool(get("SCOOT_LABELS", "true")),
            verbosity=get("SCOOT_VERBOSITY", "full").strip().lower(),
            images=_as_bool(get("SCOOT_IMAGES", "true")),
            vision_model=get("SCOOT_VISION_MODEL", "auto"),
            image_max_bytes=int(get("SCOOT_IMAGE_MAX_BYTES", str(4 * 1024 * 1024))),
            editor=get("SCOOT_EDITOR", "idea").strip().lower(),
            scope=get("SCOOT_SCOPE", "workspace").strip().lower(),
            logo=_resolve_logo_setting(get("SCOOT_LOGO")),
            emoji=_as_bool(get("SCOOT_EMOJI", "true")),
        )

    def override(self, **kwargs) -> "Config":
        """Return a copy with the given (non-None) fields overridden: the CLI-flag layer.

        A ``model`` here is the flag. A new ``root`` (``--root``) without one re-resolves the saved
        model for that folder, unless the flag or ``SCOOT_MODEL`` already decided it.
        """
        clean = {k: v for k, v in kwargs.items() if v is not None}
        if "root" in clean:
            clean["root"] = Path(clean["root"]).resolve()
        if "model" in clean:
            clean.setdefault("model_source", "flag")
        elif "root" in clean and self.model_source not in ("flag", "env") and clean["root"] != self.root:
            clean["model"], clean["model_source"] = _resolve_model_setting(None, clean["root"])
        return replace(self, **clean)

    def resolve_model(self, task_hint: str = "") -> str:
        """The qualified ``provider/model`` for the configured preference.

        ``default`` is the default provider's preferred model (``gpt-5.3-codex`` on OpenAI). ``auto`` is
        the per-turn heuristic over the live model list; before that list exists it resolves the same
        way as ``default``.
        """
        from .providers.base import qualify
        from .providers.registry import default_provider_name, fallback_model

        if is_model_alias(self.model):
            return fallback_model(self)
        return qualify(default_provider_name(self), self.model)

