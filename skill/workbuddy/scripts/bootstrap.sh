#!/usr/bin/env bash
# 首跑引导：从技能包内嵌副本安装工具库 → uv sync 锁版本装依赖 → 导入冒烟
# 幂等：重复执行不覆盖已有安装；更新工具库请删掉目标目录重跑，或跑 pack.sh 重新打包
set -eu
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$SKILL_DIR/bundled/calligraphy-studio"
TARGET="${CALLIGRAPHY_STUDIO_HOME:-$HOME/calligraphy-studio}"

if [ ! -d "$SRC" ]; then
  echo "MISSING bundled/calligraphy-studio（本技能 zip 需内嵌工具库副本：在工具库跑 skill/workbuddy/pack.sh 重新打包）"
  exit 1
fi

if [ -d "$TARGET" ]; then
  echo "复用已有安装：$TARGET（如需全新安装请先删除该目录）"
else
  echo "安装工具库 → $TARGET"
  mkdir -p "$(dirname "$TARGET")"
  cp -R "$SRC" "$TARGET"
fi

cd "$TARGET"
command -v uv >/dev/null 2>&1 || { echo "MISSING uv（先装：curl -LsSf https://astral.sh/uv/install.sh | sh）"; exit 1; }
echo "装依赖（uv sync，按 uv.lock 精确锁定；Python 3.12 由 uv 自动准备）…"
uv sync

"$TARGET/.venv/bin/python" -c "import core.profile, core.orchestrate, styles.langdu.plugin; print('OK smoke-import')"
command -v ffmpeg >/dev/null 2>&1 && echo "OK ffmpeg" || echo "WARN ffmpeg 未装：brew install ffmpeg（渲染必需）"

echo "---- 安装完成 ----"
echo "工具库：$TARGET"
echo "数据根：默认 ~/Workspace/10-Projects/VideoCreationStorage/CalligraphyWorksShow（换目录请 export CALLIGRAPHY_DATA_ROOT=<目录>）"
echo "AI 认字升级（可选）：export DEEPSEEK_API_KEY=<key>"
echo "验证：bash $(dirname "$0")/check_env.sh 期望 RESULT=ready"
