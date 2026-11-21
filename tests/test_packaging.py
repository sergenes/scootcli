"""The README as PyPI shows it: PyPI resolves a relative link against pypi.org, where it is a 404."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import scootcli

ROOT = Path(__file__).resolve().parent.parent
_LINK = re.compile(r"\]\(([^)\s]+)\)")


def _setup_module():
    spec = importlib.util.spec_from_file_location("scoot_setup", ROOT / "setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # guarded: importing it does not run setup()
    return module


def test_pypi_readme_has_no_relative_links():
    text = _setup_module().pypi_readme()
    targets = _LINK.findall(text)
    assert targets, "the README should contain links"
    relative = [t for t in targets if not t.startswith(("https://", "http://", "#", "mailto:"))]
    assert relative == []


def test_repo_files_are_linked_at_the_release_tag():
    text = _setup_module().pypi_readme()
    base = f"https://github.com/sergenes/scootcli/blob/v{scootcli.__version__}/"
    for path in ("LICENSE", "ROADMAP.md", "SPEC.md", "CHANGELOG.md", "DESIGN.md", ".env.example",
                 "docs/headless-protocol.md", "docs/blueprint.md"):
        assert f"]({base}{path})" in text, path
    assert "](./" not in text


def test_every_rewritten_link_points_at_a_file_in_the_repo():
    text = _setup_module().pypi_readme()
    base = f"https://github.com/sergenes/scootcli/blob/v{scootcli.__version__}/"
    for target in _LINK.findall(text):
        if target.startswith(base):
            assert (ROOT / target[len(base):].split("#")[0]).exists(), target


def test_readme_on_github_keeps_its_relative_links():
    assert "](./LICENSE)" in (ROOT / "README.md").read_text()


def test_setup_version_matches_the_package():
    assert _setup_module().version() == scootcli.__version__
