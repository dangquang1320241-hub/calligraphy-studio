# calligraphy-studio

书法作品 → 竖屏短视频的自动化工具。上传作品照片 + 释文，按风格一键出片。

## 环境准备（一次）

```bash
uv sync              # 依 pyproject.toml + uv.lock 建 .venv（含 dev 测试组）
brew install ffmpeg   # 若未装
```

依赖唯一来源是 `pyproject.toml`（锁在 `uv.lock`）；改动依赖后运行 `uv lock && uv sync`。

## 一条命令出片（M1：朗读版）

```bash
.venv/bin/python -m core.orchestrate make 朗读版 <作品图.jpg> \
    --text 释文.txt --slug <作品标识> --title 《作品名》节选 --author 作者名
```

流程：作品档案（EXIF 归正 → 几何摆正 → inkseg 列/字检测 → 列框确认图）→ 风格插件生成 config → 渲染 → QC（ffprobe + 联系表 + 音画对位）→ 发布索引。

- 同一作品再次出片（换风格/调参数）自动复用档案，释文/读音人工确认成果不重做。
- 自动列字数估错时：`--col-counts 4,7,5,5,4,5,5,5`（右→左）人工指定。
- 数据目录默认 `~/Workspace/10-Projects/VideoCreationStorage/CalligraphyWorksShow`，可用 `--data-root` 改。
- 无释文也可先建档案（版式级风格直出；释文在 Web ③屏补），HEIC（iPhone/微信传图）直接上传即可。

## ⚡ 一键出片（可选）

```bash
.venv/bin/python -m core.orchestrate auto <作品图.jpg> --style 朗读版 --slug <标识> [--text 释文.txt]
```

- **填 `--text`**：零 LLM 调用直出（释文以您填的为准——自家人写的作品认得字，这是最准的路）
- **不填**：AI 视觉认字直出。需设 `DEEPSEEK_API_KEY`（默认模型 `deepseek-flash`，V4.1 起原生视觉）；GLM 备选设 `ZAI_API_KEY`；`CALLIGRAPHY_LLM=glm|deepseek` 显式切换
- AI 认字的档案标 `llm-auto`，低置信字超过 3 个自动退回 ②③ 确认流程；释文随时可在 Web ③屏修正后**一键重出**（档案复用，重出=一次渲染）
- 无需释文的版式类风格（墨色呼吸/作品入画等）零 LLM；Web ①屏勾选「⚡ 一键出片」同款能力

## 结构

见 [docs/架构.md](docs/架构.md)：风格矩阵、作品档案 schema、插件契约、LLM 接入契约、里程碑。

## Web 界面与部署给家人（局域网）

```bash
.venv/bin/python -m webapp                # 本机 http://127.0.0.1:8800
.venv/bin/python -m webapp --host 0.0.0.0 # 局域网 http://<Mac的IP>:8800
```

四屏：①上传构建 → ②列框确认 → ③释文确认（含逐列权威模式）→ ④风格出片。
任务状态与日志全落盘 `~/.calligraphy-studio/jobs/`，webapp 重启不丢。

**风格样片**：④屏风格卡带样片缩略图；「▶ 看风格样片」首次自动用最近的人审作品试渲 10 秒（约 1 分钟），缓存在数据根 `样片库/<风格>.mp4`，之后即点即看。CLI 等价：`python -m core.orchestrate sample <风格> --slug <作品> [--seconds N]`。

**安全边界（威胁模型）**：webapp 无鉴权 + `/data` 静态挂载可读全部作品——这是"家庭局域网=半可信网络"下的有意取舍。**不要直接暴露公网**；对外开放前必须补：token 鉴权、上传目录隔离、任务并发上限（详见 docs/审查报告.md）。

**备份**：数据根（作品档案/项目/发布索引）+ `~/.calligraphy-studio/jobs/` 即全部状态；工具库本身 git 管理。

## 人工确认点（3）

1. 列框确认图（`作品档案/<slug>/列框确认图.jpg`）——列数/字数/落款判定
2. 释文（`作品档案/<slug>/释文.txt`）——草书识别永远人工过目
3. 成片联系表（`项目/<slug>_<风格>/检查记录/`）——视觉与音画对位

## 测试与静态检查

```bash
.venv/bin/python -m pytest tests/ -q          # 单元+接口（无网络）
RUN_RENDER_TESTS=1 .venv/bin/python -m pytest tests/ -q  # 含渲染集成
uvx ruff check core common styles webapp tests --select E9,F   # 错误级静态检查
```
