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
    T──┤───┤      pipx install scootcli
    │  ╰┬─┬╯      scoot
  (o)═══╧═╧═(o)
```

`scoot` is a terminal coding agent in plain Python.
You type what you want in natural language; it reads, searches, edits, and runs things in your repo, asking before anything risky.
It talks to official model APIs (OpenAI today, a local Ollama for free, Anthropic next) and has **zero third-party dependencies**: the whole tool is the Python standard library, and it ships as a single-file zipapp as well as a wheel.

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
pipx install scootcli            # or: pip install scootcli
scoot auth set openai            # paste your OpenAI API key once (hidden input, validated, stored 0600)
cd ~/code/your-project
scoot                            # open the REPL
```

Or run entirely local with [Ollama](https://ollama.com), no key at all:

```bash
ollama pull llama3.2
scoot --model ollama/llama3.2
```

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
| `anthropic` | Messages API | `ANTHROPIC_API_KEY` | arrives in 0.2.0 |

```bash
scoot models                         # every configured provider, grouped
scoot models --provider ollama       # one provider
scoot --model openai/gpt-5.3-codex "..."
scoot --model auto "..."             # pick a model per prompt from the live list (cheap for trivial, strong for edits)
```

Inside the REPL, `/model <provider/model>` switches and is remembered for the next launch; `/model default` goes back to the provider's preferred model.
`SCOOT_EFFORT` (`low` | `medium` | `high` | `xhigh`, default `medium`) sets the reasoning effort for models that take it.

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
/sessions  list saved sessions      /resume    resume a saved session ([id])
/forget    delete session(s)        /panel     toggle the bottom status bar
/verbosity feed detail (full|compact|quiet)
/c         copy last answer (Ctrl-S) /exit      quit (also Ctrl-C)
```

Press **ESC** while a turn is running to interrupt it; the conversation is kept.
Type `/` and press **Tab** to complete slash commands.

**Terminals.** scoot works in macOS Terminal, iTerm2, and inside tmux; the status bar uses a scroll region, the input dock uses raw mode, and clipboard copy uses the system tool or an OSC-52 escape.
Under tmux, ESC reaches scoot only after tmux's `escape-time` has passed, so with the default 500 ms the interrupt feels delayed; `set -sg escape-time 10` in `~/.tmux.conf` makes it immediate.
For clipboard copy through tmux, `set -g set-clipboard on` (or `external`) lets the OSC-52 escape reach the outer terminal.
Without a TTY, scoot falls back to a plain prompt with no bar and no dock.

### Tools and approvals

The agent has eight tools: `read_file`, `list_dir`, `search`, `write_file`, `edit_file`, `run_shell`, `open_editor` (hands a file to IntelliJ IDEA's `idea -e` or to VS Code, `SCOOT_EDITOR` picks), and `update_plan` (a progress checklist for multi-step work).
Every tool is sandboxed to the workspace root.

When a call needs approval you can approve once `[a]`, trust that tool for the session `[t]`, approve everything this session `[A]`, edit the arguments `[e]`, skip `[s]`, or quit `[q]`.
`/approve <mode>` sets how much runs without asking: `always` prompts for everything, `auto-read` auto-approves reads, `auto-edits` also auto-approves file edits, `yolo` runs everything.
Catastrophic shell commands (a denylist: `rm -rf /`, `git push --force`, piping downloads into a shell, and so on) are re-confirmed in every mode.

For risky autonomous runs, `/worktree start` moves the work into a throwaway git worktree; `/worktree merge` or `/worktree discard` when done.

### Streaming, images, sessions

Responses stream live and stay interruptible.
Drag an image into the prompt and a vision-capable model describes it into the turn as text, so even a text-only coding model can act on it; `SCOOT_VISION_MODEL` pins the describer, `--no-images` turns the feature off.
Every turn auto-saves under `~/.local/state/scoot/sessions/` (owner-only, secrets redacted, last 20 kept); `scoot --continue` or `/resume` picks up where you left off.

### The status bar

The bottom row shows the mascot's face (its eyes follow the turn: `o o` idle, `> >` thinking, `- -` stopped), the provider, the workspace and session id, the model, the approval mode, context size against the auto-compact threshold, cumulative tokens, message count, and the last error if any.

## Install options

```bash
pipx install scootcli                          # recommended: isolated, `scoot` on PATH
pip install scootcli                           # anywhere
curl -LO https://github.com/sergenes/scootcli/releases/latest/download/scoot.pyz && python3 scoot.pyz  # single file, no install
git clone https://github.com/sergenes/scootcli && cd scootcli && pip install -e .                       # from source
```

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
