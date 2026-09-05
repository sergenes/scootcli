"""update_plan — let the model publish/refresh a step-by-step plan for a multi-step task.

This is a **meta tool**: it has no filesystem or shell side effects, so it is always auto-approved.
It normalizes the submitted plan and hands it back via ``ToolResult.meta['plan']``; the agent loop
stores it on the session and renders it (checkbox list) so the user can follow long tasks.
"""

from __future__ import annotations

from typing import List

from . import register
from .base import Tool, ToolContext, ToolResult

_STATUSES = ("pending", "in_progress", "completed")
_MAX_STEPS = 20


def normalize_plan(raw) -> List[dict]:
    """Coerce arbitrary input into a list of ``{"step": str, "status": <known>}`` (bounded)."""
    plan: List[dict] = []
    if not isinstance(raw, list):
        return plan
    for item in raw[:_MAX_STEPS]:
        if isinstance(item, str):
            step, status = item, "pending"
        elif isinstance(item, dict):
            step = item.get("step") or item.get("title") or item.get("name") or ""
            status = item.get("status", "pending")
        else:
            continue
        step = str(step).strip()
        if not step:
            continue
        status = str(status).strip().lower().replace("-", "_").replace(" ", "_")
        if status in ("done", "complete", "finished"):
            status = "completed"
        elif status in ("active", "current", "doing", "inprogress"):
            status = "in_progress"
        if status not in _STATUSES:
            status = "pending"
        plan.append({"step": step, "status": status})
    return plan


def plan_progress(plan: List[dict]) -> "tuple[int, int]":
    """Return ``(completed, total)`` for a normalized plan."""
    total = len(plan)
    done = sum(1 for s in plan if s.get("status") == "completed")
    return done, total


class UpdatePlan(Tool):
    name = "update_plan"
    description = (
        "Publish or update a short step-by-step plan for a multi-step task, and mark progress. "
        "Call it when starting a multi-step task and again whenever a step's status changes. "
        "Each item is {step, status} with status one of: pending, in_progress, completed. "
        "Keep exactly one step in_progress. Skip this for trivial one-step requests."
    )
    risk = "read"
    auto_approve = True
    parameters = {
        "type": "object",
        "properties": {
            "plan": {
                "type": "array",
                "description": "The full ordered plan (resubmit the whole list each update).",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "string", "description": "Short imperative step description."},
                        "status": {
                            "type": "string",
                            "enum": list(_STATUSES),
                            "description": "pending | in_progress | completed",
                        },
                    },
                    "required": ["step", "status"],
                },
            },
            "explanation": {
                "type": "string",
                "description": "Optional one-line note about what changed.",
            },
        },
        "required": ["plan"],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        plan = normalize_plan(args.get("plan"))
        if not plan:
            return ToolResult.fail("plan must be a non-empty list of {step, status} items")
        done, total = plan_progress(plan)
        return ToolResult(
            ok=True,
            content=f"Plan updated: {done}/{total} steps completed.",
            summary=f"{done}/{total} steps",
            meta={"plan": plan, "explanation": str(args.get("explanation") or "").strip()},
        )


register(UpdatePlan())

