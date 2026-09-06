#!/usr/bin/env bash
# scoot installer: downloads the single-file zipapp from the latest GitHub release and puts it on PATH.
#
#   curl -fsSL https://raw.githubusercontent.com/sergenes/scootcli/main/install.sh | bash
#
# Options (environment variables):
#   SCOOT_VERSION=v0.2.0       install a specific release instead of the latest
#   SCOOT_INSTALL_DIR=~/bin    where to put the `scoot` command (default: ~/.local/bin)
#
# Needs: curl and Python 3.9 or newer. Nothing else: scoot has no dependencies.
# Prefer `pipx install scootcli` if you already use pipx; this script is for machines without it.
set -euo pipefail

REPO="sergenes/scootcli"
INSTALL_DIR="${SCOOT_INSTALL_DIR:-$HOME/.local/bin}"
VERSION="${SCOOT_VERSION:-latest}"

say()  { printf '%s\n' "$*"; }
fail() { printf 'scoot install: %s\n' "$*" >&2; exit 1; }

# ── Python 3.9+ ────────────────────────────────────────────────────────────────
PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 \
       && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        PY="$candidate"; break
    fi
done
[ -n "$PY" ] || fail "Python 3.9 or newer is required (python3 not found or too old). Install it from https://www.python.org/downloads/ or your package manager, then rerun."
command -v curl >/dev/null 2>&1 || fail "curl is required."

# ── download ───────────────────────────────────────────────────────────────────
if [ "$VERSION" = "latest" ]; then
    URL="https://github.com/$REPO/releases/latest/download/scoot.pyz"
else
    URL="https://github.com/$REPO/releases/download/$VERSION/scoot.pyz"
fi
TMP="$(mktemp -t scoot.XXXXXX)"
trap 'rm -f "$TMP"' EXIT
say "→ downloading scoot ($VERSION) from $URL"
curl -fsSL --retry 3 -o "$TMP" "$URL" || fail "download failed (is the release '$VERSION' published?)"

# ── verify before installing ───────────────────────────────────────────────────
VER="$("$PY" "$TMP" --version 2>/dev/null)" || fail "the downloaded file did not run with $PY; refusing to install it"

# ── install ────────────────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
install -m 0755 "$TMP" "$INSTALL_DIR/scoot"
say "✔ installed $VER → $INSTALL_DIR/scoot"

case ":$PATH:" in
    *":$INSTALL_DIR:"*) ;;
    *)
        case "$(basename "${SHELL:-sh}")" in
            zsh)  RC="$HOME/.zshrc" ;;
            fish) RC="$HOME/.config/fish/config.fish" ;;
            *)    RC="$HOME/.bashrc" ;;
        esac
        say ""
        say "  $INSTALL_DIR is not on your PATH yet. Add it for new shells:"
        if [ "$(basename "${SHELL:-sh}")" = "fish" ]; then
            say "    fish_add_path $INSTALL_DIR"
        else
            say "    echo 'export PATH=\"$INSTALL_DIR:\$PATH\"' >> $RC && source $RC"
        fi
        if [ "$INSTALL_DIR" = "$HOME/.local/bin" ] && [ "$(uname -s)" = "Linux" ]; then
            say "  (on Debian, Ubuntu, and Pop!_OS a new login shell picks ~/.local/bin up by itself now that it exists)"
        fi
        ;;
esac

say ""
say "  next: scoot auth set openai   (or: ollama pull llama3.2 && scoot --model ollama/llama3.2)"
say "        scoot                   opens the REPL in the current directory"
