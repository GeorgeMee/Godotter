#!/bin/bash
# remove_godotter.sh - 从系统路径中移除 godotter

set -e

TARGET_LINK="/usr/local/bin/godotter"
PROJECT_DIR="/home/Godots/Godotter"
WRAPPER_SCRIPT="$PROJECT_DIR/godotter.sh"

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}🧹 开始清理 godotter...${NC}"

# 删除软链接
if [ -L "$TARGET_LINK" ]; then
    rm "$TARGET_LINK"
    echo -e "${GREEN}✓ 已删除软链接: $TARGET_LINK${NC}"
else
    echo -e "${YELLOW}⚠ 软链接不存在: $TARGET_LINK${NC}"
fi

# 删除包装脚本
if [ -f "$WRAPPER_SCRIPT" ]; then
    rm "$WRAPPER_SCRIPT"
    echo -e "${GREEN}✓ 已删除包装脚本: $WRAPPER_SCRIPT${NC}"
else
    echo -e "${YELLOW}⚠ 包装脚本不存在: $WRAPPER_SCRIPT${NC}"
fi

echo ""
echo -e "${GREEN}✅ 清理完成！${NC}"
echo ""
echo "如需重新注册，请运行: $PROJECT_DIR/setup_godotter.sh"
