# scoot roadmap

What is shipped is described in `SPEC.md`; this file is what comes next.
Dates are decisions, not promises.

## 0.1.0: first public release (released 2026-09-05)

Published on PyPI as `scootcli`, tagged `v0.1.0`, with `scoot.pyz` attached to the GitHub release.
Verified: a clean virtualenv installs it with no dependencies; tool-calling turns work on OpenAI (`gpt-5.3-codex`) and Ollama (`llama3.2`); the REPL renders in macOS Terminal and tmux.

Still open from the launch checklist, to be done as 0.1.x fixes come in:
- A pass on iTerm2 and one Linux terminal; an image prompt through the vision path; `--continue` and `/compact` on a real session.
- A CI workflow that runs the test suite, then branch protection on `main`.

## 0.2.0: Anthropic (released 2026-09-06)

The Anthropic Messages API adapter on the provider layer, live-verified on `claude-opus-5` (tool calling with thinking replay, streaming and non-streaming, images, resume) plus the `open_editor` tool, the non-interactive `run_shell`, and the spinner timer.

## 0.3.0: routing and a curl install (released 2026-09-06)

- ✅ **Install script** (2026-09-06). `install.sh` at the repo root, served raw from GitHub: checks for Python 3.9+, downloads the latest `scoot.pyz` from the GitHub release, installs it as `~/.local/bin/scoot`, prints a PATH hint. The README lists it as the second install option after `pipx`:
  `curl -fsSL https://raw.githubusercontent.com/sergenes/scootcli/main/install.sh | bash`
- ✅ **Routing** (2026-09-06). `providers/router.py`: `auto` is opt-in and routes once per turn from a classifier (optional), then `router.json` rules, then a default, then the built-in heuristic. Design note: routing is a model chooser at turn start rather than a provider wrapper, because a turn must stay on one model for reasoning and thinking replay to work; the pool still dispatches by prefix.
- ✅ Per-model spend in `/status`, `/route` explains decisions.

## 0.4.0: hooks and headless mode (released 2026-09-06)

The standard machine interfaces of a coding agent, so editors, automation, and remote-control tools can drive scoot without scraping its terminal.
Detailed plan: `docs/plans/0.4.0-hooks-and-headless.md`.

- **Hooks**: shell commands on lifecycle events (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `Notification`, `SessionEnd`) with JSON payloads on stdin and decisions on stdout, configured in `~/.config/scoot/hooks.json` and `.scoot/hooks.json`. Payload shapes follow the de-facto standard set by Claude Code so existing hook scripts work with little change. `/hooks` lists them; `SCOOT_HOOKS=0` disables.
- **Headless mode**: `scoot --headless`, line-delimited JSON over stdin and stdout: prompts, notes, and approval answers in; streamed text, tool calls, approval requests, results, plan updates, usage, and errors out. Same sessions, tools, approvals, and hooks as the REPL. Versioned protocol.

## 0.5.0: scope (released 2026-09-06)

The workspace is the default, not a wall: a file tool that needs a path outside it asks once (this path, its directory, anywhere), with `--scope anywhere` / `/scope` to skip or manage; headless `scope_request`. Found on a real Linux run.

## Later, as configuration rows

- xAI (Grok), Groq, OpenRouter, LM Studio, vLLM: each is a `ProviderSpec` once its Responses or Chat Completions support is checked.
- Cursor has no public model API; nothing to add.

## Backlog

- Auto-attach specific open files beyond the injected repo map.
- Richer `/status`: per-model tokens and cost.
- More presets (`test`, `review`).
- Interactive editing of tool arguments with a real editor instead of a line prompt.
- Wordmark banner (figlet) next to the mascot, if it can be made clean.
