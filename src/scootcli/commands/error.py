"""/error: show the latest error of this session (kept across compaction and restarts)."""

from __future__ import annotations

import time

from ..rendering import color, strip_controls
from . import register
from .base import SlashCommand


def _run(session, args: str):
    from .. import diagnostics

    record = diagnostics.load(session.id) or getattr(session, "error_record", None)
    if not record:
        print(color("no error recorded for this session.", "gray"))
        return
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.get("time") or 0))
    facts = [str(record.get("kind") or "Error")]
    if record.get("status"):
        facts.append(f"HTTP {record['status']}")
    if record.get("code"):
        facts.append(f"code {record['code']}")
    if record.get("errno") is not None:
        facts.append(f"errno {record['errno']}")
    print(color(strip_controls(" · ".join(facts)), "red") + color(f"  {when}", "gray"))
    if record.get("message"):
        print("  " + strip_controls(str(record["message"])))
    if record.get("hint"):
        print(color("  hint: " + strip_controls(str(record["hint"])), "yellow"))
    meta = [f"model {record['model']}" if record.get("model") else "",
            f"scoot {record['scoot']}" if record.get("scoot") else "",
            ("caused by " + " ← ".join(str(c) for c in record["causes"])) if record.get("causes") else ""]
    meta = [m for m in meta if m]
    if meta:
        print(color("  " + strip_controls(" · ".join(meta)), "gray"))
    frames = [f for f in (record.get("frames") or []) if isinstance(f, dict)]
    if frames:
        print(color("  where (innermost last):", "gray"))
        for f in frames:
            print(color(strip_controls(f"    {f.get('file', '?')}:{f.get('line', '?')} in {f.get('func', '?')}"), "gray"))


register(SlashCommand("error", "show this session's latest error", _run))
