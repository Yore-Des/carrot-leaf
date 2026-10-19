#!/bin/sh
# Carrot Leaf installer for macOS and Linux.
#
#   curl -LsSf https://raw.githubusercontent.com/Yore-Des/carrot-leaf/main/install.sh | sh
#
# Installs uv (https://docs.astral.sh/uv/) if it's missing, then installs Carrot Leaf
# into its own environment with its own Python, so the system Python doesn't matter.
# Set CARROT_LEAF_SOURCE to install from somewhere else, e.g.
#   CARROT_LEAF_SOURCE=git+https://github.com/Yore-Des/carrot-leaf sh install.sh
# Set CARROT_LEAF_NO_MODIFY_PATH=1 to leave your shell profile alone.
# Set CARROT_LEAF_NO_FFMPEG=1 to skip downloading ffmpeg now (Carrot Leaf gets it on first use).
set -eu

SOURCE="${CARROT_LEAF_SOURCE:-carrot-leaf}"
orange() { printf '\033[38;5;208m%s\033[0m\n' "$1"; }
green() { printf '\033[38;5;113m%s\033[0m\n' "$1"; }

orange "  \\|/  Carrot Leaf installer"

if ! command -v uv >/dev/null 2>&1; then
    echo "→ Installing uv (Python package manager)…"
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        echo "Need curl or wget to continue." >&2
        exit 1
    fi
    # uv installs to ~/.local/bin (or $XDG_BIN_HOME); make it visible for the rest of this script.
    PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:$HOME/.cargo/bin:$PATH"
    export PATH
fi

echo "→ Installing Carrot Leaf…"
uv tool install --python 3.12 --upgrade "$SOURCE"
if [ -z "${CARROT_LEAF_NO_MODIFY_PATH:-}" ]; then
    uv tool update-shell >/dev/null 2>&1 || true
fi

if [ -z "${CARROT_LEAF_NO_FFMPEG:-}" ]; then
    echo "→ Getting ffmpeg…"
    BIN_DIR="$(uv tool dir --bin 2>/dev/null || echo "${XDG_BIN_HOME:-$HOME/.local/bin}")"
    "$BIN_DIR/carrot" setup || echo "  (Carrot Leaf will fetch ffmpeg the first time it needs it.)"
fi

green "✓ Done. Run:  carrot"
if ! command -v carrot >/dev/null 2>&1; then
    echo "  (Open a new terminal first so 'carrot' is on your PATH.)"
fi
