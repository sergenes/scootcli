# scoot — Design & Architecture Reference

Enduring design notes for `scoot` (architecture, layout, subsystems, the agent system prompt, and
per-feature deep-dives). This is **reference material**, not a task list: what ships is specified in
[`SPEC.md`](./SPEC.md), what comes next in [`ROADMAP.md`](./ROADMAP.md), and shipped history in
[`CHANGELOG.md`](./CHANGELOG.md).

---


## 2. High-Level Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │                  scoot (CLI)               │
                    │   scoot "prompt"   |   scoot  (REPL)      │
                    │   argparse -> single agent entrypoint         │
                    │   (+ optional shortcut presets: models, ...)  │
                    └───────────────┬──────────────────────────────┘
                                    │  natural-language prompt
                                    ▼
                             ┌───────────────────────────────┐
                             │        Agent / Loop Engine     │
                             │  infer intent from prompt +    │
                             │  available tools, then:        │
                             │  plan → call model → route:    │
                             │   • tool_call → approve → run  │
                             │   • assistant text → done?     │
                             └───────────────┬───────────────┘
                                             ▼
                   ┌─────────────────────────────────────────────┐
                   │              ProviderPool                    │
                   │  provider/model → adapter (Responses, Chat)  │
                   │  API keys · retries · error mapping          │
                   └───────────────┬─────────────────────────────┘
                                   ▼
          OpenAI /v1/responses  ·  Ollama /v1/responses  ·  (Anthropic /v1/messages, 0.2.0)
```

> **Intent inference, not verb parsing.** There is only one real path: the agent. Whether the user
> wants an answer, an explanation, a new file, or an edit is decided by the *model* based on the
> prompt + the tool schemas it's offered — exactly like Claude Code / Codex / Cursor. "Commands" are
> optional sugar that pre-seed the agent, not distinct code paths.

### Layers
1. **CLI layer** — argument parsing, subcommands, config, output formatting.
2. **Agent/Loop engine** — orchestrates the conversation, tool routing, approvals, stop conditions.
3. **Tools layer** — sandboxed local actions (fs + shell + search), each with a JSON schema.
4. **Provider layer** — one `Provider` protocol, wire adapters per vendor API, a registry of provider rows, and the stdlib HTTP transport underneath.

### Design principles — modular & extensible
The whole point is that **adding a capability later should mean dropping in one small file, not
editing the core.** Everything is built around **registries + small interfaces**:

- **Plugin-style registries.** Tools, slash-commands, and presets each live in their own package with
  an `__init__.py` registry. New tool = new file exposing a `Tool` (schema + handler); it is
  auto-discovered and its schema auto-added to the API `tools` param. Same pattern for slash-commands
  and presets. **No central switch statement to edit.**
- **Stable interfaces / dependency direction.** Core engine (`agent.py`) depends only on abstract
  interfaces: `Tool`, `SlashCommand`, `LLMClient`, `Renderer`. Concrete implementations depend on the
  core, never the reverse — so we can add a provider, swap the renderer, or add tools without
  touching the loop.
- **Single source of truth.** The tool registry feeds *both* the runtime `tools` schema and the
  system-prompt `{{tool_list}}`, so they can never drift.
- **Everything is data-driven config.** Models, approval policy, limits come from `config.py`
  (§4) — no hard-coded constants scattered around.
- **Thin CLI, fat engine.** `cli.py`/`repl.py` only parse input and render; all behavior lives in the
  engine + registries, keeping surfaces (CLI, future IDE/HTTP wrapper) interchangeable.

> **Extension checklist** (documented in README): to add a tool → drop a file in `tools/`; to add a
> slash-command → drop a file in `commands/`; to add a preset → drop a file in `presets/`. That's it.

---

## 3. Project Layout

```
scootcli/
├── pyproject.toml            # packaging; version comes from scootcli/__init__.py; entrypoint `scoot`
├── README.md · SPEC.md · DESIGN.md · ROADMAP.md · CHANGELOG.md · AGENTS.md · NAME-AND-LOGO.md
├── .env.example              # every setting, commented (copy to ~/.config/scoot/.env)
├── scripts/build.sh          # dist/scoot.pyz + wheel + sdist
├── src/scootcli/
│   ├── cli.py                # argparse: bare `scoot` -> REPL; one-shot; presets; models; auth
│   ├── repl.py               # REPL loop, ReplUI, ReplSession
│   ├── agent.py              # the agentic loop (the ONE entrypoint)
│   ├── providers/            # provider layer (see §5)
│   │   ├── base.py           # Provider protocol, ChatRequest/ChatResult, ProviderSpec, retries, errors
│   │   ├── openai_responses.py  # Responses API wire (OpenAI, Ollama)
│   │   ├── openai_chat.py    # Chat Completions wire (compatible endpoints)
│   │   └── registry.py       # built-in rows, make_provider, ProviderPool
│   ├── transport.py          # stdlib HTTP/1.1 + SSE, ESC cancellation, HTTPS_PROXY
│   ├── auth.py / credentials.py  # API-key lookup (env > saved) and 0600 storage
│   ├── config.py / models.py # .env allowlist + precedence; the `auto` heuristic
│   ├── prompts.py            # system prompt assembly
│   ├── approvals.py          # approval modes, prompts, shell denylist
│   ├── logo.py               # the mascot
│   ├── keys.py · status.py · activity.py · rendering.py · lineeditor.py · panel.py
│   ├── images.py · vision.py · sessions.py · context.py · project.py · worktree.py · workspace.py · clipboard.py
│   ├── tools/                # drop-in registry: read_file, list_dir, search, write_file, edit_file, run_shell, update_plan
│   ├── commands/             # drop-in registry of slash commands
│   └── presets.py            # explain / edit
└── tests/                    # network-free pytest suite
```

---

## 4. Configuration (`config.py`)

Precedence (highest wins): **CLI flag → environment variable → project `.env` → global `~/.config/scoot/.env` → default**.

Two `.env` files are read (project first, then global; loading never overrides a set variable), and
only allowlisted keys are imported: `SCOOT_*`, the providers' key variables, `HTTPS_PROXY`/`NO_PROXY`.
That keeps a project's own secrets out of scoot's process.

| Setting        | Env var              | Default                                              |
| -------------- | -------------------- | ---------------------------------------------------- |
| Provider       | `SCOOT_PROVIDER`     | first provider with a key, else `ollama`             |
| Model          | `SCOOT_MODEL`        | `default` (the provider's preferred model); `auto` opts into the per-turn heuristic |
| Effort         | `SCOOT_EFFORT`       | `medium`                                             |
| Proxy          | `HTTPS_PROXY`        | none (localhost and `NO_PROXY` hosts never proxied)  |
| Request timeout| `SCOOT_TIMEOUT`      | `120`                                                |
| Max agent steps| `SCOOT_MAX_STEPS`    | `50`                                                 |
| Approval mode  | `SCOOT_APPROVAL`     | `yolo`                                               |
| Workspace root | `SCOOT_ROOT`         | current working directory                            |
| Base URLs      | `SCOOT_<PROVIDER>_BASE_URL` | the registry row's default                    |

Global CLI flags: `--model`, `--provider`, `--effort`, `--proxy`, `--root`, `--approval`/`--yolo`/`--yes`,
`--verbose/-v`, `--json`, `--no-stream`, `--no-panel`, `--no-dock`, `--no-workspace`, `--no-labels`,
`--no-images`, `--no-logo`, `--no-emoji`, `--continue`, `--resume`, `--resume-last`, `--version`.

---

## 5. Providers (`providers/`)

The internal conversation format is the OpenAI chat shape: `system`/`user`/`assistant`/`tool`
messages, `tool_calls` on assistant messages, `{"type": "function", ...}` tool schemas, `image_url`
parts with data URIs. Every consumer speaks it; adapters translate at the edge.

- **`Provider` protocol** (`base.py`): `chat(...)`, `chat_stream(..., on_delta)`, `list_models()`.
  `ChatRequest` carries messages, model, tools, temperature, max tokens, the cancel event, and `hints`
  (task text, images, tools needed) for a future router. `ChatResult` carries content, model,
  tool calls, finish reason, normalized usage, and the assistant message to append to history.
- **`ProviderSpec`** is a data row: name, base URL, wire, key variables, whether a key is required,
  preferred and vision model substrings, capabilities (`tools`, `streaming`, `vision`, `reasoning`,
  `temperature`). Built-ins: `openai` and `ollama`. Adding a compatible vendor is one `register(...)`.
- **Wire adapters** subclass `BaseProvider` and implement `_complete` / `_stream`: `openai_responses`
  (`POST /responses`; `instructions`, `input` items, flat function tools, `store: false`,
  `reasoning.effort` for reasoning models; SSE events `response.output_text.delta`,
  `response.output_item.done`, `response.completed`) and `openai_chat` (`POST /chat/completions`).
- **Opaque provider items.** A reply's output items (encrypted `reasoning`, later Anthropic
  `thinking`) are stored on the assistant message under `provider_items`, tagged with the producing
  `provider/model`, and replayed verbatim only to that model. Other models get a rebuilt message.
- **`ProviderPool`** holds one adapter per provider, built lazily, and dispatches by the model prefix;
  it exposes the same surface as a single provider, so consumers never know how many are in play.
  A routing provider plugs in here as just another dispatch target.
- **Auth** (`auth.py`): the provider's environment variables, then `~/.config/scoot/credentials.json`
  (`0700`/`0600`). Keys never print; `rendering.redact()` masks key-shaped strings.
- **Resilience** lives in `BaseProvider`: transient errors (network, timeout, 429, 5xx) retry three
  times with capped, jittered, interruptible backoff, never after streamed text was shown; 401 fails
  at once with a hint naming the key variable.
- **Transport** (`transport.py`): stdlib HTTP/1.1 over `socket`/`ssl`, SSE line streaming, a watcher
  thread that closes the socket on ESC, `CONNECT` through `HTTPS_PROXY` (no-auth or Basic) with
  localhost and `NO_PROXY` hosts always direct, plain HTTP for local servers.

---

## 6. Interface — Persistent REPL (prompt-first)

`scoot` is used by **running it with no flags**. It opens a persistent session: it waits for a
prompt, works on it (showing live progress), prints a summary, and returns to the prompt — looping
until you quit. The agent infers intent from each prompt (answer / explain / create / edit), so there
are no verb subcommands to remember. This mirrors Claude Code / Codex / Cursor.

### Session lifecycle
```
$ scoot
       ╭───╮      scoot 0.1.0 · openai/gpt-5.3-codex · root: ~/proj/foo
       │o o│      type a prompt · /help · esc to interrupt · ctrl-c to quit
    T──┤───┤      ⌃S or /c copies the last reply to your clipboard
    │  ╰┬─┬╯
  (o)═══╧═╧═(o)

›  add a --verbose flag to cli.py            ← you type, press Enter
   ⠋ thinking…                               ← spinner while the model responds
   ● read_file  cli.py                        ← tool call announced as it happens
     approve? [a]pprove [e]dit [s]kip [q]uit ← inline single-key approval (v1)
   ✔ read_file  (128 lines)                   ← result line
   ⠙ working…  step 2/3 · esc to stop        ← progress + interrupt hint
   ● edit_file  cli.py  (+8 −1)               ← diff shown, approval requested
     approve? [a/e/s/q] a
   ✔ edit_file  applied

   ── Summary ─────────────────────────────
   Added a --verbose flag and wired it into the logger setup.
   Changed: cli.py (+8 −1)
   ─────────────────────────────────────────

›  ▏                                          ← back to prompt, history preserved
```

### Behaviors (agreed)
- **Bare `scoot`** → banner, then wait for input. No flags required to get started.
- **While working** → a live status region shows the current activity, each tool call, a step
  counter, and a persistent `esc to stop` hint.
- **On completion** → a **model-generated prose summary** of what was done, then return to the prompt.
- **ESC while working** → **interrupt immediately**: close the in-flight model request and/or the
  running tool subprocess, print `⏹ interrupted`, and return to the prompt. **Conversation context is
  kept**, so you can redirect ("actually, revert that") on the next turn.
- **Ctrl-C** → quit the whole app. `Ctrl-D` on an empty prompt and `/exit` also quit.
- **Enter on empty prompt** → no-op (re-prompt). Multiline paste is accepted as one prompt.

### REPL slash-commands (built-in, drop-in extensible)
Typed at the prompt; handled locally by the `commands/` registry (they never hit the model unless the
command itself calls it). Adding a new one = drop a file in `commands/`.

| Command            | What it does |
| ------------------ | ------------ |
| `/init`            | **Learn the project** and write an **`AGENTS.md`**: scan the workspace (languages, structure, build/test commands, entry points, conventions), have the model summarize it, and save it to the repo root. Auto-loaded into the system prompt on future runs (see §15) so the agent has durable project context. |
| `/compact`         | **Summarize → clear → reseed.** When context grows large, ask the model to summarize the conversation so far, drop the old messages, and keep the summary as the new base context. Frees tokens without losing the thread. Can run automatically near the context limit. |
| `/status`          | Show the **provider** (and where its key came from), **model**, effort, env files read, **token usage** (session + last turn), **root**, step budget, approval mode. |
| `/model [name]`    | No arg → list available models (highlighting current). With a name → **switch model** for subsequent turns. Default selection is **`auto`** (see below). |
| `/reset`           | Clear the conversation (fresh context); keep the loaded `AGENTS.md`. |
| `/save <file>`     | Dump the transcript (and tool activity) to a file. |
| `/auth`            | List providers with their key source; `/auth set <provider>` pastes, validates, and saves a key; `/auth clear <provider>` forgets it (see §18). |
| `/logo`            | Show the mascot; `/logo on|off` toggles it and remembers the choice. |
| `/plan`            | Show the current task plan and progress. |
| `/root <dir>`      | Change the workspace root (re-sandboxes tools). |
| `/help`            | List all registered slash-commands + this help. |
| `/exit`            | Quit (same as Ctrl-C / Ctrl-D). |

#### `/init` → `AGENTS.md`
- Walks the tree (respecting `.gitignore`, skipping vendored/build dirs), samples key files
  (README, manifests like `package.json`/`pyproject.toml`/`pom.xml`/`build.gradle`, entry points),
  and asks the model to produce a concise `AGENTS.md`: project purpose, layout, how to build/test/run,
  key modules, and conventions.
- **`AGENTS.md` is auto-discovered and injected** into the system prompt on every run (the emerging
  cross-tool standard, also read by Codex and others). Editable by hand; `/init` regenerates it.
- Idempotent: prompts before overwriting an existing `AGENTS.md`.

#### `/compact` → context management (`context.py`)
- Triggered manually, or automatically when estimated tokens approach the model's context window.
- Produces a structured summary (decisions made, files changed, open TODOs, current plan) → replaces
  the old turns with a single summary message → conversation continues seamlessly.
- Token counts tracked in `context.py` (approximate, from `usage` fields returned by the API).

#### `/model`, `default`, and `auto`
- Models are `provider/model`. The preference `default` (the initial value) resolves to the default
  provider's first preferred model: `gpt-5.3-codex` on OpenAI, `llama3.2` on Ollama.
- `auto` is opt-in: before each turn `models.resolve_auto` picks from the **live model list** (all
  configured providers, qualified ids): a strong coding model for multi-step/edit prompts, a cheaper
  one for short questions, skipping dated snapshot ids when a plain id exists. This heuristic is the
  seed of the routing provider (ROADMAP 0.3.0).
- `/model <provider/model>` pins a model (a bare name is qualified with the default provider);
  `/model default` and `/model auto` return to the aliases. The choice persists in
  `~/.config/scoot/preferences.json`; `--model` / `SCOOT_MODEL` override it for a run.

### How ESC-interrupt works (stdlib only)
- The terminal is put in **cbreak/raw mode** via `termios`+`tty`; a small **key-listener thread**
  uses `select` to watch stdin for the ESC byte (`0x1b`) without needing Enter.
- The **agent runs in a worker thread**. On ESC, the listener sets a shared `cancel_event`.
- The client holds the model call's `subprocess.Popen`; on cancel it calls `.terminate()` so a
  long-running request dies **instantly** instead of after it returns. Tool subprocesses
  (esp. `run_shell`) are terminated the same way.
- Between steps the agent checks `cancel_event` and bails out cleanly. Net effect: ESC feels
  immediate because the only thing that can block (an external process) is killed outright.
- On exit we always restore the terminal mode (`try/finally`), even on crash.

### How intent is inferred (no verb parsing)
The agent's **system prompt + tool schemas** let the model decide per prompt:
- Question about code → answer directly (optionally after a `read_file`/`search`).
- "Explain / what does X do" → read then explain; no edit happens (it won't call a write tool).
- "Add / change / fix / rename X" → read, then propose `edit_file`/`write_file` (approval gated).
- "Create a new file/script" → call `write_file`.
- Ambiguous → ask a one-line clarifying question instead of guessing.

There are **no separate `chat` / `explain` / `edit` / `agent` code paths** — one loop, many behaviors.

### Optional shortcut presets (sugar, not required)
Thin conveniences that pre-seed the same agent (or, for `models`, call the API directly). Each also
works as a plain prompt inside the REPL:

| Preset (optional)              | Equivalent inside the REPL        | Notes |
| ------------------------------ | --------------------------------- | ----- |
| `scoot models`               | — (not an agent task)             | Direct `list_models()`; supports `--json`. |
| `scoot explain <path>`       | `explain <path>`                  | Read-only. |
| `scoot edit <path> -m "..."` | `edit <path>: ...`                | Still approval-gated. |
| `scoot "one-shot prompt"`    | first line of a REPL session      | Runs a single turn non-interactively, then exits. |

Presets are muscle-memory sugar only; removing them loses no functionality. v1 can ship with just the
REPL + `models`.

### Global flags (optional; the REPL needs none)
`--model`, `--proxy`, `--insecure`, `--root`, `--max-steps`, `--verbose/-v`, `--json`
(for the one-shot/preset forms). The interactive REPL is fully usable with **no flags at all**.

---

## 7. The Agentic Loop (`agent.py`)

Since the prompt-first design routes **everything** through this loop, it must gracefully handle both
extremes: a trivial one-line question (answer immediately, zero tools) and a multi-step coding task
(plan → tools → verify). The model decides which, based on the prompt and offered tools.

### Turn structure
```
1. Build messages:
     [ system(agent persona + tool contract + planning rules),
       user(task),
       ...prior turns (assistant text, tool_calls, tool results) ]
2. Call client.chat(messages, tools=TOOL_SCHEMAS, tool_choice="auto").
3. Inspect the assistant response:
     a. If message.tool_calls present:
          for each tool_call:
             - render intent (name + arguments)
             - REQUEST APPROVAL  (approve / edit-args / skip / abort)
             - if approved: execute tool -> capture result (truncated/limited)
             - append {role: "tool", tool_call_id, content: result}
          -> loop back to step 2
     b. Else (plain assistant text):
          - Check the STOP CONDITION (see below).
          - If task appears complete -> print final answer, exit loop.
          - If model is still "thinking out loud" without acting and not done,
            nudge with a continuation message ("proceed / call a tool or say DONE").
4. Enforce max-steps guard; on exceed -> summarize progress and stop.
```

### "Is the task done?" decision
Because we use **native tool calling**, the loop is primarily driven by whether the model returns
`tool_calls` or not:
- **`tool_calls` returned** → not done; execute (with approval) and continue.
- **No `tool_calls`, plain text** → candidate completion. Confirm via a lightweight convention:
  the agent system prompt instructs the model to end its final message with a
  **`DONE`** sentinel (and a short summary of what changed). If `DONE` is present → stop.
  If absent and no tool call → send one continuation nudge, then stop after a bounded number of
  nudges to avoid loops.

### Planning
- For **non-trivial tasks**, the agent system prompt asks the model to first produce a short numbered
  plan before acting. For **simple prompts** (a question, a one-liner) the prompt explicitly allows
  skipping the plan and answering directly — we don't want ceremony on "what does X do?".
- `scoot --plan-only` prints that plan and exits (dry run for the human).
- The plan lives as the first assistant message and is kept in context so the model can track
  progress ("step 3 of 5 done").
- Optional: a dedicated internal `update_plan` pseudo-tool later, so plan state is structured.

### Stop conditions (any triggers exit)
- Model emits `DONE` with no pending tool calls.
- `--max-steps` reached.
- User aborts an approval prompt.
- Unrecoverable tool error the model can't route around (bounded retries).

---

## 8. Tools (`tools/`)

Each tool = **JSON schema** (name, description, parameters) + **Python handler** + **result
formatter**. Registered in `tools/__init__.py` so schemas are auto-collected for the `tools` param.

| Tool         | Parameters                              | Approval | Notes / Safety |
| ------------ | --------------------------------------- | -------- | -------------- |
| `read_file`  | `path`, optional `start`, `end`         | ✅ (v1: all) | Must resolve inside `--root`; size cap (e.g. 200 KB); line-number annotated output. |
| `list_dir`   | `path`                                  | ✅       | Root-scoped; hides `.git`, huge dirs truncated. |
| `search`     | `query`, `is_regex`, `include_glob`, `files_only` | ✅ | ripgrep if available, else Python fallback; results **grouped by file** with per-file counts + sample lines (token-efficient); `files_only` for a bare file list. |
| `write_file` | `path`, `content`                       | ✅       | Shows full diff vs existing; creates parent dirs; refuses outside root. |
| `edit_file`  | `path`, `old_string`, `new_string`      | ✅       | Unique-match replacement (like a surgical patch); shows diff. |
| `run_shell`  | `command`, optional `cwd`               | ✅ (loud)| **Most dangerous** — always confirmed; command echoed verbatim; timeout; captured stdout/stderr truncated; runs in `--root`. |

### Tool safety rules (enforced in `base.py`)
- **Path sandboxing:** every fs path is resolved and must stay within `--root` (no `..` escapes,
  no absolute paths outside root). Reject with a clear error otherwise.
- **Output limits:** truncate large tool outputs before feeding back to the model (keeps token cost
  and context sane); note truncation explicitly.
- **Deterministic errors:** tools return structured `ToolResult(ok, content, error)` — errors are
  fed back to the model so it can adapt, not crashed.

### Native tool-calling contract
- Send `tools=[{type:"function", function:{name, description, parameters(JSON schema)}}...]`.
- `tool_choice="auto"`.
- Response tool calls carry `id` + `function.name` + `function.arguments` (JSON string) → parse,
  execute, reply with `role:"tool"` + matching `tool_call_id`.
- Adapters translate this contract to the vendor wire: Responses uses `function_call` /
  `function_call_output` items and flat tool definitions; the Anthropic adapter (0.2.0) uses
  `tool_use` / `tool_result` blocks. Consumers never see the difference.

---

## 9. Approvals (`approvals.py`)

**Approval modes (session-level, see §16 / M5.1):**

| Mode         | Read tools (read_file/list_dir/search) | Write tools (write/edit) | run_shell |
| ------------ | -------------------------------------- | ------------------------ | --------- |
| `always` (default) | prompt | prompt | prompt |
| `auto-read`  | auto-approve | prompt | prompt |
| `auto-edits` | auto-approve | auto-approve | prompt |
| `yolo`       | auto-approve | auto-approve | auto-approve* |

*Even in `yolo`, a **denylist** of catastrophic commands is always confirmed/blocked (see §16).

Set via `/approve <mode>`, `/yolo` (shortcut for `yolo`), or the `--approval`/`--yolo` CLI flags.
The mode is shown in `/status` and echoed when auto-approving so actions are never silent.

For a prompted call, the UI shows the tool name, pretty-printed arguments, and (for writes) a **diff**;
for `run_shell`, the exact command. Inline controls:

```
[a] approve once   [t] trust this tool for the session   [A] approve all this session (yolo)
[e] edit arguments   [s] skip (tell model no)   [q] abort agent
```

- **trust `[t]`** adds the tool to a per-session trust-list (`ReplSession.trusted_tools`) so further
  calls to it are auto-approved — kills approval fatigue on read-heavy exploration (search/read loops).
- **all-session `[A]`** switches the session to `yolo`. The **denylist still re-confirms** catastrophic
  shell commands even after `[t]`/`[A]`.
- **skip** returns a `ToolResult(ok=False, error="user declined")` so the model can choose another path.
- **abort** stops the whole loop.

---

## 10. UX & Output (`rendering.py`, `status.py`) — stdlib only
- **ANSI escape codes** for color/bold/dim (no `rich`); a tiny helper wraps them and honors
  `NO_COLOR` / non-TTY (auto-disable color when piped).
- **Spinner + live status** via `status.py`: a single redrawn line (carriage-return based) showing
  the current activity, tool calls, and `step n/m · esc to stop`.
- **Unified diffs** for file changes via stdlib `difflib` (green/red lines).
- `--json` for scripting (structured events: `turn`, `tool_call`, `tool_result`, `final`).
- `--verbose` prints raw request/response summaries and token usage (`prompt_tokens`,
  `completion_tokens`) like the example already does.

---


## 12. Install / Run

```bash
pipx install scootcli                    # users: isolated install, `scoot` on PATH
python3 scoot.pyz                        # or the single-file zipapp from the release page
pip install -e .                         # dev
./scripts/build.sh                       # dist/scoot.pyz + wheel + sdist (needs `pip install build`)
python -m twine upload dist/scootcli-*   # release
```

Packaging is `pyproject.toml` `[project]` metadata with `dynamic = ["version"]` read from
`scootcli.__version__`; `dependencies = []` is a design constraint, not an omission.

---

## 13. Security Notes
- **Official APIs only**, authenticated with the user's own API keys; Ollama runs locally.
- Keys come from the environment or `~/.config/scoot/credentials.json` (`0700`/`0600`), are validated
  before saving, and are never echoed; `rendering.redact()` masks key-shaped strings (`sk-…`, GitHub
  tokens, Bearer values, JWTs) in every output path including saved sessions.
- Only allowlisted keys are imported from `.env` files, so a project's secrets stay out of the process.
- All filesystem/shell actions are **root-sandboxed**; the shell denylist re-confirms catastrophic
  commands in every approval mode.
- Saved sessions are owner-only; `/forget all` removes them.
- **Zero third-party deps** shrinks the supply-chain attack surface (no external packages to vet).

---


## 15. Agent System Prompt (draft)

This is the single system prompt that powers intent inference. It is sent as the first `system`
message on every turn (with `{{workspace_root}}`, `{{os}}`, `{{model}}`, `{{tool_list}}` interpolated
at runtime, plus `{{agent_md}}` = the contents of `AGENTS.md` if present). Kept deliberately tight to
save tokens.

```text
You are scoot, a terminal coding assistant for a software engineer.
You run inside the user's workspace and act through tools. Be concise, correct, and safe.

ENVIRONMENT
- Working directory (root): {{workspace_root}}
- OS: {{os}}   Model: {{model}}
- All file paths you use must stay inside the root. Never touch paths outside it.

PROJECT CONTEXT (from AGENTS.md, if present)
{{agent_md}}

INTENT — READ THE USER, DON'T WAIT FOR COMMANDS
The user just types natural language. Infer what they want and act:
- A question ("what does X do?", "how do I…") -> answer directly. Read a file first only if you
  need its contents to be accurate. Do NOT modify anything for a question.
- "Explain / review X" -> read the relevant file(s), then explain. No edits.
- "Add / change / fix / rename / refactor X" -> read what you need, then make the change via the
  edit_file or write_file tool.
- "Create a new file/script" -> use write_file.
- If the request is ambiguous or could destroy work, ask ONE short clarifying question instead of
  guessing. Prefer acting when the intent is clear.

PLANNING
- For a multi-step task, first output a short numbered plan (max ~6 steps), then start executing it.
- For a trivial request (a question, a one-line change), skip the plan and just do it.
- Keep the user oriented: briefly say what you're about to do before a tool call.

TOOLS
You have these tools: {{tool_list}}
- Use tools to gather context instead of assuming. Read before you edit.
- Make the smallest change that satisfies the request. Preserve existing style.
- After editing, if feasible, verify (re-read the file or run a quick check via run_shell).
- Every tool call is shown to the user and must be approved by them. If a call is declined, adapt:
  choose another approach or ask what they'd prefer. Never try to bypass approval.
- Tool results may be truncated; request more specifically if you need it.

SAFETY
- run_shell is powerful: prefer read-only/idempotent commands, keep them short, explain why.
- Never print secrets or the contents of env files / tokens. Never exfiltrate data.
- Do not make sweeping changes the user didn't ask for.

COMPLETION
- When the task is fully done, stop calling tools and give a short summary of what you did
  (what changed, which files, any commands run, and anything the user should check).
- End that final message with the token `DONE` on its own line so the CLI knows you're finished.
- If you are blocked and cannot proceed, explain why and end with `DONE`.
```

### Notes on the prompt
- **`DONE` sentinel** is what the loop keys on to end a turn (see §7). Native tool-calling already
  signals "not done" via `tool_calls`; `DONE` disambiguates the no-tool-call case.
- **`{{tool_list}}`** is rendered from the tool registry so the prompt and the actual `tools` schema
  never drift.
- The **approval** language is intentionally in the prompt so the model gracefully handles a declined
  call rather than looping.
- Preset commands (`explain`, `edit`) simply prepend a one-line seed to the user's text; they reuse
  this same system prompt unchanged.
```

---

## 16. Autonomous (YOLO) Mode & Git-Worktree Isolation (M5.1)

### Problem
Approving every tool call is fine for careful edits but exhausting for read-heavy exploration or
larger autonomous tasks. We want a way to let the agent run with fewer (or no) prompts **without**
losing safety.

### Approach — two independent dials

**1. Approval mode (how much we prompt).** Session-level, three levels (see §9):
`always` → `auto-edits` → `yolo`. Default stays `always`. This is the immediate annoyance fix and is
useful on its own (e.g. `auto-edits` auto-approves `read_file`/`list_dir`/`search` but still gates
writes and shell).

**2. Isolation (where the agent writes).** Optional and independent of the mode:
`in-place` (today) or `worktree` (a throwaway git worktree/branch). YOLO is far safer when paired with
worktree isolation, but the two are orthogonal knobs.

### Controls
- `/approve always|auto-edits|yolo` — set the mode mid-session.
- `/yolo [--worktree]` — shortcut: switch to `yolo`, optionally spinning up an isolated worktree.
- CLI: `--approval <mode>`, `--yolo`, `--worktree`.
- `/status` shows the current mode + isolation + (if worktree) the branch/path.

### Safety that always applies (even in YOLO)
- **Root sandbox** on all fs/shell paths (unchanged).
- **`run_shell` denylist** — patterns that are always confirmed or refused regardless of mode:
  `rm -rf /`, `:(){:|:&};:`, `git push`, `sudo`, `curl`/`wget`/`nc` to external hosts, writing outside
  root, `chmod -R`, disk/format commands. Tunable via config.
- **Timeouts** on shell, **ESC** interrupt, and an **action audit log** at
  `.scoot/session-<ts>.log` (tool, args, result summary) so a YOLO run is fully reviewable.
- Auto-approved calls are **echoed** (`● write_file … (auto)`) — never silent.

### Git-worktree isolation (`worktree.py`)
Design goals: let the agent work freely, then give the human a single clean review/merge gate.

```
enter (/yolo --worktree):
  require: inside a git repo (git rev-parse) ; else offer fallback (below)
  ts = timestamp
  git worktree add .scoot/work-<ts> -b scoot/<ts>   (from current HEAD)
  session.root = .scoot/work-<ts>            # tools now sandboxed to the worktree
  banner: "⚙ working in isolated worktree scoot/<ts>"

...agent runs autonomously (yolo)...

finish (task done / user /done):
  show: git -C <worktree> diff --stat  + full diff
  ask: [m]erge into <original-branch>   [k]eep branch   [d]iscard
    merge  -> commit worktree changes, then fast-forward/merge branch into original; remove worktree
    keep   -> commit + leave branch scoot/<ts> for manual review; remove worktree dir
    discard-> git worktree remove --force ; delete branch
```

**Graceful degradation / edge cases**
- **Not a git repo** → offer: (a) plain in-place YOLO, or (b) **checkpoint mode** — auto `git init`
  is *not* forced; instead we snapshot changed files to `.scoot/backups/<ts>/` before each write so
  the user can restore. (Simplest safe fallback when git is unavailable.)
- **Dirty working tree** in main → worktree is created from HEAD, so uncommitted changes stay put and
  are untouched; we warn that they won't be visible to the agent.
- **Merge conflicts** on merge-back → abort the merge, keep the branch, tell the user to resolve
  manually (`git merge scoot/<ts>`).
- **Untracked/ignored files** the agent created → included in the worktree commit so they merge too.
- **Cleanup on crash/exit** → `try/finally` removes the worktree dir; the branch is retained unless
  discarded, so no work is lost.

### Rollout
- **M5.1a** ships approval modes only (no git) — immediate relief, low risk.
- **M5.1b** adds worktree isolation behind `--worktree`, defaulting **off**.

---

## 17. Error Handling & Resilience (M5.2)

### Principle
Every failure is (1) a **typed error**, (2) **retried** only when it makes sense, and (3) surfaced as a
**friendly, actionable one-liner** — never a Python traceback, and the REPL always survives.

### New typed errors (`errors.py`)
`NetworkError`, `TimeoutError`, `SSLError` (⊂ `TransportError`); `RateLimitError`, `ServerError`,
`QuotaError`, `ContextLengthError` (⊂ `ApiError`). Each carries a short `hint` for the user.

### Transport layer — classify socket/TLS failures
| Failure | Maps to | User hint |
| ------- | ------- | --------- |
| DNS resolution failed | `NetworkError` | no internet, DNS failure, or a typo in the base URL |
| connection refused/reset | `NetworkError` | is the endpoint up? check the base URL and proxy |
| timeout | `RequestTimeout` | slow or blocked network; will retry |
| TLS/cert error | `SSLError` | check the endpoint URL and CA certificates |
| proxy `CONNECT` refused (407) | `ProxyError` | put credentials in `HTTPS_PROXY` as `http://user:pass@host:port` |

### Client layer — switch on HTTP status
| Status | Maps to | Behavior |
| ------ | ------- | -------- |
| 401 | `AuthError` | no retry; hint names the key variable and `scoot auth` |
| 402 / 403 | `QuotaError` / permission | no retry; explain billing/quota/permission |
| 404 | `ApiError` | no retry; likely bad model/endpoint |
| 429 | `RateLimitError` | **retry with backoff**, honor `Retry-After` header |
| 400 `context_length_exceeded` | `ContextLengthError` | suggest/auto-run `/compact` then retry |
| 5xx | `ServerError` | **retry with backoff** |
| other | `ApiError` | surface message |

### Retry/backoff
- Capped exponential with jitter: ~3 attempts, base 0.5 s, factor 2, cap ~8 s.
- **Only** for `NetworkError`/`TimeoutError`/`RateLimitError`/`ServerError`. Never for 4xx/auth/quota.
- Backoff sleep is **interruptible** (checks `cancel_event`) so ESC still works while waiting.
- Emit a subtle status line on retry: `retrying in 2s (attempt 2/3)…`.

### Out-of-tokens / quota
- Detect quota exhaustion from status (402/403) and/or error `code`/`message`
  (e.g. `quota`, `insufficient`, `limit reached`) → `QuotaError` with a clear message; no retry.

### REPL surfacing
- One-line, colored, with the hint. Examples:
  - `⚠ network: no internet / proxy unreachable — check VPN, then retry.`
  - `⚠ no API key for openai — set OPENAI_API_KEY or run scoot auth set openai.`
  - `⚠ context too long — run /compact to shrink, then resend.`  (offer to auto-compact + retry)
  - `⚠ rate limited — retried 3×, still busy. Try again shortly.`
- The turn's user message is preserved so the user can just retry.

---

## 18. Authentication & Onboarding

### What scoot uses
Plain provider API keys. There is no OAuth, no token exchange, no session cookie: each provider row
names the environment variables that may hold its key (`OPENAI_API_KEY`), and whether a key is
required at all (Ollama: no).

### Where a key comes from (search order)
1. The provider's environment variables (which a `.env` file may have supplied, see §4).
2. scoot's saved credentials: `~/.config/scoot/credentials.json`, `{"keys": {"openai": "sk-…"}}`.

### `/auth` (and `scoot auth`)
- With **no argument**: one row per provider: key source (`env:NAME`, `saved`, `no key`, or
  `no key needed`), the default marker, the base URL, and where saved keys live.
- `/auth set <provider>`: read the key with **hidden input**, **validate** it by listing the provider's
  models, and only then **save** it. On failure nothing is stored.
- `/auth clear <provider>`: forget the saved key; report when a key from the environment still applies.
- `scoot auth …` runs the same flows from the shell (handy before entering the REPL).

### Secure storage (`credentials.py`)
- Directory `0700`, file `0600`, written through a restrictive `os.open` so the key is never briefly
  world-readable. `SCOOT_CONFIG_DIR` relocates it (tests use this).
- Stdlib-only, no OS keychain, so the zipapp stays self-contained.

### First-run onboarding
- If the default provider needs a key and none is found, startup prints one hint line naming the
  variable and the `scoot auth set` command, and still opens the REPL.

### Tests
- Key precedence (env beats saved), storage modes, keyless providers always configured, status rows.
- Fake transport per `(status, body)` → the right typed error and retry count; 401 not retried;
  missing key fails before any request; keyless provider sends no `Authorization` header.

---

## 19. Fixed bottom input + scrolling output (M14) — DESIGN / PROPOSAL

**Goal.** Pin the prompt input to a fixed line at the bottom of the terminal (just above the status
bar). All output (assistant text, tool calls, approvals, spinner) scrolls in the region *above* it.
The input line never scrolls away — like a chat TUI. (Also: clear the screen on launch — shipped
separately as a small fix.)

### Layout (bottom-up)
```
row  N     : status bar (reverse video)            [existing]
row  N-1   : input line   › <buffer with cursor>   [NEW — fixed]
row  N-2   : blank spacer (separates output/input)  [separator]
rows 1..N-3: scrollable OUTPUT region (DECSTBM)      [content]
```
We reserve the **bottom 3 rows** (spacer + input + bar); the scroll region shrinks to `1..N-3`.
Because the input/bar rows sit *outside* the scroll region, output printed while the cursor is in the
region scrolls beneath them and can never overwrite them.

### New module: `lineeditor.py` (stdlib raw-mode single-line editor)
A minimal readline that renders on a fixed row and returns the typed string.
- State: `buffer` (list of chars), `cursor` index, horizontal `scroll` offset, `history` + index.
- `readline(prompt) -> str | None` (None on EOF/Ctrl-D at empty buffer).
- Render: move to the input row, clear it, draw `prompt` + the visible slice of the buffer, then place
  the real cursor at the right column. Uses cursor save/restore + absolute positioning (like the bar).
- Keys (via a small escape-sequence parser over `keys.read_key`):
  - printable → insert; **Backspace** → delete-before; **Enter** → submit.
  - **Ctrl-C** → quit app; **Ctrl-D** (empty) → EOF; **Ctrl-U/K/W** → kill line/to-end/word;
    **Ctrl-A/E** → home/end.
  - **←/→** move; **Home/End**; **↑/↓** history.
  - **Horizontal scroll** when `prompt+buffer` exceeds width (show a window; never wrap into other rows).
- Pure **state machine** (`apply_key(state, key) -> state`) so it's unit-testable with scripted keys,
  no TTY needed (matches our network-free test style). Rendering is a thin separate layer.

### Coordinator: extend the bottom dock
- Generalize the current `panel.StatusBar` (or add a `BottomDock`) that owns the reserved region:
  sets scroll region `1..N-3`, draws spacer(N-2), lets the editor own the input row (N-1), draws the
  bar (N). One place computes geometry + handles resize (recompute, re-establish region, redraw).
- `repl._loop()` replaces `input("› ")` with `editor.readline("› ")`. Before running a turn, park the
  cursor at the bottom of the output region so streaming/prints flow correctly above the input.
- Approvals + spinner + streaming are unchanged (they already write at the cursor, which is in-region).

### Raw-mode lifecycle
- `readline()` enters cbreak/raw for editing and restores after (reuse `keys.py` termios helpers).
- The agent turn keeps using `InterruptibleSection` for ESC. Terminal state is restored in `finally`
  (scroll region reset `\033[r`, reserved rows cleared, cooked mode) — even on crash.

### Safety / fallback
- **Feature-flagged**: `--no-dock` / `SCOOT_DOCK=false`. If anything misbehaves, this falls back to
  today's inline `input()` behavior. Default **on** for TTYs.
- **Non-TTY** (piped stdin / tests): bypass the dock entirely → plain `input()`; bar/editor are no-ops.
  Keeps piping and the test suite working unchanged.

### Edge cases / risks (call out before building)
- **Line wrap**: long inputs use horizontal scroll, never wrap (which would corrupt other rows).
- **Unicode width**: v1 counts codepoints; wide chars (CJK/emoji) may misalign by a column — acceptable
  for v1, note it.
- **Resize (SIGWINCH)**: recompute geometry and redraw; scroll region self-heals on next render.
- **Paste**: bracketed-paste (`\033[?2004h`) captured as one burst; embedded newlines → spaces in v1
  (multi-line compose via Alt+Enter is future work).
- **Ctrl-C**: quit app (consistent with today). **Terminal restore** guaranteed via try/finally.

### Milestones
- **M14a** ✅ — Dock + fixed input MVP: `lineeditor.py` (pure `apply_key` state machine + raw-mode
  renderer), `StatusBar(reserve_input=True)` reserves the bottom 3 rows (scroll region `1..N-3`,
  spacer N-2, input N-1, bar N), editor with printable/Backspace/Enter/Ctrl-C/Ctrl-D, `input()`
  fallback on non-TTY, `--no-dock` / `SCOOT_DOCK=false`. Submitted prompt is echoed into the
  scrolling region; cursor save/restore keeps output flowing above the pinned input. (92 tests.)
- **M14b** ✅ — Full editing: ←/→ + Ctrl-B/F, Home/End + Ctrl-A/E, Ctrl-U/K/W kill ops, forward
  Delete (`ESC[3~`), ↑/↓ + Ctrl-P/N history recall (with live-line stash), horizontal scroll so the
  caret stays visible, implicit resize (render recomputes size each keystroke). Reader switched to raw
  `os.read` so multi-byte ESC sequences aren't stranded in stdin's buffer. (101 tests.)
- **M14c** ✅ — Bracketed paste: `readline` toggles `ESC[?2004h/l`, the reader recognises `ESC[200~ …
  ESC[201~` and inserts the payload via the pure `insert_text` (newlines/tabs → spaces, control chars
  dropped) so a multi-line paste never submits mid-way; payload read is stall-bounded. (104 tests.)

### Tests
- Unit-test `apply_key` transitions: insert, backspace, cursor moves, home/end, kill-word, history,
  Enter returns buffer, Ctrl-D empty → EOF. All TTY-free.
- Fallback: non-TTY `readline()` delegates to `input()`.
- pty smoke (manual): type + Enter renders on the fixed row while output scrolls above.


