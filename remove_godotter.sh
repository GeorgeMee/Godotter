#!/bin/bash
# remove_godotter.sh - remove godotter from the system PATH

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
WRAPPER_SCRIPT="$PROJECT_DIR/godotter.sh"
INSTALL_BIN_DIR="${INSTALL_BIN_DIR:-/usr/local/bin}"
TARGET_LINK="$INSTALL_BIN_DIR/godotter"

if [ -L "$TARGET_LINK" ] || [ -f "$TARGET_LINK" ]; then
    rm -f "$TARGET_LINK"
    echo "Removed $TARGET_LINK"
else
    echo "No installed godotter link found at $TARGET_LINK"
fi

if [ -f "$WRAPPER_SCRIPT" ]; then
    rm -f "$WRAPPER_SCRIPT"
    echo "Removed generated wrapper $WRAPPER_SCRIPT"
fi
