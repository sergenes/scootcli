# scoot

[![PyPI](https://img.shields.io/pypi/v/scootcli.svg?label=PyPI)](https://pypi.org/project/scootcli/)
[![tests](https://github.com/sergenes/scootcli/actions/workflows/tests.yml/badge.svg)](https://github.com/sergenes/scootcli/actions/workflows/tests.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#install-options)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)](#install-options)
[![Providers](https://img.shields.io/badge/providers-OpenAI%20%7C%20Ollama%20%7C%20Anthropic-orange.svg)](#providers-and-models)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

```
       ╭───╮      scoot: a tiny coding agent that goes where you point it.
       │o o│
    T──┤───┤      pipx install scootcli   (or: curl -fsSL https://raw.githubusercontent.com/sergenes/scootcli/main/install.sh | bash)
    │  ╰┬─┬╯      scoot
  (o)═══╧═╧═(o)
```

`scoot` is a terminal coding agent in plain Python.
You type what you want in natural language; it reads, searches, edits, and runs things in your repo, asking before anything risky.
It talks to official model APIs (OpenAI, Anthropic, and a local Ollama for free) and has **zero third-party dependencies**: the whole tool is the Python standard library, and it ships as a single-file zipapp as well as a wheel.

## Where this comes from

scoot began as [mini_agent](https://github.com/sergenes/mini_agent), a fifty-line Python script written to answer one question: where exactly does a chatbot turn into an agent?
The answer was a `while` loop that sends the conversation to a model, runs whatever tool the model asks for, appends the result, and goes around again until the model answers without calling a tool.
The article [Building an AI Agent from Scratch: No Magic, Just a Deterministic Loop](https://levelup.gitconnected.com/building-an-ai-agent-from-scratch-no-magic-just-a-deterministic-loop-a916161705fb?sk=bd25f188cb4cb68d52ef5b77dd67336a) (free link) walks through that script, swaps the cloud model for a local one, and adds tools and MCP on top of the same loop.
Its conclusion is the design brief for this tool:

> There's no magic. The model observes the conversation history, decides whether it has enough to answer or needs a tool, and repeats until it's done.
>
> Build the naive version first. Then decide.

The naive version did real work in my repos for long enough that the next step was obvious: keep the deterministic loop at the centre and build the rest of a proper command-line tool around it, approvals, sessions, a REPL, and a provider layer, without adding a framework or a dependency.

## Quick start

```bash
pipx install scootcli && pipx ensurepath   # then open a new terminal so `scoot` is on PATH
scoot auth set openai            # paste your OpenAI API key once (hidden input, validated, stored 0600)
cd ~/code/your-project
scoot                            # open the REPL
```

No pipx yet? On Debian, Ubuntu, and Pop!_OS it is `sudo apt install pipx`; on macOS `brew install pipx`.
Or skip pipx entirely with the one-line installer, which needs only `curl` and Python 3.9+:

```bash
curl -fsSL https://raw.githubusercontent.com/sergenes/scootcli/main/install.sh | bash
```

Or run entirely local with [Ollama](https://ollama.com), no key at all:

```bash
ollama pull llama3.2
scoot --model ollama/llama3.2
```

**First run.** With no key saved and no Ollama running, scoot still opens: the banner says "no provider set up yet", a short block lists the three ways to set one up, and the bar shows `not set up` until a provider can answer.
Run `scoot auth set openai` (or `anthropic`) inside the REPL and the bar switches to the real provider and model at once.

The first prompt:

```
❯ add a --version flag to cli.py
  I'll read cli.py, then add the flag.
  ● read_file {"path": "cli.py"}
  ✔ read_file  128 lines
  ● edit_file {"path": "cli.py", ...}   (+6 -0)
  ✔ edit_file  cli.py +6 -0
🛴 scoot
  Added --version to the parser; it prints the package version and exits.
```

## Providers and models

Models are addressed as `provider/model`.
A bare name means the default provider, which is the first provider that has a key, else Ollama.

| provider | how it is reached | key | default model |
|---|---|---|---|
| `openai` | OpenAI Responses API | `OPENAI_API_KEY` | `gpt-5.3-codex` |
| `ollama` | local Ollama, Responses API | none | `llama3.2` |
| `anthropic` | Anthropic Messages API | `ANTHROPIC_API_KEY` | `claude-opus-5` |

```bash
scoot models                         # every configured provider, grouped
scoot models --provider ollama       # one provider
scoot --model openai/gpt-5.3-codex "..."
scoot --model auto "..."             # pick a model per prompt from the live list (cheap for trivial, strong for edits)
```

Inside the REPL, `/model <provider/model>` switches and is remembered for the next launch; `/model default` goes back to the provider's preferred model.

### Routing

`auto` is opt-in (`--model auto`, `SCOOT_MODEL=auto`, or `/model auto`) because choosing costs a decision per turn.
Without any configuration it uses a built-in heuristic over the models your providers actually list: a strong coding model for multi-step or editing prompts, a cheaper one for short questions, never a dated snapshot.
A turn is routed once, at its first model call, and stays on that model.

Write your own rules in `~/.config/scoot/router.json` (or the file named by `SCOOT_ROUTER`); first match wins, and a rule whose provider has no key is skipped:

```json
{
  "rules": [
    {"when": {"has_images": true},        "use": "anthropic/claude-opus-5"},
    {"when": {"complex": true},           "use": "openai/gpt-5.3-codex"},
    {"when": {"est_tokens_over": 60000},  "use": "anthropic/claude-sonnet-5"},
    {"when": {"prompt_matches": "(?i)translate|summari[sz]e"}, "use": "ollama/qwen3"}
  ],
  "default": "ollama/llama3.2",
  "classifier": {
    "model": "openai/gpt-5-mini",
    "tiers": {"simple": "ollama/llama3.2", "coding": "openai/gpt-5.3-codex", "hard": "anthropic/claude-opus-5"}
  }
}
```

Conditions: `complex`, `has_images`, `needs_tools`, `est_tokens_over`, `prompt_matches`.
The optional `classifier` asks a small model one question per turn ("simple, coding, or hard?") and maps the answer to a tier; it adds a short call, and any failure falls through to the rules.
Expect it to be rough with a 3B local model: on a hand-labelled set of seven prompts, `llama3.2` and `qwen2.5` each got four right, mostly confusing "coding" with "hard".
The rules are deterministic, so put the decisions you care about there, and if you want a better judge, name a cheap hosted model as the classifier (`openai/gpt-5-mini`), which costs a few hundred tokens per turn.
`/route` shows the rules in force and why the current model was picked; `/status` shows tokens, cached tokens, and cost per model, from the list prices in `pricing.py` (stamped with the date they were last checked; unknown models show `?`).
`SCOOT_EFFORT` (`low` | `medium` | `high` | `xhigh`, default `medium`) sets the reasoning effort for models that take it, on both OpenAI and Anthropic.
On Claude Opus 5 the server-side refusal fallback is requested by default, so a declined request is retried on another Claude model inside the same call; `SCOOT_ANTHROPIC_FALLBACKS=0` turns that off.

A note on how this is built: scoot speaks the OpenAI chat format internally and translates at the edge.
Adding a provider that speaks that format is one registry row; a different wire format is one small adapter (`src/scootcli/providers/`).

## Configuration

Settings resolve as **CLI flag → environment variable → project `.env` → global `.env` → default**.

Two optional `.env` files are read: `~/.config/scoot/.env` (the stable place for keys) and the nearest `.env` walking up from the current directory.
Only scoot's own keys are imported from them: `SCOOT_*`, provider API keys, and `HTTPS_PROXY` / `NO_PROXY`.
A project's other secrets never enter scoot's process through a `.env` file.

```bash
cp .env.example ~/.config/scoot/.env && chmod 600 ~/.config/scoot/.env
```

The most useful settings (see [`.env.example`](./.env.example) for all of them):

| setting | meaning | default |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI key (or `scoot auth set openai`) | |
| `SCOOT_PROVIDER` | default provider for bare model names | first with a key, else `ollama` |
| `SCOOT_MODEL` | `default`, `auto`, or `provider/model` | `default` |
| `SCOOT_EFFORT` | reasoning effort | `medium` |
| `SCOOT_APPROVAL` | `always` · `auto-read` · `auto-edits` · `yolo` | `yolo` |
| `SCOOT_MAX_STEPS` | tool-call steps per turn before asking to continue | `50` |
| `SCOOT_OLLAMA_BASE_URL` | where Ollama listens | `http://localhost:11434/v1` |
| `HTTPS_PROXY` | proxy for hosted providers; localhost is never proxied | |

## Usage

```bash
scoot                                # interactive REPL
scoot "explain what a Python dataclass is"   # one-shot turn, then exit
scoot explain src/auth.py            # preset: explain a file (read-only)
scoot edit cli.py -m "add a --version flag"  # preset: edit with an instruction
scoot --continue                     # resume the most recent session for this directory
scoot --resume <id>                  # resume a specific saved session
scoot --yes "fix the failing test"   # auto-approve every tool call (scripting/CI)
scoot --approval auto-edits "..."    # auto reads and edits, prompt only for shell
scoot --json "..."                   # machine-readable result (never streamed)
scoot --verbose "..."                # model and token usage on stderr
scoot models --json                  # machine-readable model list
scoot auth                           # which providers have a key
scoot auth set openai                # save a key; scoot auth clear openai forgets it
scoot --no-logo                      # hide the mascot; /logo off remembers it
scoot --no-panel --no-dock           # plain prompt, no status bar (also what you get without a TTY)
```

### In the REPL

```
/help      list commands            /reset     clear the conversation
/status    provider, model, tokens  /save FILE dump the transcript
/init      scan project → AGENTS.md /compact   summarize and shrink context
/model     list or switch model     /approve   set mode (always|auto-read|auto-edits|yolo)
/yolo      auto-approve all         /worktree  isolate work in a git worktree
/auth      provider keys            /logo      show or toggle the mascot
/scope     where file tools may go   /hooks     configured hooks and results
/route     how auto picks a model
/sessions  list saved sessions      /resume    resume a saved session ([id])
/forget    delete session(s)        /panel     toggle the bottom status bar
/verbosity feed detail (full|compact|quiet)
/c         copy last answer (Ctrl-S) /exit      quit (also Ctrl-C)
```

Press **ESC** while a turn is running to interrupt it; the conversation is kept.
Press **Ctrl-N** while a turn is running to add a note: scoot asks for one line at the next model call and the model sees it before continuing ("use pytest, not unittest").
Type `/` and press **Tab** to complete slash commands.

**Terminals.** scoot works in macOS Terminal, iTerm2, and inside tmux; the status bar uses a scroll region, the input dock uses raw mode, and clipboard copy uses the system tool or an OSC-52 escape.
Under tmux, ESC reaches scoot only after tmux's `escape-time` has passed, so with the default 500 ms the interrupt feels delayed; `set -sg escape-time 10` in `~/.tmux.conf` makes it immediate.
For clipboard copy through tmux, `set -g set-clipboard on` (or `external`) lets the OSC-52 escape reach the outer terminal.
Without a TTY, scoot falls back to a plain prompt with no bar and no dock.

### Tools and approvals

The agent has eight tools: `read_file`, `list_dir`, `search`, `write_file`, `edit_file`, `run_shell`, `open_editor` (hands a file to IntelliJ IDEA's `idea -e` or to VS Code, `SCOOT_EDITOR` picks), and `update_plan` (a progress checklist for multi-step work).
Paths are resolved against the workspace root, and `~` works.

The workspace is where the agent works, not a wall.
When a tool needs a file outside it, in `~/.config`, another repo, `/etc`, scoot asks once: allow this path `[a]`, allow that directory for the session `[d]`, allow anywhere for the session `[A]`, skip `[s]`, or quit `[q]`.
`--scope anywhere`, `SCOOT_SCOPE=anywhere`, or `/scope anywhere` skip the question; `/scope` shows what has been granted.

When a call needs approval you can approve once `[a]`, trust that tool for the session `[t]`, approve everything this session `[A]`, edit the arguments `[e]`, skip `[s]`, or quit `[q]`.
`/approve <mode>` sets how much runs without asking: `always` prompts for everything, `auto-read` auto-approves reads, `auto-edits` also auto-approves file edits, `yolo` runs everything.
Catastrophic shell commands (a denylist: `rm -rf /`, `git push --force`, piping downloads into a shell, and so on) are re-confirmed in every mode.

For risky autonomous runs, `/worktree start` moves the work into a throwaway git worktree; `/worktree merge` or `/worktree discard` when done.

### Streaming, images, sessions

Responses stream live and stay interruptible.
Drag an image into the prompt and a vision-capable model describes it into the turn as text, so even a text-only coding model can act on it; `SCOOT_VISION_MODEL` pins the describer, `--no-images` turns the feature off.
Every turn auto-saves under `~/.local/state/scoot/sessions/` (owner-only, secrets redacted, last 20 kept); `scoot --continue` or `/resume` picks up where you left off.

### Automation: hooks and headless mode

**Hooks** run your own scripts at lifecycle events: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `Notification`, `SessionEnd`.
A hook gets a JSON payload on stdin and answers with an exit code or JSON on stdout; the shapes follow the convention Claude Code established, so a script written for one works with the other.
Put them in `~/.config/scoot/hooks.json` or `.scoot/hooks.json` in the project:

```json
{
  "PreToolUse": [
    {"matcher": "run_shell|write_file|edit_file", "hooks": [{"type": "command", "command": "~/bin/guard.py", "timeout": 30}]}
  ],
  "Stop": [{"hooks": [{"type": "command", "command": "~/bin/notify.sh"}]}]
}
```

A `PreToolUse` hook can answer `{"permissionDecision": "deny", "reason": "..."}` to skip a tool (the model is told why), `allow` to skip the approval prompt, or `ask` to force one even in `yolo`; exit code 2 denies with stderr as the reason. Claude Code's nested `hookSpecificOutput` answer is accepted as is, so a hook written for Claude Code works without edits.
A `Stop` hook that answers `{"decision": "block", "reason": "run the tests first"}` sends the agent back to work with that instruction, at most three times per turn.
Matchers understand Claude Code's tool names as well as scoot's, so `"matcher": "Bash|Write|Edit"` fires for `run_shell`, `write_file`, and `edit_file`, and the payload carries the alias as `tool_alias`: one `hooks.json` and one script can serve both tools.
`/hooks` shows what is configured and what ran; `SCOOT_HOOKS=0` turns hooks off.

**Headless mode** is for editors, automation, and remote-control tools: `scoot --headless` reads JSON lines on stdin (`prompt`, `approve`, `note`, `interrupt`, `command`, `shutdown`) and writes JSON lines on stdout (streamed text, tool calls, approval requests, results, plan updates, usage, errors, a heartbeat), with nothing else ever printed there.
Same sessions, tools, approvals, routing, and hooks as the REPL; every `turn_end` carries the turn's tokens and cost, and `--json` one-shot output carries `cost` too.
The message tables and a full transcript are in [`docs/headless-protocol.md`](./docs/headless-protocol.md); an unanswered approval is denied after `SCOOT_APPROVAL_TIMEOUT` seconds (default 120).

### The status bar

The bottom row shows the mascot's face (its eyes follow the turn: `o o` idle, `> >` thinking, `- -` stopped), the provider, the workspace and session id, the model, the approval mode, context size against the auto-compact threshold, cumulative tokens, message count, and the last error if any.

## Install options

```bash
pipx install scootcli && pipx ensurepath       # recommended: isolated; ensurepath puts ~/.local/bin on PATH for new shells
curl -fsSL https://raw.githubusercontent.com/sergenes/scootcli/main/install.sh | bash   # no pipx: puts the zipapp at ~/.local/bin/scoot
pip install scootcli                           # inside a venv, container, or CI job (system Pythons on recent Linux and Homebrew block pip outside a venv)
curl -LO https://github.com/sergenes/scootcli/releases/latest/download/scoot.pyz && python3 scoot.pyz  # single file, no install
git clone https://github.com/sergenes/scootcli && cd scootcli && pip install -e .                       # from source
```

The install script needs only `curl` and Python 3.9+. `SCOOT_VERSION=v0.2.0` pins a release and `SCOOT_INSTALL_DIR` changes the target; read it before you run it, it is sixty lines.
## Upgrade options

Use the same tool you installed with:

```bash
pipx upgrade scootcli                          # pipx install
pip install --upgrade scootcli                 # pip install
curl -fsSL https://raw.githubusercontent.com/sergenes/scootcli/main/install.sh | bash   # the curl installer: rerun it, it always fetches the latest release
scoot --version                                # what you have
scoot --check-update                           # ask PyPI whether a newer release exists
```

With `SCOOT_UPDATE_CHECK=1` the REPL checks once in the background at start and shows `⬆ x.y.z` in the status bar; nothing contacts PyPI otherwise.
To remove scoot: `pipx uninstall scootcli`, `pip uninstall scootcli`, or `bash install.sh --uninstall` for the curl installer; config in `~/.config/scoot` and sessions in `~/.local/state/scoot` stay either way.

Both pipx and the script install into `~/.local/bin`. On a fresh Linux account that directory is added to PATH at login only if it already exists, so after the very first install either open a new login shell or run `pipx ensurepath`; the installer prints the exact line for your shell.

Requirements: Python 3.9 or newer on macOS or Linux.
Nothing else: no compiler, no packages, no `curl`.

## Development

```bash
pip install -e .
python -m pytest -q          # network-free suite
./scripts/build.sh           # dist/scoot.pyz + wheel + sdist
```

Design notes live in [`DESIGN.md`](./DESIGN.md), the behaviour spec in [`SPEC.md`](./SPEC.md), what is next in [`ROADMAP.md`](./ROADMAP.md), and the release history in [`CHANGELOG.md`](./CHANGELOG.md).
New capability is a drop-in file: a tool in `tools/`, a slash command in `commands/`, a provider row or adapter in `providers/`.

## Security

- API keys are read from the environment or from `~/.config/scoot/credentials.json` (directory `0700`, file `0600`), validated before saving, and never echoed; `rendering.redact()` masks key-shaped strings in all output.
- Only allowlisted keys are imported from `.env` files.
- All file and shell tools are sandboxed to the workspace root; destructive shell commands are always re-confirmed.
- Saved sessions are owner-only with secrets redacted; `/forget all` removes them.
- No third-party dependencies means no third-party code to audit.

## Contributing

Stars and forks are welcome, and so is using scoot to improve scoot: it is a coding agent, so point it at its own repo and let it do the work while you review.
Bug reports with a way to reproduce them are the most useful thing you can send.

Pull requests are welcome too, with one honest caveat: I intend to keep this tool small, so I will not merge most feature proposals.
A feature gets in when it is clearly useful to most users of a coding agent, fits the stdlib-only constraint, and comes with tests.
If you have an idea that does not meet that bar, a fork is the right home for it, and I am happy to link to forks that go somewhere interesting.

## License

MIT, see [`LICENSE`](./LICENSE).
Written by [Sergey Nes](https://www.linkedin.com/in/sergey-neskoromny).
