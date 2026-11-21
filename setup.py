"""Build-time metadata that ``pyproject.toml`` cannot express: the README as PyPI shows it.

The README links to files in the repository with relative paths, which is right on GitHub. PyPI
renders the same text on pypi.org and resolves those paths there, so every one of them is a 404
(``pypi.org/project/scootcli/LICENSE``). The long description published to PyPI is therefore the
README with each relative link rewritten to the file on GitHub at this release's tag, so the page
for a version keeps pointing at that version's files.

Everything else lives in ``pyproject.toml``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = "https://github.com/sergenes/scootcli"

# A Markdown link or image target that is not already absolute, an in-page anchor, or a mail address.
_RELATIVE = re.compile(r"(\]\()(?!https?://|#|mailto:)([^)\s]+)(\))")
_IMAGE = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")


def version() -> str:
    text = (ROOT / "src" / "scootcli" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M).group(1)


def pypi_readme() -> str:
    tag = "v" + version()

    def absolute(match: "re.Match") -> str:
        path = match.group(2)
        while path.startswith("./"):
            path = path[2:]
        # an image must be served as the file itself, not as GitHub's page about the file
        kind = "raw" if path.split("#")[0].lower().endswith(_IMAGE) else "blob"
        return f"{match.group(1)}{REPO}/{kind}/{tag}/{path}{match.group(3)}"

    return _RELATIVE.sub(absolute, (ROOT / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    from setuptools import setup

    setup(long_description=pypi_readme(), long_description_content_type="text/markdown")
