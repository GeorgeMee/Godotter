#!/bin/bash
# uninstall_godotter.sh - remove Godotter command wrappers from the system PATH

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
INSTALL_BIN_DIR="${INSTALL_BIN_DIR:-/usr/local/bin}"

remove_command() {
    local command_name="$1"
    local wrapper_script="$PROJECT_DIR/${command_name}.sh"
    local target_link="$INSTALL_BIN_DIR/$command_name"

    if [ -L "$target_link" ] || [ -f "$target_link" ]; then
        rm -f "$target_link"
        echo "Removed $target_link"
    else
        echo "No installed $command_name link found at $target_link"
    fi

    if [ -f "$wrapper_script" ]; then
        rm -f "$wrapper_script"
        echo "Removed generated wrapper $wrapper_script"
    fi
}

remove_command "gdt"
remove_command "godotter"
