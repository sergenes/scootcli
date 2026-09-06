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

## 0.3.0: routing

- `RouterProvider`: implements the `Provider` protocol and delegates each request to a real provider using `ChatRequest.hints` (task text, images, tools needed, estimated tokens) and rules from `SCOOT_ROUTER`.
- Optional classifier: ask a small local model (Ollama) which tier a request needs before sending it to a hosted one.
- Per-provider spend accounting in `/status` from the normalized usage.
- The `auto` heuristic in `models.py` becomes the router's first rule set.

## Later, as configuration rows

- xAI (Grok), Groq, OpenRouter, LM Studio, vLLM: each is a `ProviderSpec` once its Responses or Chat Completions support is checked.
- Cursor has no public model API; nothing to add.

## Backlog

- Auto-attach specific open files beyond the injected repo map.
- Richer `/status`: per-model tokens and cost.
- More presets (`test`, `review`).
- Interactive editing of tool arguments with a real editor instead of a line prompt.
- Wordmark banner (figlet) next to the mascot, if it can be made clean.
