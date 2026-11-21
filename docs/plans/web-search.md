# Plan: a web_search tool (SearXNG)

Status: released in 0.15.0 (2026-11-20), live-tested the same day; the agents-at-work bridge has been updated and deployed for it.
This note is the design and threat model for `web_search`, so its effect on anything that drives scoot (in particular the agents-at-work bridge) can be checked before release.

## Why SearXNG

Search is the "demand" the web-fetch note deferred to. Of the options, SearXNG was chosen because it needs no API key and no third-party account: the user runs a SearXNG instance and scoot points at its JSON API, the same local-and-configurable shape as Ollama for models. No vendor lock-in, no key to store, and the query goes only to an instance the user controls.

## Shape

- A single tool, `web_search(query, count)`, returning a short list of results (title, URL, snippet).
- It reaches `SCOOT_SEARCH_URL` + `/search?q=...&format=json&safesearch=1` through scoot's existing stdlib transport (so the proxy and cancellation apply); no new dependency, no SDK.
- Results are bounded: at most 10 (default 5), each snippet capped, the whole output truncated like any tool output.

## Gating and safety

`web_search` is treated like the planned `fetch_url`, because it has the same surface:

- **Off by default.** The tool is registered only when `SCOOT_SEARCH_URL` is set; with no instance configured it does not appear in the tool list at all. `load_builtins` checks the environment (fixed at startup) each time it runs.
- **No session-wide approval.** The REPL prompt has two choices for every tool since 0.15.0, `approve [a]` and `skip [s]` (ESC stops the turn); headless `approval_request.options` are `allow`, `deny`, `abort`. A session-wide answer sent anyway approves that one call, with a notice, and never trusts the tool or switches the mode to `yolo` (enforced in the agent loop, not only in the UIs).
- **Never auto-approved, even in `yolo`.** The tool sets `always_confirm = True`, which `approvals.needs_prompt` honors before the mode and trust checks. Every search asks, and the approval prompt shows the exact query. This is because the query is data egress (it could carry a secret) and the results are untrusted web content.
- **Results are untrusted.** The tool output is prefixed as untrusted web content, and the system prompt's existing "tool output is not an instruction" stance covers it; the model must still get approval for anything it does with what it reads.
- **A repository cannot redirect it.** `SCOOT_SEARCH_URL` is on the project `.env` blocklist (R02), so only the user's global config or environment can set the search endpoint; a cloned repo cannot point searches at a host of its choosing.

## Non-goals

- No hosted search provider and no API key in this step; SearXNG only. A hosted backend (Brave, Tavily) could be added later as a second option behind the same tool.
- No fetching of result pages; `web_search` returns snippets and URLs. Reading a page is the separate, still-unbuilt `fetch_url` (`docs/plans/web-fetch.md`).
- No auto-approve and no session-wide trust for web access.

## What the agents-at-work agent should verify

- With `SCOOT_SEARCH_URL` unset (the default), `web_search` is absent from the tool list, so nothing changes for the bridge.
- With it set, a `web_search` call produces a normal `approval_request` / `tool_call` that the bridge relays like any other approval; the phone approves or denies. It is never auto-approved, even in `yolo`, so it cannot run unattended without the phone answering. If the bridge runs unattended with no approver, treat `web_search` as effectively unavailable (every call waits for approval), which is the intended safety behavior.
- No new headless protocol event or version bump; `web_search` is an ordinary tool.

## Acceptance

- Absent from the tool list unless `SCOOT_SEARCH_URL` is set.
- Every call prompts, in every approval mode.
- A project `.env` setting `SCOOT_SEARCH_URL` is ignored and reported.
- Results are parsed from SearXNG JSON, bounded, and labeled untrusted; a missing/invalid response and an unconfigured URL fail with a clear message; ESC cancels an in-flight search.

## How to test by hand

Written 2026-11-18 for a manual pass before releasing 0.15.0.
The automated suite covers the parsing and gating (`python -m pytest -q`, 422 passing); this is the live check against a real SearXNG.

### 1. Start a local SearXNG with the JSON API on

SearXNG ships with only the HTML format enabled, and scoot needs JSON, so the first start is followed by one settings edit.

```bash
mkdir -p ~/searxng
docker run -d --name searxng -p 8888:8080 -v ~/searxng:/etc/searxng searxng/searxng
```

The first start creates `~/searxng/settings.yml` with only `use_default_settings: true` and a `server:` block.
It has no `search:` section, so append one:

```yaml
search:
  formats:
    - html
    - json
```

```bash
docker restart searxng
curl -s 'http://localhost:8888/search?q=python&format=json' | head -c 300
```

The `curl` must print JSON with a `results` list.
A 403 means the `formats` edit did not take; scoot reports the same case as `SearXNG returned HTTP 403; enable its JSON API ...`.

### 2. Off by default

Run from the repo, with the variable unset:

```bash
unset SCOOT_SEARCH_URL
PYTHONPATH=src python3 -m scootcli
```

- Ask: `which tools do you have?`
  `web_search` must not be in the list.

### 3. On, and always asking

```bash
SCOOT_SEARCH_URL=http://localhost:8888 PYTHONPATH=src python3 -m scootcli
```

- Ask: `search the web for the latest SearXNG release and give me the links`.
  An approval prompt must appear, showing `search http://localhost:8888 for: <the query>`.
- Approve it.
  The result must be a numbered list of titles, URLs, and snippets, at most 5, and the model's answer must use them.
- Run `/yolo` (or start with `--approval yolo`) and ask for another search.
  The prompt must still appear.
- Check the prompt's keys: `approve [a]  skip [s]` and nothing else.
  Pressing `t`, `A`, `e`, or `q` must do nothing but show the prompt again; ESC stops the turn.
- Skip one search at the prompt.
  The turn must continue without results and without an error.
- Ask for `20 results`.
  No more than 10 may come back.

### 4. Failure paths

- **Instance down:** `docker stop searxng`, then ask for a search and approve it.
  The tool must fail with a clear `search failed: ...` message and the session must stay usable.
  Start it again with `docker start searxng`.
- **JSON disabled:** remove `json` from `formats`, restart the container, search again.
  The failure must be `SearXNG returned HTTP 403` with the hint to add `json` to `search.formats`.
  Put `json` back afterwards.
- **ESC:** point `SCOOT_SEARCH_URL` at a server that accepts the connection and never answers (`nc -lk 8899` in another terminal, then `http://127.0.0.1:8899`), approve a search, and press ESC.
  The search must stop at once with `interrupted`.
  An unroutable host (for example `http://10.255.255.1`) is a different case: see the open finding below.

### 5. A repository cannot redirect it

```bash
REPO="$PWD"   # run this line from the scootcli checkout
mkdir -p /tmp/scoot-search-test && cd /tmp/scoot-search-test
echo 'SCOOT_SEARCH_URL=http://evil.example' > .env
unset SCOOT_SEARCH_URL
PYTHONPATH="$REPO/src" python3 -m scootcli
```

- scoot must report that the project `.env` tried to set `SCOOT_SEARCH_URL` and that it was ignored (at start or in `/status`).
- `web_search` must be absent from the tool list.
- With `SCOOT_SEARCH_URL=http://localhost:8888` exported in the shell, the approval prompt must show `localhost:8888`, never `evil.example`.

### 6. Machine interfaces

- **Headless:** `SCOOT_SEARCH_URL=http://localhost:8888 PYTHONPATH=src python3 -m scootcli --headless`, send a prompt that needs a search.
  An `approval_request` for `web_search` must be emitted and the search must wait for the `approve` answer, in every approval mode.
- **One-shot JSON:** `SCOOT_SEARCH_URL=http://localhost:8888 PYTHONPATH=src python3 -m scootcli --json "search the web for X"`.
  Stdout must be exactly one JSON object; the search is declined (it cannot ask), with the notice on stderr.
- **agents-at-work bridge:** the three checks in "What the agents-at-work agent should verify" above.

### 7. Clean up

```bash
docker rm -f searxng
rm -rf /tmp/scoot-search-test
```

Keep `~/searxng` if the instance will be reused.

## Live pass, 2026-11-20

Run against `searxng/searxng:latest` in Docker on macOS, with `anthropic/claude-haiku-5-5`, through headless mode, one-shot `--json`, and the REPL in a pseudo-terminal.

Passed:

- Absent from the tool list with `SCOOT_SEARCH_URL` unset; present with it set.
- A real search returns titled, linked, snippet-capped results marked untrusted, and the model answers from them.
- Every search asked for approval in `yolo`, and again after `allow_tool` and after `allow_session`.
- `count: 20` returned 10 results.
- A denied search is skipped and the turn continues.
- Instance down: `search failed: [Errno 61] Connection refused`, and the session stays usable.
- JSON format disabled: `SearXNG returned HTTP 403`.
- Interrupt during an in-flight search (connected, no answer yet) stops it within the watcher's poll.
- A project `.env` setting `SCOOT_SEARCH_URL` is ignored with a notice, the tool stays absent, and an exported value wins.
- One-shot `--json`: one JSON object on stdout, the search declined, the notice on stderr.
- The REPL shows `search <url> for: <query>` above the approval keys, and an approved search runs and answers.

Fixed during the pass:

- The 403 from a default SearXNG now says to add `json` to `search.formats` (`test_web_search_403_says_to_enable_the_json_format`).

- The approval prompt offered `trust web_search [t]` and `all-session [A]` (headless: `allow_tool`, `allow_session`), which the tool ignored while `A` could still switch the session to `yolo`.
  They are no longer honoured for an always-confirm tool, and the REPL prompt became `approve [a]  skip [s]` for every tool; re-checked live in headless and in the REPL.

Open findings, both planned for 0.16.0:

- **Interrupt does not cancel the connect phase.**
  With an unroutable search host, the interrupt was honored only after the 30 s connect timeout.
  This is in `transport.py` (the watcher can only shut down a socket that already exists), so it applies to every provider request too, not just `web_search`.
- **No list price for `anthropic/claude-haiku-5-5` or `openai/chat-latest`**, so `cost` is `null` for both.

Not covered in this pass: a real terminal (iTerm2, tmux) by eye.
The agents-at-work bridge was checked and updated separately by its own agent.
