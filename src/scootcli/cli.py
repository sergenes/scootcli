"""scoot command-line entrypoint.

Usage:
  * ``scoot``                       — open the interactive REPL (banner, ESC-interrupt, slash-cmds)
  * ``scoot "a prompt"``            — one-shot agentic turn (tools + approvals), then exit
  * ``scoot explain <path>``        — preset: explain file(s) (read-only)
  * ``scoot edit <path> -m "..."``  — preset: edit file(s) with an instruction
  * ``scoot models [--provider X]`` — list available models (per provider)
  * ``scoot auth [set|clear <provider>]`` — show provider keys, or save / forget one

Global flags override config: ``--model``, ``--proxy``, ``--root``,
``--approval``/``--yolo``, ``--verbose``, ``--json``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import json as _json
import sys
from typing import List, Optional

from .providers import ProviderPool
from . import __version__
from .config import Config
from .errors import ScootError
from .rendering import color, eprint, redact


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scoot",
        description="scoot: a tiny coding agent that goes where you point it.",
    )
    # Global flags (CLI-flag layer of config precedence).
    parser.add_argument("--version", action="version", version=f"scoot {__version__}")
    parser.add_argument("--check-update", action="store_true",
                        help="Ask PyPI whether a newer scoot exists, print both versions, and exit.")
    parser.add_argument("--model", help="Model as provider/model (e.g. openai/gpt-5.3-codex), 'default', or 'auto'.")
    parser.add_argument("--provider", help="Default provider (openai, ollama, ...) for bare model names.")
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh"],
                        help="Reasoning effort for models that support it (default medium).")
    parser.add_argument("--proxy", help="HTTPS proxy URL.")
    parser.add_argument("--root", help="Workspace root directory.")
    parser.add_argument("--approval", choices=["always", "auto-read", "auto-edits", "yolo"],
                        help="Approval mode for tool calls.")
    parser.add_argument("--yolo", action="store_true", help="Auto-approve all tool calls (shortcut).")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Auto-approve all tool calls for this run (handy for one-shot/scripted use).")
    parser.add_argument("-m", "--message", help="Instruction for 'edit' / focus for 'explain'.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output (models / one-shot).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output.")
    parser.add_argument("--no-stream", action="store_true",
                        help="Disable live token streaming (wait for the full response).")
    parser.add_argument("--no-panel", action="store_true",
                        help="Disable the persistent bottom status bar in the REPL.")
    parser.add_argument("--no-dock", action="store_true",
                        help="Disable the fixed bottom input line (use a plain inline prompt).")
    parser.add_argument("--vision", action="store_true",
                        help="With 'models': list only vision-capable models.")
    parser.add_argument("--no-workspace", action="store_true",
                        help="Don't inject the repo map (git + file tree) into the agent prompt.")
    parser.add_argument("--no-labels", action="store_true",
                        help="Hide the role labels/gutters (❯ you / ⏺ scoot) in the REPL transcript.")
    parser.add_argument("--scope", choices=["workspace", "anywhere"],
                        help="Where file tools may go: workspace (default; the first access outside it asks once) or anywhere.")
    parser.add_argument("--headless", action="store_true",
                        help="Line-delimited JSON on stdin/stdout instead of the REPL (see docs/headless-protocol.md).")
    parser.add_argument("--no-logo", action="store_true",
                        help="Hide the mascot (launch banner art + status-bar face). Persist with /logo off.")
    parser.add_argument("--no-emoji", action="store_true",
                        help="Use the plain ⏺ transcript label instead of 🛴 (terminals without emoji).")
    parser.add_argument("--no-images", action="store_true",
                        help="Don't detect/describe image files dropped into the prompt.")
    parser.add_argument("--vision-model",
                        help="Vision model id for describing dropped images (or 'auto').")
    parser.add_argument("-c", "--continue", dest="continue_latest", action="store_true",
                        help="Resume the most recent session for this directory.")
    parser.add_argument("--resume", metavar="ID", help="Resume a specific saved session by id.")
    parser.add_argument("--resume-last", action="store_true",
                        help="Auto-resume the latest session for this directory on launch (resume=auto).")

    # Positional: a reserved word ('models'), a preset ('explain'/'edit'), or a free-form prompt.
    parser.add_argument(
        "prompt",
        nargs="*",
        help="A prompt, the word 'models', or a preset ('explain <path>' / 'edit <path> -m ...').",
    )
    return parser


def _config_from_args(args: argparse.Namespace) -> Config:
    # The workspace decides which project .env applies, so resolve --root before loading; otherwise
    # `scoot --root ~/other` would take the launch directory's settings into the other project.
    start = Path(args.root).expanduser().resolve() if args.root else None
    return Config.load(cwd=start).override(
        model=args.model,
        provider=args.provider,
        effort=args.effort,
        proxy=args.proxy,
        root=args.root,
        approval="yolo" if (args.yolo or args.yes) else args.approval,
        scope="anywhere" if args.yes else args.scope,
        verbose=True if args.verbose else None,
        stream=False if args.no_stream else None,
        panel=False if args.no_panel else None,
        dock=False if args.no_dock else None,
        resume="auto" if args.resume_last else None,
        workspace_context=False if args.no_workspace else None,
        labels=False if args.no_labels else None,
        images=False if args.no_images else None,
        logo=False if args.no_logo else None,
        emoji=False if args.no_emoji else None,
        vision_model=args.vision_model,
    )


def _cmd_models(pool: ProviderPool, as_json: bool, vision_only: bool = False,
                provider: Optional[str] = None) -> int:
    models = pool.list_models(provider=provider)
    if vision_only:
        from .models import is_vision_model

        models = [m for m in models if is_vision_model({"id": m.id, **m.raw})]
    if as_json:
        print(_json.dumps({
            "models": [{"id": m.id, "provider": m.provider, "name": m.name} for m in models],
            "errors": {k: redact(v) for k, v in pool.list_errors.items()},
        }, indent=2))
        return 0
    for name, err in sorted(pool.list_errors.items()):
        eprint(color(f"⚠ {name}: {redact(err)}", "yellow"))
    if not models:
        print("No models returned.")
        return 0
    by_provider: dict = {}
    for m in models:
        by_provider.setdefault(m.provider, []).append(m)
    default = pool.default_name
    for name in sorted(by_provider):
        tag = "  (default)" if name == default else ""
        print(color(f"{name}{tag}", "bold"))
        for m in sorted(by_provider[name], key=lambda x: x.name):
            print(f"  {color(m.id, 'cyan')}")
    return 0


def _run_once(config: Config, pool: ProviderPool, prompt: str, as_json: bool, resume=None) -> int:
    """Run a single agentic turn (tools + approvals) and print the result, then exit."""
    import threading

    from .agent import Agent
    from .repl import ReplSession, ReplUI

    from .providers.registry import readiness

    ready, message = readiness(config)
    if not ready:  # fail fast with guidance instead of a connection error after retries
        if as_json:
            print(_json.dumps({"status": "error", "model": "", "steps": 0, "content": "",
                               "error": message, "usage": {}}, indent=2))
        else:
            eprint(color(message, "yellow"))
        return 1
    session = ReplSession(config, pool)
    if resume is not None:
        session.apply_record(resume)
    from .hooks import Hooks, session_event, startup_notices, submit_prompt

    session.hooks = Hooks(config.root)
    for note in startup_notices(session):
        eprint(color(f"⚠ {note}", "yellow"))
    session_event(session, "SessionStart", source="resume" if resume is not None else "startup")
    submitted = submit_prompt(session, prompt)
    if submitted is None:
        reason = getattr(session, "hook_block_reason", "") or "a UserPromptSubmit hook blocked it"
        eprint(color(f"⏹ prompt not sent: {reason}", "yellow"))
        session_event(session, "SessionEnd", reason="blocked")
        return 1
    prompt = submitted
    # Fold any dropped image paths into the prompt (best-effort; no-op when none/disabled).
    try:
        from .vision import fold_images_into_text

        prompt = fold_images_into_text(prompt, config, pool)
    except Exception:
        pass
    session.messages.append({"role": "user", "content": prompt})
    # JSON output must be clean, so never stream tokens to stdout in that mode.
    agent_config = config.override(stream=False) if as_json else config
    outcome = Agent(agent_config, pool).run_turn(session, ReplUI(), threading.Event())
    session.autosave()  # persist so `scoot -c` can continue this conversation
    session_event(session, "SessionEnd", reason=outcome.status)

    if as_json:
        print(_json.dumps({
            "status": outcome.status,
            "model": session.active_model,
            "steps": outcome.steps,
            "content": outcome.content,
            "error": outcome.error,
            "usage": session.last_usage,
            "cost": session.session_cost(),
        }, indent=2))
        return 0 if outcome.status == "done" else 1

    if outcome.status == "done":
        if outcome.content.strip() and not outcome.streamed:
            print(outcome.content.strip())
        if config.verbose:
            from .pricing import fmt

            u = session.last_usage
            eprint(color(f"[{session.active_model}] steps={outcome.steps} "
                         f"prompt={u.get('prompt_tokens', '?')} "
                         f"completion={u.get('completion_tokens', '?')} "
                         f"cost={fmt(session.session_cost())}", "gray"))
        return 0
    if outcome.status == "incomplete" and outcome.content.strip() and not outcome.streamed:
        print(outcome.content.strip())
    eprint(color(f"⚠ {redact(outcome.error or outcome.status)}", "red"))
    return 1


def _cmd_auth(config: Config, pool: ProviderPool, words: List[str]) -> int:
    """``scoot auth [set|clear <provider>]``: the /auth slash-command from the shell."""
    from . import commands
    from .repl import ReplSession

    commands.load_builtins()
    session = ReplSession(config, pool)
    commands.get("auth").handler(session, " ".join(words))
    return 0


def _interactive(pool: ProviderPool, resume=None, remember_model: Optional[str] = None) -> int:
    """Launch the persistent REPL (banner, live status, ESC-interrupt, slash-commands).

    ``remember_model`` is the model named on the command line (``--model``), if any. Launching the REPL
    with it counts as choosing it for this folder, exactly like ``/model`` inside the REPL, so a plain
    ``scoot`` here next time starts on the same model. One-shot prompts and headless runs do not save
    it: a flag on a single command is not a choice for the next session.
    """
    from .repl import Repl

    config = pool.config
    if remember_model and remember_model.strip():
        from .preferences import set_model

        set_model(remember_model.strip(), root=config.root)
    # resume=auto (SCOOT_RESUME / --resume-last): reload the latest session for this directory
    # unless the user already picked one explicitly via --resume/--continue.
    if resume is None and getattr(config, "resume", "hint") == "auto":
        from . import sessions

        resume = sessions.latest_for_root(str(config.root))

    # First-run onboarding happens inside the REPL (banner + a setup block), because anything printed
    # here would be wiped by the screen clear the REPL does on start.
    return Repl(config, pool, resume=resume).run()


def _resolve_resume(config: Config, args: argparse.Namespace):
    """Return a SessionRecord to resume (from --resume/--continue), or None. Exits on a bad id."""
    from . import sessions

    if args.resume:
        record = sessions.load(args.resume)
        if record is None:
            eprint(color(f"no session with id '{args.resume}' (try running /sessions).", "yellow"))
            raise SystemExit(1)
        return record
    if args.continue_latest:
        record = sessions.latest_for_root(str(config.root))
        if record is None:
            eprint(color("no saved session to continue for this directory.", "yellow"))
            raise SystemExit(1)
        return record
    return None


def _resolve_prompt(args: argparse.Namespace) -> Optional[str]:
    """Turn positional args (+ presets) into a task prompt, or None for the REPL."""
    from . import presets

    words = args.prompt
    if not words:
        return None
    head, rest = words[0], words[1:]
    if presets.is_preset(head):
        return presets.build_prompt(head, rest, args.message)
    return " ".join(words)


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.check_update:
        from .updates import check, is_newer, upgrade_hint

        current, latest = check()
        if latest is None:
            print(f"scoot {current} (could not reach PyPI to check for updates)")
            return 1
        if is_newer(latest, current):
            print(f"scoot {current}; newer release available: {latest}\n  {upgrade_hint()}")
        else:
            print(f"scoot {current} is the latest release.")
        return 0
    try:
        config = _config_from_args(args)
        pool = ProviderPool(config)

        if args.prompt and args.prompt[0] == "models":
            return _cmd_models(pool, args.json, vision_only=args.vision, provider=args.provider)

        if args.prompt and args.prompt[0] == "auth":
            return _cmd_auth(config, pool, args.prompt[1:])

        prompt = _resolve_prompt(args)
        resume = _resolve_resume(config, args)
        if args.headless:
            if prompt is not None:
                eprint(color("--headless takes prompts on stdin, not as an argument.", "yellow"))
                return 2
            from .headless import run_headless

            return run_headless(config.override(panel=False, dock=False, logo=False), pool, resume=resume)
        if prompt is not None:
            return _run_once(config, pool, prompt, args.json, resume=resume)
        return _interactive(pool, resume=resume, remember_model=args.model)
    except ScootError as exc:
        eprint(color(f"⚠ {redact(str(exc))}", "red"))
        return 1
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())

