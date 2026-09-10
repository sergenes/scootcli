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

## 0.8.0: remembered --model, resize repaint (released 2026-09-07)

`--model` on the REPL command line is saved like `/model`, so a plain `scoot` starts on it next time.
The dock repaints on `SIGWINCH`: the editor wakes at once, locates the transcript's end from the terminal's cursor report, scrolls it up if needed, and redraws region, bar, and input; a resize mid-turn is repaired the same way.

## 0.8.1: a model per folder (released 2026-09-07)

`/model X` and `--model X` are remembered for the workspace; `/model X everywhere` sets the fallback for every folder, `/model forget` drops the folder's choice, `/status` names the layer the model came from.

## 0.9.0: nothing gets lost (released 2026-09-07)

The first release driven by an outside review: worktree finish that never removes uncommitted work, atomic file writes with argument validation, failed-command output reaching the model, resume that keeps this run's approval mode, complete tool-result history after abort or interrupt, cut-off replies reported as incomplete, process-group cancellation for shell and hooks, socket shutdown on ESC, prompt SSE delivery, and a zipapp that exits with the real status.

## 0.10.0: trust boundaries (released 2026-09-08)

The remaining high-priority findings of the same review, the ones that change behaviour and documentation, all shipped:

- Project `.env` gets a narrow allowlist (R02): endpoint URLs, approval, scope, hooks, and config or state directories come only from flags or the user's own configuration.
- Project hooks require a one-time trust decision per repository, denies win over allows across project and global hooks, and provider key variables are stripped from hook environments (R01).
- The README security section says what is true: file tools are scoped, shell commands run with the user's normal access, the denylist is an accident guard (R03). The `yolo` default stays; `rm -r -f` and `git -C x push --force` join the denylist's normalisation.
- The stdlib search fallback and the workspace map skip symlinks and hidden or ignored files (R07); session redaction reaches tool arguments and replay items (R13); session ids are validated as basenames and malformed records are skipped on listing (R17).

## 0.11.0: machine interfaces and small corrections (in progress)

The review's medium findings. Shipped on `dev`:

- ✅ One-shot `--json` is exactly one JSON object on every exit path, diagnostics to stderr (R18).
- ✅ `--root` resolved before the `.env` search (0.10.0), and the `[A]` key and headless request ids (0.10.0); "allow this path" now lasts one call, not the session (R20).
- ✅ Untrusted terminal control characters stripped from diffs, tool output, transcript, and model text (R21).
- ✅ A fallback cycle that cannot alternate between two unavailable models (R16).
- ✅ ripgrep errors told apart from "no matches"; numeric config settings validated; `AGENTS.md` read bounded (part of R15).
- ✅ A shared atomic JSON writer for credentials, preferences, and sessions.

Remaining:

- Bounded subprocess capture (rest of R15): `run_shell` and hook output are still fully buffered by `communicate()` before truncation, and ripgrep output is fully collected before the match cap. Drain each pipe into a bounded buffer so a runaway command cannot exhaust memory. This one needs a live run because it touches the process-group cancellation path, so it gets its own commit.

## Later, as configuration rows

- xAI (Grok), Groq, OpenRouter, LM Studio, vLLM: each is a `ProviderSpec` once its Responses or Chat Completions support is checked.
- Cursor has no public model API; nothing to add.

## Backlog

Verification done 2026-09-06, on Linux by hand: bar, dock, paste, ESC, `/c` copy, and `/compact` on a real long session, all as expected.
Still owed: the same look in iTerm2, and more Linux runs as the first users report (the first two each found a real problem, the third found none).

Small, released in 0.6.0 (2026-09-06): cost per model in `/status`, the opt-in update check, the Ctrl-N mid-turn note, classifier guidance, `install.sh --uninstall`.

Larger:
- Web fetch: a gated, opt-in `fetch_url` tool so the agent can read a page (docs, changelogs, error explanations). Off by default, never auto-approved even in `yolo`, SSRF-guarded, text-only, size-bounded, through the existing transport. Search is deferred. Design note and threat model in [`docs/plans/web-fetch.md`](docs/plans/web-fetch.md).
- Windows: the input dock and ESC handling use termios and raw mode, so scoot runs on Windows only under WSL today; a native path needs the `msvcrt` equivalents and a status bar that survives the console.
- Auto-attach specific open files beyond the injected repo map.
- More presets (`test`, `review`).
- Interactive editing of tool arguments with a real editor instead of a line prompt.
- Wordmark banner (figlet) next to the mascot, if it can be made clean.
