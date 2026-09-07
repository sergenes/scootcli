# Optional: accept Claude Code's exact PreToolUse hook output

**Status:** done 2026-09-06 (implemented as proposed, plus `additionalContext` under `hookSpecificOutput`; tests in `tests/test_hooks.py`). Kept for the reasoning.

**Original status:** optional, not a blocker. Requested by the Agents At Work integration (a separate
product that bridges scoot and other CLI agents to a phone). scoot itself stays unaware of
Agents At Work; this is a general hook-compatibility improvement.

**One-line ask:** in `src/scootcli/hooks.py`, `Hooks._interpret`, also read the PreToolUse
permission decision when it arrives nested under `hookSpecificOutput`, the shape Claude Code
emits, and treat `"approve"` there as `"allow"`.

## Why

A hook script that wants to allow or deny a tool call can already answer scoot in three shapes
that `_interpret` understands today:

- `{"permissionDecision": "allow" | "deny" | "ask"}` (top level)
- `{"decision": "approve"}` → allow, `{"decision": "block"}` → deny
- exit code 2 → deny

Claude Code's PreToolUse hooks answer with a **fourth** shape that scoot does not read:

```json
{"hookSpecificOutput": {"permissionDecision": "allow" | "deny" | "ask", "permissionDecisionReason": "..."}}
```

A tool that emits Claude Code's exact output is a very common case: any hook framework written
for Claude Code (Agents At Work is one, but not the only one) produces this nested shape with
`"allow"`/`"deny"`, and Claude Code's own docs use `"approve"` as the allow synonym in the
older `{"decision": "approve"}` form. Because scoot only looks at the top level, such a hook is
silently treated as "no decision" (falls through to the `additionalContext` branch), so its
allow/deny is ignored and scoot falls back to its own approval prompt.

Making scoot accept the nested shape means **any Claude-Code-compatible hook works with scoot
unchanged**. For Agents At Work specifically, it lets the bridge reuse its existing Claude hook
path for scoot with zero scoot-specific branches, and every future hook it adds works for both
agents for free. Without this change the integration still works — the bridge just emits scoot's
native top-level shape from a small `scoot` branch — so this is polish, not a requirement.

## The change

File: `src/scootcli/hooks.py`, method `Hooks._interpret`.

Current:

```python
@staticmethod
def _interpret(event: str, data: dict) -> Decision:
    reason = str(data.get("reason") or "")
    perm = str(data.get("permissionDecision") or "").lower()
    if event == "PreToolUse" and perm in ("allow", "deny", "ask"):
        return Decision(action=perm, reason=reason)
    raw = str(data.get("decision") or "").lower()
    if raw == "block":
        return Decision(action="deny" if event == "PreToolUse" else "block", reason=reason)
    if raw == "approve" and event == "PreToolUse":
        return Decision(action="allow", reason=reason)
    context = data.get("additionalContext") or data.get("context") or ""
    return Decision(context=str(context) if context else "")
```

Proposed: unwrap `hookSpecificOutput` first, and accept `"approve"` as `"allow"` in the
permission field. Only the first few lines change.

```python
@staticmethod
def _interpret(event: str, data: dict) -> Decision:
    reason = str(data.get("reason") or "")
    # Claude Code nests the PreToolUse decision under hookSpecificOutput; accept that
    # shape as well as scoot's own top-level permissionDecision. reason may travel as
    # permissionDecisionReason there.
    hso = data.get("hookSpecificOutput")
    if isinstance(hso, dict):
        if not reason:
            reason = str(hso.get("permissionDecisionReason") or hso.get("reason") or "")
        perm_src = hso.get("permissionDecision")
    else:
        perm_src = data.get("permissionDecision")
    perm = str(perm_src or "").lower()
    if perm == "approve":
        perm = "allow"   # Claude Code's allow synonym
    if event == "PreToolUse" and perm in ("allow", "deny", "ask"):
        return Decision(action=perm, reason=reason)
    raw = str(data.get("decision") or "").lower()
    if raw == "block":
        return Decision(action="deny" if event == "PreToolUse" else "block", reason=reason)
    if raw == "approve" and event == "PreToolUse":
        return Decision(action="allow", reason=reason)
    context = data.get("additionalContext") or data.get("context") or ""
    return Decision(context=str(context) if context else "")
```

Notes for whoever implements it:

- Keep every existing branch working (top-level `permissionDecision`, `decision: block|approve`,
  exit 2, `additionalContext`). The change is additive: read the nested field when present, and
  fold `"approve"` into `"allow"` for the permission field.
- `hookSpecificOutput` is Claude Code's documented PreToolUse output key. Its
  `permissionDecision` values are `allow`, `deny`, `ask`; its reason key is
  `permissionDecisionReason`.
- No protocol version bump: this only widens what an existing hook may return.

## Test

Add a unit test alongside the existing hook tests: feed `_interpret("PreToolUse", …)` each of

- `{"hookSpecificOutput": {"permissionDecision": "allow"}}` → action `allow`
- `{"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "no"}}` →
  action `deny`, reason `no`
- `{"hookSpecificOutput": {"permissionDecision": "approve"}}` → action `allow`

and confirm the pre-existing cases (top-level `permissionDecision`, `decision: block`, exit 2)
still pass.

## If you skip it

The Agents At Work bridge will add a `scoot` branch to its own hook that emits scoot's native
`{"permissionDecision": "allow" | "deny", "reason": "…"}` at the top level, which `_interpret`
already understands. Nothing breaks; the bridge just carries one more agent-specific branch.
