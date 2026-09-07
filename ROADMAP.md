# scoot roadmap

What is shipped is described in `SPEC.md`; this file is what comes next.
Dates are decisions, not promises.

## 0.1.0: first public release (released 2026-09-05)

Published on PyPI as `scootcli`, tagged `v0.1.0`, with `scoot.pyz` attached to the GitHub release.
Verified: a clean virtualenv installs it with no dependencies; tool-calling turns work on OpenAI (`gpt-5.3-codex`) and Ollama (`llama3.2`); the REPL renders in macOS Terminal and tmux.

Done since: CI and branch protection (0.1.0), image prompts and `--continue` verified live on both hosted providers (0.2.0), a real Linux install (0.4.0, which produced the first-run fixes and 0.5.0's scope).
Launch checklist closed 2026-09-06: bar, dock, paste, ESC, and `/c` copy verified by hand on a Linux terminal, and `/compact` on a real long session; both behaved as expected. iTerm2 itself has not had its own look yet.

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

## Website: scootcli.org (parked)

The domain is bought and does nothing yet. The options weighed (a page, Firebase redirects, GoDaddy forwarding), the redirect config, and the GoDaddy steps are kept outside the repo in the maintainer's local `misc/` notes. Pick it up when there are users; until then the README keeps the raw GitHub install line.

## 0.7.0: hook compatibility (released 2026-09-06)

Hooks accept Claude Code's nested `hookSpecificOutput` decisions and match its tool names (`Bash`, `Write`, `Edit`, ...), so a `hooks.json` and its scripts written for Claude Code work with scoot unchanged; `tool_alias` in payloads; `cost` in headless `turn_end` and `--json` output.

## Later, as configuration rows

- xAI (Grok), Groq, OpenRouter, LM Studio, vLLM: each is a `ProviderSpec` once its Responses or Chat Completions support is checked.
- Cursor has no public model API; nothing to add.

## Backlog

Verification done 2026-09-06, on Linux by hand: bar, dock, paste, ESC, `/c` copy, and `/compact` on a real long session, all as expected.
Still owed: the same look in iTerm2, and more Linux runs as the first users report (the first two each found a real problem, the third found none).

Small, released in 0.6.0 (2026-09-06): cost per model in `/status`, the opt-in update check, the Ctrl-N mid-turn note, classifier guidance, `install.sh --uninstall`.

Larger:
- Windows: the input dock and ESC handling use termios and raw mode, so scoot runs on Windows only under WSL today; a native path needs the `msvcrt` equivalents and a status bar that survives the console.
- Auto-attach specific open files beyond the injected repo map.
- More presets (`test`, `review`).
- Interactive editing of tool arguments with a real editor instead of a line prompt.
- Wordmark banner (figlet) next to the mascot, if it can be made clean.
