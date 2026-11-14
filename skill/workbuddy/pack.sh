#!/usr/bin/env bash
# 组装 WorkBuddy 技能 zip：manifest/prompt/README/scripts + 工具库完整副本（排除 venv/git/缓存）
# 产物 dist/calligraphy-studio-skill.zip（不入 git）→ WorkBuddy「Skills 技能 → 导入 → 本地上传」
set -eu
cd "$(dirname "$0")"                      # skill/workbuddy
REPO="$(cd ../.. && pwd)"                 # calligraphy-studio/
DIST="$(pwd)/dist"
rm -rf "$DIST"
mkdir -p "$DIST/bundled"

echo "复制工具库副本（排除 .venv/.git/缓存）…"
rsync -a \
  --exclude '.venv/' --exclude '.git/' --exclude '__pycache__/' --exclude '.pytest_cache/' \
  --exclude '.coverage' --exclude '.DS_Store' --exclude 'skill/' --exclude 'dist/' \
  --exclude '样片库/' --exclude '_uploads/' \
  "$REPO/" "$DIST/bundled/calligraphy-studio/"

cp manifest.json prompt.md README.md "$DIST/"
cp -R scripts "$DIST/scripts"
(cd "$DIST" && zip -rq calligraphy-studio-skill.zip manifest.json prompt.md README.md scripts bundled)
echo "打包完成：$DIST/calligraphy-studio-skill.zip"
du -h "$DIST/calligraphy-studio-skill.zip"
