#!/bin/bash
# setup_godotter.sh - 注册 godotter 到系统路径

set -e

PROJECT_DIR="/home/Godots/Godotter"
WRAPPER_SCRIPT="$PROJECT_DIR/godotter.sh"
TARGET_LINK="/usr/local/bin/godotter"

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}🔍 正在查找入口文件...${NC}"

# 优先查找的入口文件列表（按优先级排序）
ENTRY_FILES=(
    "$PROJECT_DIR/src/godotter/cli.py"
    "$PROJECT_DIR/src/godotter/main.py"
    "$PROJECT_DIR/src/godotter/godotter.py"
    "$PROJECT_DIR/src/godotter/app.py"
    "$PROJECT_DIR/main.py"
    "$PROJECT_DIR/cli.py"
    "$PROJECT_DIR/godotter.py"
    "$PROJECT_DIR/app.py"
)

ENTRY_FILE=""
for file in "${ENTRY_FILES[@]}"; do
    if [ -f "$file" ]; then
        ENTRY_FILE="$file"
        echo -e "${GREEN}✓ 找到入口文件: $file${NC}"
        break
    fi
done

if [ -z "$ENTRY_FILE" ]; then
    echo "❌ 错误: 未找到入口文件"
    echo "请确保以下文件之一存在:"
    printf '  - %s\n' "${ENTRY_FILES[@]}"
    exit 1
fi

# 创建包装脚本
echo -e "${YELLOW}📝 创建包装脚本: $WRAPPER_SCRIPT${NC}"

cat > "$WRAPPER_SCRIPT" << EOF
#!/bin/bash
# godotter.sh - Godotter CLI 包装脚本
# 自动生成，请勿手动修改

cd "$PROJECT_DIR" || exit 1
exec uv run godotter "\$@"
EOF

# 添加执行权限
chmod +x "$WRAPPER_SCRIPT"
echo -e "${GREEN}✓ 包装脚本已创建并添加执行权限${NC}"

# 创建软链接
echo -e "${YELLOW}🔗 创建软链接: $TARGET_LINK -> $WRAPPER_SCRIPT${NC}"

# 如果软链接已存在，先删除
if [ -L "$TARGET_LINK" ]; then
    rm "$TARGET_LINK"
    echo -e "${YELLOW}⚠ 已删除旧的软链接${NC}"
fi

# 创建新的软链接
ln -s "$WRAPPER_SCRIPT" "$TARGET_LINK"
echo -e "${GREEN}✓ 软链接创建成功${NC}"

# 验证安装
echo -e "${YELLOW}🔧 验证安装...${NC}"
if command -v godotter &> /dev/null; then
    echo -e "${GREEN}✅ Godotter 注册成功！${NC}"
    echo ""
    echo "使用方法:"
    echo "  godotter --help     显示帮助信息"
    echo "  godotter info       显示项目信息"
    echo "  godotter chat       启动聊天"
    echo "  godotter providers  查看 AI 提供商"
    echo ""
    echo "如需卸载，请运行: $PROJECT_DIR/remove_godotter.sh"
else
    echo "❌ 警告: godotter 命令似乎不可用，请检查 /usr/local/bin 是否在 PATH 中"
    exit 1
fi
