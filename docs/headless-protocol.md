# Headless protocol

`scoot --headless` speaks line-delimited JSON: one object per line on stdin, one object per line on stdout, nothing else on stdout.
Diagnostics go to stderr.
Protocol version 1.
Within a major version fields are only added, never renamed or removed, and unknown fields must be ignored by both sides.

Start it the way you would start the REPL, with the same flags: `scoot --headless --root /path/to/repo --model openai/gpt-5.3-codex --approval always`.
Sessions, compaction, tools, hooks, and routing behave as in the REPL.
The status bar, the input dock, and the mascot are off.
`--continue` and `--resume <id>` load a saved session first.

## Input messages (stdin)

| type | fields | effect |
|---|---|---|
| `prompt` | `text`, optional `images` (list of file paths) | starts a turn; ignored while a turn runs until the turn ends (queued) |
| `approve` | `id`, `decision`: `allow` \| `allow_tool` \| `allow_session` \| `deny` \| `abort`, optional `args` (edited tool arguments) | answers an `approval_request`; `allow_tool` trusts the tool for the session, `allow_session` switches to `yolo`, `abort` ends the turn |
| `note` | `text` | queued and delivered as a user message at the next model call of the running turn (or the next turn) |
| `interrupt` | | cancels the running turn, like ESC in the REPL; the conversation is kept |
| `command` | `name`, optional `args` | runs a slash command (`status`, `model`, `compact`, `reset`, `route`, `hooks`, ...); its text output comes back in `command_output`; `exit` shuts down |
| `shutdown` | | saves the session and exits with code 0; closing stdin does the same |
| `approve` (for a `scope_request`) | `id`, `decision`: `allow_once` \| `allow_dir` \| `allow_all` \| `deny` \| `abort` | answers a request for a path outside the workspace |

## Output messages (stdout)

| type | fields |
|---|---|
| `ready` | `protocol`, `version`, `session_id`, `model`, `cwd`, `tools`, `resumed` |
| `turn_start` | `turn`, `prompt` |
| `activity` | `message` (what the spinner would say: thinking, running a tool, compacting) |
| `text_delta` | `text` (a streamed fragment of the answer) |
| `assistant` | `text`, `final` (`false` for narration between tool calls, `true` for the complete streamed answer) |
| `tool_call` | `id`, `name`, `args`, `kind` (`read` \| `write` \| `shell` \| `meta`), `auto_approved`, `decision` (when answered) |
| `approval_request` | `id`, `name`, `args`, `kind`, `preview`, `options`, `timeout_s` |
| `scope_request` | `id`, `name`, `path`, `options` (`allow_once`, `allow_dir`, `allow_all`, `deny`, `abort`), `timeout_s`; the tool wants a path outside the workspace; answer with `approve` |
| `tool_result` | `name`, `ok`, `summary`, `error`, `content` (bounded) |
| `plan` | `steps` (from `update_plan`: `step`, `status`) |
| `turn_end` | `turn`, `status` (`done` \| `interrupted` \| `aborted` \| `max_steps` \| `error` \| `blocked`), `steps`, `model`, `usage`, `cost` (USD for this turn, `null` when a model's price is unknown), `cost_session`, `content`, `error` |
| `notice` | `message` (compaction, model fallback, step limit reached, approval timed out, note queued) |
| `command_output` | `name`, `output` |
| `error` | `message`, `hint`, `kind` (`protocol` \| `turn` \| `command` \| `setup`) |
| `heartbeat` | `busy`, `turn`, every 10 seconds |
| `bye` | `reason` |

## Rules

- Approval requests come from the same decision path as the REPL; `SCOOT_APPROVAL=yolo` still auto-approves, and the shell denylist still produces a request that must be answered.
- An unanswered request is denied after `SCOOT_APPROVAL_TIMEOUT` seconds (default 120) with a `notice`; the model sees a declined tool result and continues.
- Reaching `SCOOT_MAX_STEPS` emits a `notice` and a `turn_end` with `status: max_steps`; the caller decides whether to send another prompt.
- A `UserPromptSubmit` hook that blocks the prompt ends the turn at once with `status: blocked`.
- When no provider is ready (no key, local server down) the process emits one `error` with `kind: setup` and exits 1.
- Output is never coloured and contains no ANSI sequences.

## Example transcript

```
→ {"type": "prompt", "text": "List the files here with the list_dir tool and name them in one line."}
← {"type": "ready", "protocol": 1, "version": "0.4.0", "session_id": "20260906-101500-ab12", "model": "ollama/llama3.2", "cwd": "/tmp/e2e", "tools": ["edit_file", "list_dir", "open_editor", "read_file", "run_shell", "search", "update_plan", "write_file"], "resumed": false}
← {"type": "turn_start", "turn": 1, "prompt": "List the files here with the list_dir tool and name them in one line."}
← {"type": "approval_request", "id": "call_41b9e593c8", "name": "list_dir", "args": {"path": "."}, "kind": "read", "preview": "", "options": ["allow", "allow_tool", "allow_session", "deny", "abort"], "timeout_s": 120.0}
→ {"type": "approve", "id": "call_41b9e593c8", "decision": "allow"}
← {"type": "tool_call", "id": "call_41b9e593c8", "name": "list_dir", "args": {"path": "."}, "kind": "read", "auto_approved": false, "decision": "allow"}
← {"type": "activity", "message": "running list_dir…"}
← {"type": "tool_result", "name": "list_dir", "ok": true, "summary": "3 entries", "error": "", "content": "hello.py\nREADME.md\nstripe.png"}
← {"type": "text_delta", "text": "The files"}
← {"type": "text_delta", "text": " in the current directory are: hello.py, README.md, stripe.png"}
← {"type": "assistant", "text": "The files in the current directory are: hello.py, README.md, stripe.png", "final": true}
← {"type": "turn_end", "turn": 1, "status": "done", "steps": 2, "model": "ollama/llama3.2", "usage": {"prompt_tokens": 1070, "completion_tokens": 21, "total_tokens": 1091}, "content": "The files in the current directory are: hello.py, README.md, stripe.png", "error": ""}
→ {"type": "shutdown"}
← {"type": "bye", "reason": "shutdown"}
```
