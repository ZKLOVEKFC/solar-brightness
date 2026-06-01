#!/bin/bash
# solar-brightness 一键安装脚本
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}☀️  solar-brightness 安装脚本${NC}"
echo ""

# 检查 macOS
if [[ "$(uname)" != "Darwin" ]]; then
    echo -e "${RED}❌ 仅支持 macOS${NC}"
    exit 1
fi

# 检查 Apple Silicon
if [[ "$(uname -m)" != "arm64" ]]; then
    echo -e "${YELLOW}⚠️  非 Apple Silicon Mac，DDC/CI 可能不工作${NC}"
fi

# 安装 m1ddc
if ! command -v m1ddc &>/dev/null; then
    echo "📦 安装 m1ddc..."
    if ! brew install m1ddc; then
        echo -e "${RED}❌ m1ddc 安装失败，请检查网络或手动安装: brew install m1ddc${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}✅ m1ddc 已安装${NC}"
fi

# 验证 DDC/CI
echo "🔍 检测显示器..."
DISPLAYS=$(m1ddc display list 2>/dev/null)
if [ -z "$DISPLAYS" ]; then
    echo -e "${YELLOW}⚠️  未发现 DDC/CI 可控显示器${NC}"
    echo "   请确保显示器支持 DDC/CI 并在显示器 OSD 中启用了此功能"
else
    echo "$DISPLAYS"
fi

# 安装 PyYAML
echo ""
echo "📦 安装 Python 依赖..."
pip3 install --quiet pyyaml 2>/dev/null || pip3 install pyyaml
echo -e "${GREEN}✅ PyYAML 已安装${NC}"

# 安装服务
echo ""
python3 "$(dirname "$0")/solar-brightness.py" --install

echo ""
echo -e "${GREEN}✅ 完成！${NC}"
echo ""
echo "下一步:"
echo "  1. 编辑配置: vim ~/.config/solar-brightness/config.yaml"
echo "  2. 查看状态: python3 $(dirname "$0")/solar-brightness.py --status"
echo "  3. 查看日志: tail -f ~/.config/solar-brightness/solar-brightness.log"
