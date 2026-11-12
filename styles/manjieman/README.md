# 慢揭幕模板

把一幅横幅书法作品做成竖屏（1080×1920）短视频：先出 2 秒标题，再自右向左、一行一行用软边墨色揭开，配上原文，最后缓缓推近并落款。适合视频号。

本模板不绑定某一幅作品。换一张照片、改一份 `config.json`，即可重新出片。

## 依赖

- Python 3.10+：`numpy`、`Pillow`、`opencv-python`、`scipy`
- `ffmpeg` / `ffprobe`（H.264 + AAC，响度用 loudnorm）
- 字体：Noto Serif CJK SC（系统已装 `/usr/share/fonts/opentype/noto/NotoSerifCJK-*.ttc`）

## 快速开始

```bash
# 1. 准备作品正面照片（光线均匀、尽量拍全纸）
# 2. 复制示例配置，填标题、正文、栏目范围
cp config.example.json my.json

# 3. 如需量栏目，先把照片裁切并打网格：
python ../common/make_grid.py 作品_clean.png -o grid.jpg --step 100

# 4. 渲染
python render.py --config my.json --out 慢揭幕.mp4 --timeline timeline.csv
```

脚本会：自动把照片裁到纸边并做轻微透视/影调校正（不改笔画）→ 按时间轴合成古琴式拨弦氛围（自制，不必另下曲子）→ 渲成 30fps 的 H.264/AAC。

## 配置要点

| 字段 | 说明 |
|---|---|
| `artwork` | 原照片路径 |
| `cleaned` | 裁切后的作品图；不存在且 `preprocess.run` 为 true 时会自动生成 |
| `title` / `subtitle` | 片头与片尾标题，如 `《后赤壁赋》节选`、`行草` |
| `author` / `author_note` / `seal_char` | 落款占位文字、说明、印章字（默认「书」）。请换成作者自己的名号 |
| `passage` / `passage_lines` | 揭开后显示的全文；有 `passage_lines` 时按行排 |
| `columns` | 自右向左的各列。`text` 是该列原文；`x0`/`x1` 是在 **cleaned 图** 上的水平范围 |
| `video_a` | 时长与各段时间（片头、揭开结束、推近结束、墨色揭开速度） |
| `fps` / `width` / `height` / `loudness_lufs` | 默认 30、1080、1920、−16 LUFS |

坐标：`0–1` 视为归一化（推荐：像素 ÷ 图宽/图高）；大于 1 的数视为像素。栏目顺序按位置自动从右往左揭开。

量栏目方法：对 `作品_clean.png` 跑 `make_grid.py`，在网格上读出每列左右边界，再除以宽度。列与列之间可略微重叠，以免把斜出的笔画切掉。

## 画面与安全区

字幕和落款都写在竖屏安全区内：底部约 350px、右侧约 130px 留给视频号按钮，请不要把重要字挤进这两个区域。

## 音乐

配乐由 `../common/audio.py` 用 Karplus–Strong 算法现场合成（五声音阶拨弦 + 低垫音 + 短混响），并按时间轴上的揭开/落款点给重音。没有使用任何下载或受版权保护的音源。响度目标约 −16 LUFS。

## 输出

1080×1920、30fps、H.264 + AAC，建议时长 25–35 秒（默认 30 秒）。


## 拉正照片（geometry，v2 新增）

照片拍歪了就在 config 里加：

```json
"geometry": {"auto_deskew": true},
"geometry_prep": {"scale": 2.0, "inset_px": 6}
```

或手动四角（原照片像素，左上→右上→右下→左下，以纸边为准）：

```json
"geometry": {"perspective": [[58.75,111.67],[1645.05,77.12],[1655.77,1015.43],[60.75,1051.03]]}
```

有 `geometry` 时，渲染前会调用 `common/artwork_prep.py`：`geometry.py` 透视拉正（Lanczos，不改笔画）→ 按纸边裁切并内缩 `inset_px` → 与原预处理相同的全局影调 → 放大 `scale` 倍，写到 `cleaned`（同参数同照片会复用缓存）。**拉正后所有坐标（列范围、字框、特写框、char_cuts）都以新的 cleaned 图为准**，换了 geometry 要重新量（`python3 ../common/inkseg.py detect --image <cleaned> ... --overlay 检查.jpg`）。不加 `geometry` 时行为与以前完全一样。单独生成 / 检查拉正结果：`python3 ../common/artwork_prep.py --config my.json --report 拉正前后.jpg`。

落款：`author` 写「郭德贤 · 书」这样的完整落款；`author_note` 留空就不显示括号小字；`seal_char` 是印章字（如「郭」）。

## 配乐音色（v3）
默认 `audio_tone: "warm"`：拨弦音区降低（C4 及以上降八度），合成走 `common/audio.py` 的 warm 预设（软起音、低通拨弦与混响、3.5 kHz 高架约 −5 dB），响度仍约 −16 LUFS、真峰值 ≤ −1.5 dBTP。若要回到 v2 的明亮音色，在 config 里设 `"audio_tone": "legacy"` 并自行改回音高。
