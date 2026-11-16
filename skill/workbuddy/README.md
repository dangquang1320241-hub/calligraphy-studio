# 书法出片工作台 · WorkBuddy 技能

书法作品照片 → 竖屏短视频。三种模式：⚡一键直出、✅三步确认（正式作品）、🎨风格样片。全部本地渲染，作品数据不出本机。

## 安装（WorkBuddy 桌面端）

1. 在工具库跑 `skill/workbuddy/pack.sh` 打出 `calligraphy-studio-skill.zip`（约 13.7MB，内嵌工具库副本与字体）
2. WorkBuddy →「Skills 技能」→「导入」→「本地上传」选 zip，确认名称/触发词
3. 首次对 agent 说「书法出片自检」或「出片」：agent 会跑 `scripts/bootstrap.sh` 完成安装（一次性，几分钟），随后跑 `check_env.sh` 应输出 `RESULT=ready`
4. **验证清单**（第一次必做）：
   - WorkBuddy 已授权本地文件/终端访问（技能执行命令会弹授权）
   - `ffmpeg` 已装（缺则 `brew install ffmpeg`）
   - 跑一条样片（样片模式）确认渲染链路通

## 日常使用（对 agent 说话即可）

- "⚡ 一键出片：这张图出成墨色呼吸" → 快出模式
- "帮我把这张作品正式出一条朗读版" → 确认模式（agent 会贴列框确认图给你核对）
- "看看每个风格什么样" → 样片模式
- "释文第三列错了，改成'××'，重出" → 修正 + 重出

## AI 认字说明

- 默认 **agent 自己认字**（零 API key）：agent 读取导出的逐列裁图完成释文，低置信字照常走人工确认
- 可选升级：`export DEEPSEEK_API_KEY=<key>`，一键模式自动改走 DeepSeek 视觉认字（`deepseek-flash`，质量更高）
- 无论哪条路，释文都可随时修正后重出（档案复用，重出 = 一次渲染）

## 前置与边界

- 目标机器：macOS（实测）/Linux（未系统验证）/Windows（未验证）；需要 `uv`（可自动装 Python 3.12）与 `ffmpeg`
- 工具库安装到 `~/calligraphy-studio/`（可用 `CALLIGRAPHY_STUDIO_HOME` 改）；作品数据在数据根（`CALLIGRAPHY_DATA_ROOT` 可改）
- 更新工具库：重新跑 `pack.sh` 打包 → 删除旧安装 → 重导入
- 隐私：作品照片与成片只存本地数据根，技能本身不含任何用户数据
