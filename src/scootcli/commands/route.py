"""/route — show how the ``auto`` model alias routes: rule source, rules, classifier, last decision."""

from __future__ import annotations

from ..providers.router import Router, config_path, describe
from ..rendering import color
from . import register
from .base import SlashCommand


def _run(session, args: str):
    router = getattr(session, "router", None) or Router()
    print(color("routing (for /model auto):", "bold"))
    for line in describe(router):
        print("  " + line)
    if session.model.lower() != "auto":
        print(color(f"  auto is off; the preference is '{session.model}'. Turn it on with /model auto.", "gray"))
    print(color(f"  edit rules in {config_path()} (see README: Routing)", "gray"))


register(SlashCommand("route", "show how 'auto' picks a model", _run))
