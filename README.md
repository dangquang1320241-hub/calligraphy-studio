# calligraphy-studio

书法作品 → 竖屏短视频的自动化工具。上传作品照片 + 释文，按风格一键出片。

## 环境准备（一次）

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python "numpy>=2" "opencv-python-headless>=4.10" "pillow>=11" "scipy>=1.14" "edge-tts==7.3.1"
brew install ffmpeg   # 若未装
```

## 一条命令出片（M1：朗读版）

```bash
.venv/bin/python -m core.orchestrate make 朗读版 <作品图.jpg> \
    --text 释文.txt --slug <作品标识> --title 《作品名》节选 --author 作者名
```

流程：作品档案（EXIF 归正 → 几何摆正 → inkseg 列/字检测 → 列框确认图）→ 风格插件生成 config → 渲染 → QC（ffprobe + 联系表 + 音画对位）→ 发布索引。

- 同一作品再次出片（换风格/调参数）自动复用档案，释文/读音人工确认成果不重做。
- 自动列字数估错时：`--col-counts 4,7,5,5,4,5,5,5`（右→左）人工指定。
- 数据目录默认 `~/Workspace/10-Projects/VideoCreationStorage/CalligraphyWorksShow`，可用 `--data-root` 改。

## 结构

见 [docs/架构.md](docs/架构.md)：风格矩阵、作品档案 schema、插件契约、LLM 接入契约、里程碑。

## 人工确认点（3）

1. 列框确认图（`作品档案/<slug>/列框确认图.jpg`）——列数/字数/落款判定
2. 释文（`作品档案/<slug>/释文.txt`）——草书识别永远人工过目
3. 成片联系表（`项目/<slug>_<风格>/检查记录/`）——视觉与音画对位
