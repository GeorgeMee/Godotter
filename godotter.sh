#!/bin/bash
# godotter.sh - Godotter CLI 包装脚本
# 自动生成，请勿手动修改

cd "/home/Godots/Godotter" || exit 1
exec uv run godotter "$@"
