#!/usr/bin/env bash
# calligraphy-studio 环境自检：逐行输出 OK/MISSING/INFO，agent 据此决定引导安装或直接调用
set -u
fail=0

DEV_DEFAULT="$HOME/Workspace/10-Projects/calligraphy-studio"   # 开发机路径，仅作兜底
TARGET="${CALLIGRAPHY_STUDIO_HOME:-}"
if [ -z "$TARGET" ] && [ -d "$HOME/calligraphy-studio" ]; then TARGET="$HOME/calligraphy-studio"; fi
if [ -z "$TARGET" ] && [ -d "$DEV_DEFAULT" ]; then TARGET="$DEV_DEFAULT"; fi
echo "STUDIO_HOME=$TARGET"

if [ -d "$TARGET" ]; then
  echo "OK repo"
  PY="$TARGET/.venv/bin/python"
  if [ -x "$PY" ]; then
    echo "OK venv"
    (cd "$TARGET" && "$PY" -c "import core.profile; import core.orchestrate; import styles.langdu.plugin" >/dev/null 2>&1) \
      && echo "OK deps" || { echo "MISSING deps（在 $TARGET 下跑 uv sync）"; fail=1; }
  else
    echo "MISSING venv（在 $TARGET 下跑 uv sync，或先跑 bootstrap.sh）"; fail=1
  fi
else
  echo "MISSING repo（先跑 bootstrap.sh）"; fail=1
fi

if command -v ffmpeg >/dev/null 2>&1; then echo "OK ffmpeg"; else echo "MISSING ffmpeg（brew install ffmpeg）"; fail=1; fi
command -v uv >/dev/null 2>&1 && echo "OK uv" || echo "INFO uv 未装（重装依赖时需要：curl -LsSf https://astral.sh/uv/install.sh | sh）"

if [ -n "${DEEPSEEK_API_KEY:-}" ]; then echo "OK deepseek-key（API 认字高质量模式可用）"
else echo "INFO deepseek-key 未配置（走 agent 认字模式，零 key，够用）"; fi

DATA="${CALLIGRAPHY_DATA_ROOT:-$HOME/Workspace/10-Projects/VideoCreationStorage/CalligraphyWorksShow}"
echo "DATA_ROOT=$DATA"
[ -d "$DATA" ] && echo "OK data-root" || echo "INFO data-root 不存在（首次出片时工具会自动创建；换目录请 export CALLIGRAPHY_DATA_ROOT）"

[ "$fail" -eq 0 ] && echo "RESULT=ready" || echo "RESULT=need-setup"
exit "$fail"
