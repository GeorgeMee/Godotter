#!/bin/bash
# setup_godotter.sh - register godotter into the system PATH

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
WRAPPER_SCRIPT="$PROJECT_DIR/godotter.sh"
INSTALL_BIN_DIR="${INSTALL_BIN_DIR:-/usr/local/bin}"
TARGET_LINK="$INSTALL_BIN_DIR/godotter"

if ! command -v uv >/dev/null 2>&1; then
    echo "Error: uv is not installed or not on PATH."
    exit 1
fi

mkdir -p "$INSTALL_BIN_DIR"

cat > "$WRAPPER_SCRIPT" <<EOF
#!/bin/bash
set -euo pipefail
cd "$PROJECT_DIR"
exec uv run godotter "\$@"
EOF
chmod +x "$WRAPPER_SCRIPT"

if [ -L "$TARGET_LINK" ] || [ -f "$TARGET_LINK" ]; then
    rm -f "$TARGET_LINK"
fi
ln -s "$WRAPPER_SCRIPT" "$TARGET_LINK"

echo "Registered godotter -> $TARGET_LINK"
echo "Run: godotter --help"
