# scoot: behaviour specification

This document describes what `scoot` 0.1.0 does, as shipped.
It is the reference for what a change must preserve; what is planned lives in `ROADMAP.md`, and why things are built the way they are lives in `DESIGN.md`.
Each numbered statement is testable, and most have a network-free test.

## 1. Identity and constraints

1.1 The command is `scoot`; the PyPI distribution and the Python package are `scootcli`.
1.2 The implementation uses only the Python standard library and runs on Python 3.9 or newer on macOS and Linux.
1.3 The tool ships as a wheel, an sdist, and a single-file zipapp (`scoot.pyz`) that runs without installation.
1.3a `install.sh` at the repository root installs the zipapp from the latest GitHub release (or `SCOOT_VERSION`) as `SCOOT_INSTALL_DIR/scoot` (default `~/.local/bin`), after checking for Python 3.9+ and verifying the download runs; it prints a PATH hint when needed.
1.4 Nothing scoot does requires network access except calls to the configured model providers.

## 2. Providers and models

2.1 A model id is `provider/model` (`openai/gpt-5.3-codex`, `ollama/llama3.2`).
A bare model name is resolved against the default provider.
2.2 Built-in providers are `openai` (OpenAI Responses API, key `OPENAI_API_KEY`, required), `anthropic` (Anthropic Messages API, key `ANTHROPIC_API_KEY`, required, default model `claude-opus-5`), and `ollama` (local Responses API at `http://localhost:11434/v1`, no key).
2.3 The default provider is `SCOOT_PROVIDER` when set; otherwise the first registered provider that requires a key and has one; otherwise `ollama`.
2.4 The model preference is `default` unless set: the default provider's first preferred model (`gpt-5.3-codex` for OpenAI, `llama3.2` for Ollama).
2.5 The `auto` preference routes once per turn, at the first model call, and the turn stays on that model.
Order: a configured classifier (a small model asked "simple, coding, or hard?", mapped to a tier), then the rules in `~/.config/scoot/router.json` or `SCOOT_ROUTER` (first match wins; conditions `complex`, `has_images`, `needs_tools`, `est_tokens_over`, `prompt_matches`; a rule naming a provider without a key is skipped; an unknown condition never matches), then the file's `default`, then the built-in heuristic over the live model list: a strong coding model for multi-step or editing prompts, a cheaper one for short questions, never a dated snapshot id when a plain id exists, falling back to the `default` resolution when the list is empty.
An invalid rules file is reported by `/route` and the built-in heuristic applies.
2.5a `/route` shows the rule source, the rules, the classifier, and the last decision with its reason; `/status` shows tokens per model when more than one model was used or `auto` is on.
2.6 `scoot models` lists models from every configured provider with qualified ids, grouped by provider, marking the default; providers that fail to answer are reported, not fatal.
`--provider NAME` restricts the list; `--json` returns `{"models": [...], "errors": {...}}`.
2.7 Each provider's base URL can be overridden with `SCOOT_<PROVIDER>_BASE_URL`.
2.8 Requests to `openai` carry `reasoning.effort` from `SCOOT_EFFORT` (`low` | `medium` | `high` | `xhigh`, default `medium`) for reasoning models, `store: false`, and no `temperature`; requests to `ollama` carry `temperature` when given and no `reasoning`.
2.8a Requests to `anthropic` carry `x-api-key` and `anthropic-version` headers, a required `max_tokens` (32000 unless the caller sets one), the system prompt as `system`, tools as `input_schema`, `tool_choice: auto`, top-level `cache_control: ephemeral`, `output_config.effort` from `SCOOT_EFFORT` (capped at `high` on 4.6 models, omitted on Haiku), `thinking: adaptive` only on models where omitting it would disable thinking (Opus 4.6/4.7/4.8, Sonnet 4.6), no sampling parameters, and on Claude Opus 5 and Fable `fallbacks: "default"` with its beta header unless `SCOOT_ANTHROPIC_FALLBACKS=0`.
2.8b All `tool` results of one step are sent to Anthropic in a single user message; a `stop_reason` of `refusal` becomes an error with a hint rather than an empty answer.
2.9 Output items a provider returns (for example encrypted reasoning) are stored on the assistant message, tagged with the producing model, and replayed verbatim only when the same model is called again; another model receives a rebuilt message without them.
2.10 A model call that fails with a transient error (network, timeout, 429, 5xx) is retried up to three times with capped, jittered backoff, never after streamed text has reached the screen; a 401 fails immediately with a hint naming the key variable and `scoot auth`.
2.11 Usage is normalized to `prompt_tokens`, `completion_tokens`, and `total_tokens` whatever the provider calls them.

## 3. Authentication

3.1 API keys are looked up per provider in this order: the provider's environment variables, then `~/.config/scoot/credentials.json`.
3.2 `scoot auth` (and `/auth`) lists every provider with its key source (`env:NAME`, `saved`, `no key`, or `no key needed`), marks the default, and shows the base URL.
3.3 `scoot auth set <provider>` reads a key with hidden input, validates it by listing the provider's models, and only then saves it with directory mode `0700` and file mode `0600`.
3.4 `scoot auth clear <provider>` forgets the saved key and says so when a key from the environment still applies.
3.5 Keys are never printed; key-shaped strings (`sk-…`, GitHub tokens, Bearer values, JWTs) are redacted from all output, including error messages and saved sessions.
3.6 When the default provider requires a key and none is found, the REPL still opens and prints one hint line.

## 4. Configuration

4.1 Precedence, highest first: CLI flag, environment variable, project `.env`, global `~/.config/scoot/.env`, default.
4.2 The project `.env` is the nearest `.env` found walking up from the current directory; the global file honours `XDG_CONFIG_HOME`.
4.3 Only keys named `SCOOT_*`, the providers' key variables, and `HTTPS_PROXY` / `NO_PROXY` are imported from a `.env` file; other keys are ignored.
4.4 `.env` lines may carry an `export` prefix and single or double quotes; a variable already set in the environment is never overridden.
4.5 `/status` shows which `.env` files were read.
4.6 Settings and their variables: provider `SCOOT_PROVIDER`, model `SCOOT_MODEL`, effort `SCOOT_EFFORT`, approval `SCOOT_APPROVAL`, steps `SCOOT_MAX_STEPS`, compaction threshold `SCOOT_COMPACT_AT`, timeout `SCOOT_TIMEOUT`, root `SCOOT_ROOT`, streaming `SCOOT_STREAM`, panel `SCOOT_PANEL`, dock `SCOOT_DOCK`, resume policy `SCOOT_RESUME`, workspace map `SCOOT_WORKSPACE_CONTEXT`, labels `SCOOT_LABELS`, verbosity `SCOOT_VERBOSITY`, images `SCOOT_IMAGES`, vision model `SCOOT_VISION_MODEL`, image size cap `SCOOT_IMAGE_MAX_BYTES`, logo `SCOOT_LOGO`, emoji label `SCOOT_EMOJI`, state directory `SCOOT_STATE_DIR`, config directory `SCOOT_CONFIG_DIR`.
4.7 A model chosen with `/model` and the `/logo on|off` choice persist in `~/.config/scoot/preferences.json`; an explicit flag or variable overrides them for that run.

## 5. The agent loop

5.1 One user turn runs up to `SCOOT_MAX_STEPS` model calls (default 50).
Each call receives the system prompt, the conversation, and the schemas of all registered tools.
5.2 If the reply carries tool calls, each is approved (see §7) and executed, its result appended as a `tool` message, and the loop continues; a reply without tool calls ends the turn.
5.3 A trailing `DONE` sentinel in the final answer is stripped before display.
5.4 On reaching the step limit the REPL asks whether to continue for another batch; one-shot mode stops.
5.5 A context-length error triggers one automatic compaction (§9) and a retry; a model-unavailable error switches to another model once and retries.
5.6 Streaming is on by default: tokens print as they arrive, the turn stays interruptible, and `--no-stream` or `--json` disables it.
5.7 A compact workspace map (git branch and dirty count, bounded file tree) is injected into the system prompt each turn unless `--no-workspace`.
5.8 An `AGENTS.md` at the workspace root is injected into the system prompt when present; `/init` generates one.
5.9 Pressing ESC during a turn cancels the in-flight request or tool at once, prints an interruption notice, and keeps the conversation.

## 6. Tools

6.1 Built-in tools: `read_file`, `list_dir`, `search`, `write_file`, `edit_file`, `run_shell`, `open_editor`, `update_plan`.
6.2 Every path is resolved inside the workspace root; an escape attempt is rejected.
6.3 `search` groups matches per file with bounded output; `edit_file` applies an exact replacement and reports a diff; `write_file` reports the diff against any existing content.
6.4 `run_shell` executes with a timeout and captures bounded output; its child runs with stdin closed and pagers and interactive prompts disabled (`GIT_PAGER=cat`, `GIT_TERMINAL_PROMPT=0`, and similar), so a command that would wait for input fails fast instead of hanging; commands matching the denylist (recursive deletes of root paths, force pushes, piping downloads to a shell, disk formatting, and similar) require confirmation in every approval mode.
6.4a `open_editor` opens a workspace file in an external editor, `idea -e` (IntelliJ LightEdit) by default or VS Code, as a detached process; the editor comes from the call, else `SCOOT_EDITOR`; it is approval-gated like `run_shell` and fails with an install hint when the launcher is missing.
6.5 `update_plan` records a step checklist that the UI renders and the status bar counts; it never prompts.
6.6 A tool is a drop-in module in `tools/` that calls `register`; the registry feeds both the API tool schemas and the tool list in the system prompt.

## 7. Approvals

7.1 Modes: `always` prompts for every call; `auto-read` auto-approves read-only tools; `auto-edits` also auto-approves file writes and edits; `yolo` auto-approves everything.
The default is `yolo`.
7.2 At a prompt the choices are approve once, trust this tool for the session, approve everything this session (switches to `yolo`), edit the arguments, skip this call, or abort the turn.
7.3 Denylisted shell commands are confirmed regardless of mode or trust.
7.4 `--yes` / `-y` runs a one-shot turn with everything auto-approved.
7.5 `/worktree start` creates a throwaway git worktree on a `scoot/<timestamp>` branch and points the tools there; `/worktree merge` brings the result back, `/worktree discard` drops it.

## 8. The REPL

8.1 The banner shows the mascot with the version, active model, workspace root, a resume hint or the resumed session, and key hints; `--no-logo` shows a plain box.
8.2 Input is a fixed bottom dock with full line editing, history recall, bracketed paste, growth up to six rows for long lines, and Tab completion of slash commands; without a TTY, a plain prompt is used.
8.2a The spinner shows the elapsed time after three seconds and, after thirty, a reminder that Ctrl-C forces a stop.
8.3 The bottom status bar shows the mascot face (eyes: `o o` idle, `> >` thinking, `- -` stopped), provider identity, folder and session id, model, approval mode, context size and its share of the compaction threshold, cumulative tokens, message count, worktree branch, plan progress, and the last error.
8.4 Turns are labelled `❯` for the user and `🛴 scoot` for the assistant (`⏺ scoot` with `--no-emoji`); the assistant label prints once per turn.
8.5 `/verbosity full|compact|quiet` controls whether reasoning narration and tool lines stay in the feed.
8.6 `/c` or Ctrl-S copies the last answer to the clipboard through `pbcopy`, `wl-copy`, `xclip`, `xsel`, or an OSC-52 escape.
8.7 Slash commands: `/help`, `/exit`, `/reset`, `/save`, `/status`, `/model`, `/init`, `/compact`, `/approve`, `/yolo`, `/worktree`, `/auth`, `/sessions`, `/resume`, `/forget`, `/panel`, `/verbosity`, `/c`, `/logo`.
Each is a drop-in module in `commands/`.

## 9. Context and sessions

9.1 When the context estimate passes `SCOOT_COMPACT_AT` (default 100000 tokens), or on `/compact`, the conversation is summarized by the model and replaced by the summary.
9.2 Every turn auto-saves the session to `~/.local/state/scoot/sessions/<id>.json` (mode `0600`), keyed by workspace root, with secrets redacted; the 20 most recent sessions are kept.
9.3 `scoot --continue` resumes the latest session for the directory, `scoot --resume <id>` a specific one; `SCOOT_RESUME` is `auto` (reload the latest on launch, the default), `hint` (show it in the banner), or `off`.
9.4 `/sessions` lists, `/resume [id]` loads, `/forget <id>|all` deletes.

## 10. Images

10.1 A file path to an image in the prompt (plain, quoted, escaped, or `file://`) is detected, base64-encoded (up to `SCOOT_IMAGE_MAX_BYTES`, default 4 MiB), and shown as an `[Image N]` badge in the echo.
10.2 Each image is described in its own call by `SCOOT_VISION_MODEL`, or, when `auto`, by the best vision-capable model among those the providers list, falling back to the session's model.
10.3 The descriptions are folded into the user's turn as labelled text; on failure a short note replaces the paths, and a user interrupt aborts the turn.

## 11. Errors

11.1 Every failure is a typed error with an optional one-line hint; the REPL prints `⚠ message` plus the hint and stays alive with the prompt preserved.
11.2 HTTP status mapping: 401 authentication, 402/403 quota or permission, 429 rate limit (retried), 5xx server (retried), context-length and model-unavailable detected from the error body, anything else an API error carrying the provider's message.
11.3 Transport errors distinguish DNS failure, connection failure, timeout, TLS failure, and proxy refusal, each with a hint.
11.4 `HTTPS_PROXY` (with optional Basic credentials) is honoured for hosted providers; localhost and `NO_PROXY` hosts always connect directly.

## 12. Output and exit codes

12.1 One-shot mode prints the final answer and exits 0 on success, 1 on error; `--json` prints `{status, model, steps, content, error, usage}`.
12.2 `--verbose` adds the model, step count, and token usage on stderr.
12.3 Ctrl-C quits with exit code 130.
