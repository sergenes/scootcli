# Changelog

All notable changes to `scoot`. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).
Versions before 0.1.0 were internal builds of the tool's predecessor, renumbered `0.0.N` here and trimmed to what still describes the public tool.
What comes next lives in [`ROADMAP.md`](./ROADMAP.md); the behaviour spec in [`SPEC.md`](SPEC.md); design notes in [`DESIGN.md`](./DESIGN.md).

## [0.10.0] — trust boundaries (2026-09-08)
The second release from the outside review of 0.8.1: the findings that change what a repository can make scoot do. The `yolo` default stays.
- **A cloned repository cannot reconfigure you (R02).** A project `.env` could set any `SCOOT_*` variable, including a provider base URL, which sent your own API key to a host of the repository's choosing on the first request, in every approval mode. The project layer now has a narrower allowlist: it may choose the model, effort, and terminal settings, but not base URLs, proxies, the approval mode, the scope, the root, hooks, or scoot's config and state directories. Those keys are ignored from a project file and named in a notice at start; your global `~/.config/scoot/.env` and the environment still set them. **Breaking** for anyone who kept a base URL or a proxy in a project `.env`: move it to the global file.
- **Project hooks need your trust (R01).** `.scoot/hooks.json` ran shell commands at session start in an unfamiliar checkout, before any approval. It now runs only after you review it and run `/hooks trust`; the trust is a digest of the file, so an edited file asks again, and `/hooks untrust` withdraws it. `/hooks` says whether the project file is trusted, and an untrusted one is named at start (REPL, one-shot, and a headless `notice`). A `deny` from any hook now wins over an `allow` from another (an early project `allow` used to hide a global `deny`), a project hook's `allow` no longer waives the denylist confirmation (a hook in your own global config still does, so an editor or phone bridge that approves on your behalf keeps working), and provider API key variables are removed from every hook's environment, as the module always claimed.
- **The README says what is true (R03).** The security section no longer claims shell tools are sandboxed: file tools are scoped, `run_shell` runs as you, and the denylist is an accident guard. The denylist now recognises `rm -r -f`, `rm --recursive --force`, `git -C repo push`, and `git --git-dir=x push`.
- **Search cannot read outside the workspace (R07).** Without ripgrep, the fallback followed symlinks and scanned hidden files, so a link inside a repository disclosed an external file through an auto-approved tool, and `.env` was searchable. It now walks like ripgrep does by default: no hidden files or directories, no symlinks, regular files only. The workspace map skips symlinks too.
- **Redaction reaches everything it can (R13).** Saved sessions masked key-shaped strings only in top-level message text; tool arguments, list-shaped content, and the text copies inside provider replay items kept them. Redaction is now recursive, and stops only at signed or encrypted replay fields, which the provider rejects when changed. The README states that limit and that streamed output is not filtered.
- **Headless answers name their request (R20).** An `approve` message without an `id`, or with another request's id, used to be accepted, so a queued answer could approve whatever came next. It is now a protocol error and ignored.
- **`[A]` works (R20).** The approval and scope menus advertised uppercase `A` for the session-wide choice, but every keypress was lowercased, so `A` meant "once". `A` now keeps its case; every other key is still case-insensitive.
- **`--root` decides which `.env` applies.** The project `.env` was searched from the launch directory, then the root was switched, so `scoot --root ~/other` carried the launch directory's settings into the other project. The search now starts at the root.
- **Bounded reads (R15).** `read_file` refuses FIFOs and devices, which would block, and reads the cap plus one byte instead of the whole file; an oversized image is rejected by size before it is read.
- **Session ids are file names (R17).** `/forget ../x` could delete a JSON file next to the sessions directory. Ids are validated as basenames at every entry point, a malformed saved record is skipped when listing instead of breaking startup, and records are shape-checked on load.

## [0.9.0] — nothing gets lost (2026-09-07)
A reliability release, from an outside review of 0.8.1; the R-numbers are the review's finding ids.
- **A worktree never loses work (R05).** `/worktree keep` and `/worktree merge` used to read a failed commit (a rejected hook, a missing identity) as "no changes" and force-remove the worktree with the files in it. `finish` now tells a clean tree, a committed one, and a failed commit apart, retains the worktree and the branch on any Git failure, and keeps the session in it until you fix it or `discard`. Work the agent already committed on the branch is merged too, measured against the recorded base commit.
- **Entering a worktree moves the scope (R06).** The file scope and the hooks now follow the root, so a worktree nested under the original checkout no longer leaves the original in scope; path grants for the old root are dropped.
- **Writes that cannot destroy a file (R08).** `write_file` without a string `content` is an error, not an emptied file (an explicit `""` still empties it). `edit_file` refuses a file that is not UTF-8 instead of silently replacing bytes, keeps CRLF files CRLF while letting the model quote them with plain newlines, and refuses to overwrite a file that changed after it was read. Both tools write through a temp file and an atomic rename that preserves the mode, so a failed write leaves the old file intact.
- **Failed commands explain themselves (R09).** A failing test or build used to reach the model as `ERROR: exit 1`; the captured output now follows the error line, so the model fixes the code instead of rerunning the command.
- **Resume keeps your approval mode (R04).** Resuming a session that once ran in `yolo` no longer turns `--approval always` into `yolo`; the conversation is restored, the permission policy is this run's.
- **Every tool call gets a result (R11).** Aborting or interrupting a batch answers every remaining call with a "not executed" result, and loading a saved session fills in any missing one, so the next request is never rejected for an unanswered tool call. Arguments that are not a JSON object never run a tool with empty arguments.
- **A cut-off reply is not an answer (R12).** A reply that hit the model's output limit, or a stream that closed before its terminal event, ends the turn as `incomplete`: the partial text is shown with a warning, one-shot exits 1, headless emits `status: incomplete`. Tool calls in such a reply are not executed and the retry is bounded. A 200 stream with no events, or with unreadable events, is an error rather than an empty success.
- **Stop means stop (R10).** Shell commands and hooks run in their own process group, which ESC and the timeout terminate as a whole, with bounded waits and a monotonic deadline; a background grandchild no longer keeps the call blocked. The ESC watcher shuts the socket down before closing it and closes the response, so a stalled read wakes on every platform. Hooks honour the cancel event they were always handed.
- **Streaming (R14).** Short SSE events are delivered as they arrive instead of waiting for a 1 KB buffer, and a character split across two reads is decoded whole.
- **Zipapp (R19).** `scoot.pyz` now exits with `main()`'s status (a handled error used to exit 0) and contains the package only; CI checks both.
- **A blueprint.** `docs/blueprint.md` is the minimal, self-contained spec for building a CLI agent like scoot: components and their contracts, the turn, the tools, the permission rules, the build order, and the acceptance checks.

## [0.8.1] — a model per folder (2026-09-07)
- **A model per folder.** `/model X` and `scoot --model X` now remember the choice for the folder you are in, so each project can start on its own model; `/model X everywhere` sets the model for every folder without a choice of its own, `/model forget` drops the current folder's choice, `/model` lists both saved layers, and `/status` says where the active model came from (`--model`, `SCOOT_MODEL`, this folder, every folder, or the default). The choice saved by 0.8.0 keeps working as the "every folder" layer; `--root` picks up the target folder's model.

## [0.8.0] — a remembered --model, and a dock that survives a resize (2026-09-07)
- **`--model` is remembered.** Starting the REPL with `scoot --model provider/model` now saves the choice like `/model` does, so a plain `scoot` in that folder next time starts on the same model instead of the default. One-shot prompts and headless runs still leave the saved preference alone.
- **Resize without losing the dock.** Zooming the font or resizing the window (tmux included) used to drop the status bar and the mascot and leave the input on the wrong row until the next keystroke, with a stale copy behind. scoot now handles `SIGWINCH`: the editor wakes up at once, asks the terminal where the cursor went, scrolls the transcript's tail up if the smaller screen swallowed it, and redraws region, bar, and input in one write; a resize during a running turn is repaired the same way. Works for shrink, grow (tmux pulling history back), width-only changes, and wrapped multi-row input.

## [0.7.0] — hooks that speak Claude Code's dialect, cost in machine output (2026-09-06)
- **Hook matchers understand Claude Code tool names** (`Bash`, `Write`, `Edit`, `Read`, `Grep`, `LS`, `TodoWrite`) next to scoot's own, and payloads carry `tool_alias`, so one `hooks.json` and one script can serve both tools.
- **Cost in machine output:** `turn_end` in headless mode carries `cost` (this turn) and `cost_session`; `--json` one-shot output carries `cost`. `null` when a model's price is unknown.
- **Hooks accept Claude Code's exact output.** A PreToolUse hook answering with the nested `hookSpecificOutput` form (`permissionDecision`, `permissionDecisionReason`, `additionalContext`, `approve` as a synonym for `allow`) now works unchanged; before, only scoot's flat top-level shape was read and such a hook's decision was silently ignored. The 0.4.0 claim of Claude Code-shaped decisions was only true for the input payloads until this fix.

## [0.6.0] — costs, update check, mid-turn notes (2026-09-06)
- **Cost in `/status`:** tokens, cached tokens, calls, and USD per model plus a session total, from list prices in `pricing.py` (dated; unknown models show `?`, local models are free); `--verbose` one-shot output includes the cost.
- **Update check, opt-in:** `scoot --check-update` asks PyPI once; `SCOOT_UPDATE_CHECK=1` makes the REPL check in the background at start and show `⬆ x.y.z` in the bar and `/status`.
- **Ctrl-N note:** press Ctrl-N during a turn to add a one-line note at the next model call (the REPL counterpart of headless `note`).
- **Router:** `/route` warns when the classifier is a local model; the README example uses a hosted mini model.
- **`install.sh --uninstall`.**

## [0.5.0] — the workspace is not a wall (2026-09-06)
- **The workspace is no longer a wall.** A file tool that needs a path outside the workspace asks once: allow this path, allow its directory for the session, allow anywhere for the session, skip, or abort. `--scope anywhere` / `SCOOT_SCOPE` / `/scope anywhere` skip the question, `--yes` implies it, `/scope` shows and changes grants, `/status` reports them; headless mode gets a `scope_request` event. `~` expands in paths. The system prompt tells the model to use absolute paths elsewhere instead of refusing or handing the user a script. Found on a real run where scoot wrote a script for the user to run because it believed it could not touch `~/.config`.

## [0.4.0] — hooks, headless mode, a first run that explains itself (2026-09-06)
- **Hooks:** shell commands on `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `Notification`, and `SessionEnd`, configured in `~/.config/scoot/hooks.json` and `.scoot/hooks.json` with Claude Code-shaped payloads and decisions (allow, deny, ask, block, context, exit 2). `/hooks` lists them; `SCOOT_HOOKS=0` disables.
- **Headless mode:** `scoot --headless` speaks line-delimited JSON on stdin and stdout (prompts, approvals, notes, interrupts, commands in; streamed text, tool calls, approval requests, results, plans, usage, errors, heartbeat out). Protocol version 1, documented in `docs/headless-protocol.md`; `SCOOT_APPROVAL_TIMEOUT` bounds unanswered approvals.
- Notes sent while a turn runs are delivered to the model at its next call.
- **First run, fixed from a real Linux install:** the setup guidance is now part of the REPL banner (it used to print before the screen clear and vanish); the status bar and banner say "not set up" instead of showing an unconfigured provider and model; readiness is re-checked after `/auth set|clear` and `/model`, so the bar flips to the real provider as soon as a key is saved; the "connection refused" hint for a local server names both hosted providers when no key exists, or points at `/model` when one does; readiness follows the active model's provider, not only the default.
- **Install docs:** the quick start is `pipx install scootcli && pipx ensurepath` with the pipx package hint for Debian, Ubuntu, Pop!_OS, and macOS; `install.sh` prints the PATH line for the user's shell (bash, zsh, fish) and notes that a new login shell picks `~/.local/bin` up on Linux.

## [0.3.0] — routing for auto, curl install, a friendlier first run (2026-09-06)
- **Routing for `auto`:** opt-in per-turn model choice with rules from `~/.config/scoot/router.json` (conditions on complexity, images, size, prompt regex; first match wins; providers without a key are skipped), an optional small-model classifier with tier mapping, and the old heuristic as the built-in default. Routed once per turn. `/route` explains the decision; `/status` shows tokens per model.
- **Nothing-configured state:** readiness checks with setup guidance in `auth`, `models`, one-shot, and the REPL; a dead local server fails fast with a hint instead of retrying; turns without a reply are not saved.
- **Install script:** `curl -fsSL https://raw.githubusercontent.com/sergenes/scootcli/main/install.sh | bash` installs the latest release's zipapp as `~/.local/bin/scoot`; `SCOOT_VERSION` pins, `SCOOT_INSTALL_DIR` relocates. CI runs it against the live release on Linux and macOS.

## [0.2.0] — Anthropic, open_editor, a shell that never waits (2026-09-06)
- **Anthropic provider:** `anthropic/claude-opus-5` and the rest of the Claude family through the Messages API, on the same provider layer: thinking blocks replay to the producing model, `SCOOT_EFFORT` maps to `output_config.effort`, tool results of a step travel in one message, refusals surface as errors, prompt caching is on, and Claude Opus 5 requests the server-side refusal fallback (`SCOOT_ANTHROPIC_FALLBACKS=0` to disable).
- **README badges.**
- **`open_editor` tool:** open a workspace file in IntelliJ IDEA (`idea -e`, LightEdit) or VS Code as a detached process; `SCOOT_EDITOR` sets the default, the model may pick per call; approval-gated like `run_shell`.
- **`run_shell` never waits for input:** the child runs with stdin closed and pagers/prompts disabled (`GIT_PAGER=cat`, `GIT_TERMINAL_PROMPT=0`, …), so `git log` or an interactive installer fails fast instead of hanging the turn.
- **Spinner timer:** the activity spinner shows elapsed time after 3 s and a "ctrl+c to force" reminder after 30 s.

## [0.1.0] — first public release (2026-09)

The tool went public under a new name and on official APIs.

- **Name:** `scoot`, PyPI package `scootcli`, with a mascot (banner, `/help`, status-bar face whose eyes follow the turn; `--no-logo`, `/logo on|off`).
- **Providers:** a provider layer (`providers/`) behind one `Provider` protocol. OpenAI through the Responses API (default model `gpt-5.3-codex`, `SCOOT_EFFORT` for reasoning effort) and a local Ollama through the same wire; models are `provider/model`; a `ProviderPool` dispatches by prefix; `scoot models` lists per provider. Provider output items such as encrypted reasoning are stored on the assistant message and replayed only to the producing model.
- **Auth:** plain API keys, from the environment or `scoot auth set <provider>` (validated, saved `0600`); `scoot auth` shows every provider's key source; `scoot auth clear` forgets one. Ollama needs no key.
- **Configuration:** `.env` replaces `env.env`: `~/.config/scoot/.env` plus the nearest project `.env`, importing only `SCOOT_*`, provider key variables, and `HTTPS_PROXY`/`NO_PROXY`; every setting is namespaced `SCOOT_*`; `/status` shows the env files read. Localhost and `NO_PROXY` hosts bypass the proxy.
- **Removed:** GitHub Copilot support (no public chat API), the Kerberos/`curl`/SSL-bypass corporate machinery, the JetBrains identity, the Jira tools.
- **Packaging:** MIT license, `pyproject.toml` metadata with the version in `scootcli/__init__.py`, `scoot --version`, wheel + sdist + zipapp from `scripts/build.sh`.
- **Docs:** README, SPEC, DESIGN, ROADMAP, and AGENTS rewritten for the public tool.

Also since 0.0.19:
- **Tab-completion of slash commands:** in the REPL input, type `/` (or a prefix like `/ve`) and press
  **Tab** to complete the command name; repeated Tab **cycles** through the alphabetical matches
  (wrapping around). Any other key ends the cycle. Powered by a pure `cycle_completion` state helper
  (unit-tested) fed by the live command registry.
- **`/verbosity` command:** regulate how much of a turn's "thinking" shows in the feed —
  `full` (default; reasoning + every tool call kept as a run log), `compact` (reasoning shown, each
  tool call collapses to a single transient line above the `Thinking…` spinner, removed when done), or
  `quiet` (reasoning hidden, tool calls collapsed). Also settable via `SCOOT_VERBOSITY`; shown in
  `/status` and documented in `/help` + README/SPEC.
- **REPL/session UX:** default launch now **auto-resumes** the latest session for the directory
  (`/reset` to start clean; `SCOOT_RESUME=hint|off` to opt out), and a resumed session now **replays
  its full transcript** on screen and **seeds ↑-key history** from prior prompts.
- **Status bar tokens** now show both the current context size and the cumulative session total
  (`▤ ctx N · Σ M tok`); `ctx` is estimated from restored messages before the first API call.
- **Status bar context %:** the context segment now shows its share of the auto-compact threshold
  (`ctx N (P%)`) and flips to a `△` warning glyph at ≥80% so you can `/compact` before it auto-fires.
- **Status bar message count:** the bar now shows the conversation size (`✉ N`).
- **Status bar errors:** a failed turn now shows a `⚠ <ErrorType>` segment in the bar (cleared when the
  next turn starts), mirroring the existing `◇ done/total` plan counter. README documents the full
  status-bar segment legend.
- **Single turn label:** the `⏺ scoot` lead-in now prints **once per turn** — intermediate narration
  and the final answer print underneath it as one block, instead of repeating the gutter per message.
- **Visible tool-in-use:** the auto-approved tool line is now pinned on its own row *above* the
  `running…` spinner for the whole call (previously it was drawn then instantly overwritten by the
  spinner/`thinking…`); the tool row is kept on screen (scrolls into the feed as a log of what ran)
  while only the spinner row is cleared, so fast tools no longer flash past before you can read them.
- **Auto-compact threshold** raised `24k → 100k` tokens (`SCOOT_COMPACT_AT`).
- **Default approval mode** is now `yolo` (`SCOOT_APPROVAL`); the catastrophic-command denylist
  still confirms destructive shell commands.
- **Input editor:** ↓ on a draft line clears it (keeping the text stashed); ↑ restores it.

## [0.0.19] — Multi-row growing input dock (M23)
- The fixed bottom input line is no longer stuck at one row: a long line now **wraps onto extra rows and
  the input area grows upward** (up to 6 rows, then it scrolls vertically to keep the caret visible) and
  **shrinks back** as you delete. A shared `DockLayout` keeps the status bar and line editor agreed on
  geometry with a dynamic input height, so the scroll region shrinks/grows in step and the input never
  overwrites the bar. The wrap math is a pure, unit-tested `wrap_layout()`; the bar gains a `reflow()`
  that resizes the region mid-edit **without touching the editor's cursor save-slot**.
- Input framing uses **blank pad rows** above/below the input (not drawn `─` rules): earlier a drawn rule
  looked wrong on terminal resize and — because a grown dock's old rule row wasn't cleared on submit —
  left `─` artifacts that scrolled up into the transcript on every Enter. Blanks are resize-proof, and
  the whole grown band is now cleared on collapse so nothing lingers. **189 tests.**

## [0.0.18.5] — Fix: clear the input row on submit
- The fixed bottom input line is now cleared when you press Enter, so the submitted text (e.g. a long
  pasted path) no longer lingers on the input row while the turn runs. **183 tests.**

## [0.0.18.4] — Fix: spurious ESC-interrupt + safe image fallback (M22 follow-up)
- The ESC-interrupt listener no longer fires on **escape sequences** (arrow keys, mouse, or leftover
  bracketed-paste markers from a drag-and-drop) — it now only interrupts on a **bare ESC**. This was
  cancelling the vision-describe pass mid-run.
- When image describe fails or is skipped, the raw file paths are **no longer leaked** into the
  conversation (they made the agent think you'd asked it to read unreadable files). A short
  "N image(s) couldn't be read" note is folded in instead; a real interrupt aborts the turn cleanly
  without appending a half-processed prompt. **183 tests.**

## [0.0.18.3] — Fix: multi-image describe (M22 follow-up)
- Pasting/dropping **multiple** images now describes **each one in its own vision call** and folds a
  clearly-labelled section per image (`[Image N — name]`) into context — previously a single shared
  call merged or dropped images, so the agent only "saw" one. **182 tests.**

## [0.0.18.2] — Image path badges in the REPL echo (M22 polish)
- The dock echo now shows a compact `[Image 1]`, `[Image 2]`, … badge in place of each long dropped
  image path (via `images.badge_text`); the real path is still detected, encoded, and described. Normal
  prompts are echoed verbatim. **181 tests.**

## [0.0.18.1] — Fix: drag-and-drop image paths (M22 follow-up)
- A dropped **absolute path** (`/Users/…/shot.png …`) was parsed as a slash-command (`unknown command`).
  Slash-command routing now only fires for `/name`-shaped input, so paths are treated as prompts.
- Image detection rewritten to anchor on the extension and expand to whatever **exists on disk**, so
  filenames with spaces — escaped *or* not (e.g. OneDrive screenshots with inconsistent escaping) —
  quotes, and `file://` URLs all resolve. **178 tests.**

## [0.0.18] — Drag-and-drop image attachments (M22)
- Drag one or more images into the prompt and `scoot` describes them with a vision model, folding the
  description into the turn as text — so even a non-vision coding model can "see" a screenshot/diagram.
- New `images.py`: detects image paths in a prompt (plain / backslash-escaped / quoted / `file://`),
  then base64-encodes to a `data:` URI with a MIME sniff + size cap.
- New `vision.py`: a **swappable** `VisionProvider` seam (stdlib `Protocol` + neutral `VisionResult`),
  a default vision provider reusing the chat client, and a `make_vision_provider(config, client)` factory
  — mirroring the `make_transport`/`ChatResult` pattern. Swap the model via config, the backend via a
  new class. `models.py` gains `is_vision_model` / `VISION_TIER` / `resolve_vision`.
- Wiring is best-effort and **non-destructive**: no images (or `--no-images`) ⇒ the turn is byte-for-byte
  unchanged; any encode/vision failure returns the original prompt. Config `images` / `vision_model` /
  `vision_provider` / `image_max_bytes`; flags `--no-images`, `--vision-model`, and `models --vision`.
- Vision confirmed against the Business API (same endpoint/headers) via the reference script
  **175 tests.**

## [0.0.17] — Clipboard copy + prompt/response labels (M21)
- Copy the last assistant answer to the system clipboard with **`/c`** (alias `/copy`) or **Ctrl-S**
  ("save") in the input dock. (⌘C can't be captured — terminal emulators keep the ⌘ modifier.)
- New stdlib `clipboard.py`: tries `pbcopy`/`wl-copy`/`xclip`/`xsel`/`clip.exe`, then an OSC-52 escape
  (SSH-friendly). Degrades to a friendly note when no method is available.
- Transcript now distinguishes turns: bold green `❯` gutter for user prompts, a `⏺ scoot` lead-in for
  the assistant's final answer (streamed + buffered). Toggle with `--no-labels` / `SCOOT_LABELS`
  (TTY-only, so piped/one-shot output stays clean). Banner + `/help` advertise the copy hotkey. **150 tests.**

## [0.0.16] — Documentation refresh + hardening (M20)
- Docs rewritten to be scannable (README highlights, tool-run transcript, repo-map + progress-plan sections).
- Hardening: `rendering.redact` masks the whole non-whitespace run for `Bearer …`/`tid=…`/bare `eyJ…` JWTs.
- Hardening: `CurlTransport.stream_request` tears down curl in a `finally` so abandoning the SSE generator
  early can't orphan a curl process. **137 tests.**

## [0.0.15] — Approval-mode ladder + one-shot `--yes` (M19)
- 4-tier ladder `always → auto-read → auto-edits → yolo`; `auto-edits` now truly auto-approves edits.
- New `--yes`/`-y` auto-approves all tool calls for one run; `/approve` + `--approval` share `MODES`. **135 tests.**

## [0.0.14] — Workspace context injection (M18)
- `workspace.py` injects a compact, bounded repo map (git summary + file tree) into the prompt each turn.
- `--no-workspace` / `SCOOT_WORKSPACE_CONTEXT`. **133 tests.**

## [0.0.13] — `update_plan` tool + progress UI (M17)
- Side-effect-free meta tool publishes a self-updating step plan; rendered as a checkbox list, a
  `◇ done/total` status-bar segment, and in `/status`. **126 tests.**

## [0.0.12] — Opt-in resume policy
- `SCOOT_RESUME` = `hint` (default) · `auto` · `off`; `--resume-last`. `/status` shows transport + resume.
- **121 tests.**

## [0.0.11] — Native (stdlib) transport, `curl` optional (M16)
- `NativeTransport` (`http.client`/`socket`/`ssl`) with the same contract as the curl transport; a Kerberos module
  SPNEGO via GSSAPI/ctypes; `make_transport` + `--transport`/`SCOOT_TRANSPORT` (`auto`/`native`/`curl`).
- Housekeeping: removed the stale `build/` tree. **117 tests.**

## [0.0.10] — Bracketed paste in the input dock (M14c)
- Paste enters `ESC[?2004h` mode; multi-line paste is gathered as one event and flattened to a single line.
- **104 tests.**

## [0.0.9] — Full line editing in the input dock (M14b)
- ←/→, Home/End, Ctrl-A/E/B/F/U/K/W, forward Delete, ↑/↓ history, horizontal scroll; raw `os.read` +
  CSI/SS3 escape parser. **101 tests.**

## [0.0.8] — Fixed-bottom input dock, MVP (M14a)
- `lineeditor.py` pure `apply_key` state machine + raw-mode `readline`; `StatusBar` reserves the bottom
  rows so output scrolls above a pinned prompt; `--no-dock` / `SCOOT_DOCK`. **92 tests.**

## [0.0.7] — Model persistence (M15)
- The model chosen via `/model` is saved to `~/.config/scoot/preferences.json` and reused next launch;
  `--model`/`MODEL` still override. **81 tests.**

## [0.0.6] — Search output grouping (M13)
- `search` groups matches by file with counts + sample lines; `files_only`; big-repo context cut ~4–30×.
  **76 tests.**

## [0.0.5] — Approval batching + long-request timeout fix (M12)
- `[t]` trust-tool-for-session and `[A]` approve-all; streaming uses stall detection instead of a hard
  `--max-time` (fixes premature `curl (28)`); default timeout 30→120s. **73 tests.**

## [0.0.4] — Bottom status bar + step-limit continuation (M11)
- `panel.py` pins a status bar (user · folder · model · mode · tokens) via a DECSTBM scroll region;
  step limit 25→50 with a "continue?" prompt; `--no-panel`. **69 tests.**

## [0.0.3] — Session persistence / resume (M10)
- `sessions.py` auto-saves each turn (owner-only, redacted, keyed by root); `--continue`/`--resume`,
  `/sessions`/`/resume`/`/forget`; retention 20. **61 tests.**

## [0.0.2] — Streaming responses (SSE) (M9)
- `chat_stream(on_delta)` + `_StreamAccumulator`; live tokens, ESC-interruptible, trailing-`DONE`
  suppression; `--no-stream`. **53 tests.**

## [0.0.1] — Initial release (M1–M8)
- Skeleton + client refactor (M1), persistent REPL + ESC-interrupt (M2), six tools + approvals (M3),
  agentic loop + intent inference (M4), context/`/compact` + `/init` + `auto` model (M5), approval modes +
  YOLO + git-worktree isolation (M5.1), error handling & resilience (M5.2), presets + packaging (M6/M7),
  and authentication/onboarding `/auth`·`/logout` (M8). **39 tests at M5.2.**

---

