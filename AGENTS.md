# AGENTS.md

## Overview
`scoot` is a tiny terminal coding agent in plain Python: a prompt-first REPL with an agentic loop (native tool calling, a live progress plan, a `DONE` stop condition), a workspace-aware prompt (auto-injected repo map), live streaming, session persistence and resume, a bottom status bar with a fixed input dock, approval-gated tools with per-session trust, approval modes (`always`/`auto-read`/`auto-edits`/`yolo`), optional git-worktree isolation, and **zero third-party dependencies** (Python 3.9+ stdlib only).
It talks to official model APIs through a provider layer: OpenAI (Responses API) and a local Ollama today, Anthropic next.
Models are addressed as `provider/model`.

## Layout
- `src/scootcli/`: application code.
  - `cli.py`: entry point; dispatches the REPL, one-shot prompts, presets, `models`, and `auth`.
  - `repl.py`: persistent REPL loop, `ReplUI`, `ReplSession` (conversation, accounting, mascot state).
  - `agent.py`: agentic loop, tool routing, `DONE` handling, model fallback, opaque provider items on assistant messages.
  - `providers/`: the provider layer.
    `base.py` (protocol, `ChatRequest`/`ChatResult`, `ProviderSpec`, retries, error mapping), `openai_responses.py` (Responses wire), `openai_chat.py` (Chat Completions wire, for compatible endpoints), `registry.py` (built-in rows, `make_provider`, `ProviderPool`).
  - `transport.py`: stdlib HTTP/1.1 client with SSE streaming, ESC cancellation, `HTTPS_PROXY` (localhost never proxied).
  - `auth.py` / `credentials.py`: API-key lookup (env, then saved) and `0600` storage.
  - `config.py` / `models.py`: `.env` loading with the key allowlist, precedence, the `auto` model heuristic.
  - `prompts.py`: system prompt assembly (root, model, tools, workspace map, `AGENTS.md`).
  - `approvals.py`: approval prompts, modes, shell denylist.
  - `logo.py`: the mascot (banner, `/help`, status-bar face, labels).
  - `keys.py` / `status.py` / `activity.py` / `rendering.py`: raw-mode ESC handling, spinner, ANSI, diffs, redaction.
  - `lineeditor.py` / `panel.py`: fixed-bottom input dock and the status bar.
  - `images.py` / `vision.py`: image path detection, encoding, and description through the provider layer.
  - `sessions.py` / `context.py` / `project.py` / `worktree.py` / `workspace.py` / `clipboard.py`: persistence, compaction, `/init`, worktrees, repo map, clipboard.
  - `commands/`: slash commands (drop-in registry).
  - `tools/`: `read_file`, `list_dir`, `search`, `write_file`, `edit_file`, `run_shell`, `update_plan` (drop-in registry, path-sandboxed).
  - `presets.py`: `explain` and `edit` presets that seed the same agent.
- `tests/`: network-free pytest suite (`conftest.py` restores the environment around each test).
- `scripts/build.sh`: builds `dist/scoot.pyz`, the wheel, and the sdist.
- `pyproject.toml`: packaging; the version lives in `scootcli/__init__.py`.
- `.env.example`: every setting, commented.

## Build & Test
```bash
pip install -e .
python -m pytest -q
./scripts/build.sh
```
Run without installing: `PYTHONPATH=src python3 -m scootcli`.

## Conventions
- Python 3.9+, stdlib only. No third-party imports anywhere in `src/`.
- Tools, slash commands, and providers self-register through drop-in registries; add a file, do not edit the core.
- Consumers depend on the `Provider` protocol only; provider-specific shapes stay inside `providers/`.
- Every fix starts with a failing test; the suite must stay green and free of order dependence.
- Markdown docs: one sentence per line in long files.

## Notes
- Keys: `OPENAI_API_KEY` in the environment or `~/.config/scoot/.env`, or `scoot auth set openai`. Ollama needs none.
- Only `SCOOT_*`, provider key variables, and `HTTPS_PROXY`/`NO_PROXY` are imported from `.env` files.
- Sessions auto-save to `~/.local/state/scoot/sessions/` (owner-only, redacted); `SCOOT_STATE_DIR` overrides the location.
- Default model is the default provider's preferred one (`gpt-5.3-codex` on OpenAI); `SCOOT_MODEL=auto` opts into the per-turn heuristic.
