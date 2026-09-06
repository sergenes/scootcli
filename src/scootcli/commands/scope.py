"""/scope — where file tools may go: the workspace, plus what you allowed, or anywhere."""

from __future__ import annotations

from ..rendering import color, eprint
from . import register
from .base import SlashCommand


def _run(session, args: str):
    scope = getattr(session, "scope", None)
    if scope is None:
        eprint(color("scope is not available in this context.", "yellow"))
        return
    want = (args or "").strip().lower()
    if want in ("anywhere", "all", "everywhere"):
        scope.grant_all()
        print(color("file tools may now go anywhere on this machine for this session.", "gray"))
    elif want == "workspace":
        scope.everything = False
        scope.granted.clear()
        print(color("back to the workspace only; the next access outside it will ask.", "gray"))
    elif want:
        scope.grant_dir(want)
        print(color(f"allowed {scope.granted[-1]} for this session.", "gray"))
    print(color("scope: ", "bold") + scope.describe())
    for g in scope.granted:
        print(f"  {color('+', 'green')} {g}")
    print(color("  usage: /scope anywhere · /scope workspace · /scope <dir>   (also --scope, SCOOT_SCOPE)", "gray"))


register(SlashCommand("scope", "where file tools may go (workspace | anywhere | <dir>)", _run,
                      usage="[anywhere|workspace|<dir>]"))
