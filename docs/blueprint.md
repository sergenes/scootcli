# Blueprint: a minimal coding agent for the terminal

This is the smallest design that produces a dependable coding agent like scoot: a program that takes a task, asks a model for text or tool calls, runs the permitted calls, feeds the results back, and stops with an explicit outcome.
It is written to be followed on its own.
Nothing in it depends on scoot's code, and nothing requires a third-party library.
Scoot is one implementation; its `SPEC.md` describes the full product, this file describes what you need to build your own.

## 1. Constraints

- Standard library only.
  HTTP, TLS, JSON, subprocesses, and a terminal are all the platform has to offer, and they are enough.
  A dependency is a second codebase to audit and a second thing to break at install time.
- One process, one loop.
  The agent loop runs on the calling thread; a watcher thread may exist only to interrupt it.
  No daemon, no queue, no plugin marketplace.
- One file per concern.
  A transport, one adapter per wire protocol, the loop, one file per tool, approvals, sessions, the terminal front end.
  Adding a tool or a provider is adding a file, not editing the loop.
- Every limit is a number in one place: model calls per turn, bytes per read, characters per tool output, seconds per command, tokens before compaction.
- Every action is bounded, previewable, and interruptible, and every failure leaves the user's files as they were.

## 2. Components and their contracts

| Component | Owns | Contract |
| --- | --- | --- |
| Transport | One HTTP request or one SSE stream | Takes method, URL, headers, body, a cancel event; returns status and body, or yields lines followed by a status sentinel. Shuts the socket down on cancel. |
| Provider adapter | One wire protocol (Chat Completions, Responses, Messages, ...) | Takes messages, tool schemas, model, cancel event; returns `content`, `tool_calls` (each with an id, a name, and a JSON-object argument string), `finish_reason`, `usage`, and any opaque replay state. Maps the provider's errors to a small typed set. |
| Agent loop | One user turn | Calls the adapter, runs approved tools, appends results, stops with `done`, `incomplete`, `interrupted`, `aborted`, `max_steps`, or `error`. Knows nothing about the terminal. |
| Tool | One capability | Declares a name, a JSON schema, a risk class (`read`, `write`, `shell`), and the paths it touches. Offers a `preview` (a diff, the exact command) and a `run` that returns `ok`, bounded `content`, `error`, and a one-line `summary`. Never raises into the loop. |
| Approvals | The permission policy | Given a mode, a tool, its arguments, and the session's trust list, answers "run it" or "ask, and here is why". A denylisted shell command is always "ask". |
| Scope | Where file tools may go | The workspace root plus paths the user granted this session. Rebuilt, not carried, when the root changes. |
| Session store | Saved conversations | Saves and loads `{id, root, created, updated, model, messages, usage}` with secrets redacted and the file owner-only. Never stores or restores a permission grant. |
| Front end | The terminal, or a line-delimited JSON protocol | Shows progress and results, asks the approval and scope questions, delivers the cancel signal. The loop talks to it through a small interface; a headless front end implements the same interface. |

The front end interface is six calls: `activity(message, cancel_event)` as a context manager, `assistant(text)`, `approve(tool, args, ctx) -> decision`, `approve_scope(tool, path, ctx) -> decision`, `tool_result(name, result)`, and `stream(cancel_event)` for live tokens.
That is what keeps the loop testable with a fake model and a fake terminal.

## 3. One turn

1. Append the user's prompt to the conversation.
2. Build the request: a system prompt naming the workspace and the tools, the conversation so far, the tool schemas.
3. Call the model, streaming tokens to the front end when it supports that, and watching the cancel event.
4. Append the assistant message, tool calls and all, before running anything.
5. If `finish_reason` says the reply was cut off (`length`, or a stream that closed early), do not run its tool calls.
   Answer each with "not executed: the reply was cut off" and let the model retry once; then stop with `incomplete`.
6. For each tool call, in order: parse the arguments as a JSON object (anything else is answered with an error, not run with empty arguments); resolve its paths and ask the scope question if one is outside; apply the approval policy and ask if required; run it; append exactly one `tool` message for its id.
7. If the user aborts or interrupts, answer every remaining call in the batch with "not executed" and stop.
   A history with an unanswered tool call is rejected by every provider on the next request.
8. Without tool calls, the reply is the answer.
   Stop with `done`, or with `incomplete` if it was cut off.
9. Stop with `max_steps` after the configured number of model calls; the interactive front end may offer to continue.
10. Save the session.

The failure payload a tool sends back is the error line followed by whatever the tool captured.
A failing test's output is what the model needs next; `ERROR: exit 1` alone only invites a blind rerun.

## 4. The model side

- Streaming: read whatever bytes are available (`read1`, not a fixed-size `read`), decode with an incremental UTF-8 decoder so a character split across two reads stays whole, split into SSE lines, parse `data:` events.
- Completion: a stream that ends with no events is an error; an event that does not parse is an error; a stream that closes before the protocol's terminal event (no `finish_reason` and no `[DONE]`, no `response.completed`, no `message_delta`) yields `finish_reason = "incomplete"`.
  An output-limit stop wins over `tool_calls`: the calls may be truncated.
- Retries: transient errors (network, timeout, 429, 5xx) retry a fixed number of times with capped, jittered backoff, never after streamed text has reached the screen.
  A 401 fails at once with a hint naming the key.
- Replay state (encrypted reasoning, signed blocks) rides along on the assistant message and goes back only to the provider and model that produced it.
- Cancel: a watcher thread calls `shutdown` and then `close` on the socket the moment the cancel event is set.
  `close` alone does not wake a blocked read on every platform.

## 5. The tools

| Tool | Essential behaviour |
| --- | --- |
| `read_file` | A regular file, at most N bytes, optional line range, numbered lines, truncation disclosed. |
| `list_dir` | A bounded listing, no external symlink targets. |
| `search` | ripgrep when present, a stdlib walk otherwise; grouped per file, bounded; an error is not "no matches". |
| `write_file` | Requires a string `content`; a missing one is an error, never an emptied file. Previews a diff. |
| `edit_file` | Exact, unique `old_string`; refuses a file that is not UTF-8; matches on LF-normalised text and writes the file's own line endings back. Previews a diff. |
| `run_shell` | Shows the exact command and directory; runs with stdin closed and pagers disabled; bounded output and lifetime; the exit code and both streams come back. |
| `update_plan` | Records a step checklist for the front end; no side effects, never prompts. |

Rules that apply to all of them:

- Validate argument presence and type before the preview, not in the middle of the run.
- Write through a temporary sibling file, fsync, and an atomic rename that preserves the mode.
  A failed or interrupted write leaves the old file intact.
  Refuse the replacement when the file changed after it was read.
- Start every child process in its own session and signal the whole process group on cancel and timeout, with bounded waits and a monotonic deadline.
  A background grandchild must not keep the call blocked after the shell is gone.
- Bound what you keep: read at most the limit plus one byte, truncate captured output, and say so.

## 6. Permissions

- Modes: `always` asks for every call; `auto-read` runs read-only tools; `auto-edits` also runs writes; `yolo` runs everything.
  Pick your default deliberately and document it.
- The prompt offers: approve once, trust this tool for the session, approve everything this session, edit the arguments, skip this call, abort the turn.
  Every answer is bound to the request it was asked for; a missing, empty, or late answer is a denial.
- A short denylist of catastrophic shell commands (recursive deletes of root paths, force pushes, piping a download into a shell, disk formatting) is confirmed in every mode.
  Normalise obvious spellings (`rm -r -f`, `git -C x push --force`).
  Call it what it is: an accident guard, not a sandbox.
  An approved command runs with the user's normal OS access.
- File tools are scoped to the workspace plus what the user granted.
  A path outside is not refused: the user is asked once, and may allow the path, its directory, or anywhere for the session.
  Resolve paths before checking them, do not follow symlinks out of the workspace in listings and searches.
- Switching workspace (a git worktree, `--root`) rebuilds the scope and re-reads the hooks; grants do not travel.
- Resume restores the conversation, never the approval mode or a grant.
- Executable configuration found in a repository (hooks, endpoint overrides, approval defaults) needs an explicit trust decision from the user before it takes effect.
  A repository must not be able to redirect an authenticated request or widen permissions by being cloned.

## 7. Sessions

- Save after every turn: id, workspace root, timestamps, model, messages, usage.
  Owner-only file, redacted key-shaped strings, the N most recent kept.
- An id is a basename: validate it at every entry point (save, load, list, delete) so a user-supplied id cannot reach a sibling file.
- Skip a malformed record when listing; one bad file must not stop the others from loading.
- On load, give every tool call without a result a placeholder result.
- Compact when the estimated context passes the threshold: the model summarises the older part, recent turns stay verbatim.

## 8. Stopping

One cancel event, set by ESC in the terminal or an `interrupt` message in the protocol, checked in four places: before each model call, inside the transport, inside the subprocess wait, and between tool calls.
Each place stops within a stated bound and leaves the conversation valid.
Hooks, if you add them, run through the same subprocess runner and honour the same event.

## 9. Output and exit codes

- Interactive: the answer, then the prompt.
  A cut-off reply is shown with a warning, not as the answer.
- One-shot: the answer on stdout, diagnostics on stderr, exit 0 on `done`, 1 otherwise.
- `--json`: exactly one object, `{status, model, steps, content, error, usage}`, on every exit path including the failures; nothing else on stdout.
- Headless: one JSON object per line in and out, request ids on every approval and scope question, a `turn_end` with the status.
- A single-file build (a zipapp, a shell wrapper) must exit with the same status as the installed module.

## 10. Build order

1. Transport and one adapter against a fake server; the loop against a fake model; `read_file`, `list_dir`, `search`; a plain `input()` prompt.
   Ship this: it already answers questions about a codebase.
2. `write_file` and `edit_file` with previews and atomic writes; the approval prompt; the scope question.
3. `run_shell` with the process-group runner, the denylist, and failure diagnostics.
4. Sessions: save, resume, compact.
5. Cancellation end to end, `incomplete` handling, complete tool-result history.
6. One-shot and `--json`; then headless if an editor or a script needs it.
7. Only when a real task asks for it: a second adapter, images, hooks, a status bar, a fixed input dock, worktree isolation.

## 11. Acceptance

Turn each line into a test that fails before the behaviour exists:

- A complete answer stops the turn; a cut-off reply and an exhausted step limit are distinct outcomes.
- Every tool-call id has a result after abort, interrupt, save, and resume.
- A missing `content` is rejected, unrelated bytes and line endings are preserved, a failed write leaves the old file intact.
- A failing command's output reaches the model.
- Cancellation stops the process tree and unblocks a stalled read within the bound.
- An empty or malformed stream is an error; a split character survives; a short event arrives at once.
- Resume with an explicit safer approval mode stays in that mode.
- Entering another workspace drops the old grants and puts the old root out of scope.
- A repository's own configuration cannot redirect a request or enable executable hooks without a trust decision.
- `--json` parses on success and on failure; the single-file build exits non-zero on a handled error.
- A malformed saved session does not stop the others from loading.

## What to leave out

Multi-agent orchestration, web browsing, vector memory, a plugin system, a daemon, a graphical interface.
None of them makes the loop above more dependable, and each one is a maintenance surface larger than the agent itself.
The first useful version should fit in one maintainer's head.
