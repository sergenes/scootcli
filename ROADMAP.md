# scoot roadmap

What is shipped is described in `SPEC.md`; this file is what comes next.
Dates are decisions, not promises.

## 0.1.0: first public release (released 2026-09-05)

Published on PyPI as `scootcli`, tagged `v0.1.0`, with `scoot.pyz` attached to the GitHub release.
Verified: a clean virtualenv installs it with no dependencies; tool-calling turns work on OpenAI (`gpt-5.3-codex`) and Ollama (`llama3.2`); the REPL renders in macOS Terminal and tmux.

Still open from the launch checklist, to be done as 0.1.x fixes come in:
- A pass on iTerm2 and one Linux terminal; an image prompt through the vision path; `--continue` and `/compact` on a real session.
- A CI workflow that runs the test suite, then branch protection on `main`.

## 0.2.0: Anthropic

- `providers/anthropic.py`: Messages API adapter (`POST /v1/messages`, `x-api-key`, `anthropic-version`).
- Mapping: `system` top-level; tools with `input_schema`; `tool_use` blocks to tool calls and `tool_result` blocks (all of one step in a single user message) back; streaming events `content_block_start` / `content_block_delta` (`text_delta`, `input_json_delta`) / `content_block_stop` / `message_delta`; usage from `message_start` plus `message_delta`.
- Thinking blocks ride on the same opaque `provider_items` rule as OpenAI reasoning; `SCOOT_EFFORT` maps to `output_config.effort`; `max_tokens` required and generous; `stop_reason: "refusal"` becomes a clear error; top-level `cache_control: {"type": "ephemeral"}` since the system prompt and tool list are stable prefixes; images as base64 blocks.
- Default model `claude-opus-5`; registry row with `ANTHROPIC_API_KEY`.
- Live check with a real key before release.

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
