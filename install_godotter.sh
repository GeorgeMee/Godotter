#!/bin/bash
# install_godotter.sh - register Godotter command wrappers into the system PATH

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
INSTALL_BIN_DIR="${INSTALL_BIN_DIR:-/usr/local/bin}"

if ! command -v uv >/dev/null 2>&1; then
    echo "Error: uv is not installed or not on PATH."
    exit 1
fi

mkdir -p "$INSTALL_BIN_DIR"

register_command() {
    local command_name="$1"
    local wrapper_script="$PROJECT_DIR/${command_name}.sh"
    local target_link="$INSTALL_BIN_DIR/$command_name"

    cat > "$wrapper_script" <<EOF
#!/bin/bash
set -euo pipefail
cd "$PROJECT_DIR"
exec uv run $command_name "\$@"
EOF
    chmod +x "$wrapper_script"

    if [ -L "$target_link" ] || [ -f "$target_link" ]; then
        rm -f "$target_link"
    fi
    ln -s "$wrapper_script" "$target_link"
    echo "Registered $command_name -> $target_link"
}

register_command "gdt"
register_command "godotter"

echo "Human CLI: gdt --help"
echo "Machine CLI: godotter --help"
