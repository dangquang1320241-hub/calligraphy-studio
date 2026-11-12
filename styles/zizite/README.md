# 逐字特写模板

把一幅横幅书法作品做成竖屏（1080×1920）短视频：先给全篇，再用平滑的 Ken Burns 镜头走到 4–5 处精彩点（单字或一组字），放大停住并配一句点评，最后拉回全篇、落款。适合视频号。

本模板不绑定某一幅作品。换一张照片、在 `config.json` 里改特写框和点评，即可重新出片。

## 依赖

- Python 3.10+：`numpy`、`Pillow`、`opencv-python`、`scipy`
- `ffmpeg` / `ffprobe`
- 字体：Noto Serif CJK SC（`/usr/share/fonts/opentype/noto/NotoSerifCJK-*.ttc`）

## 快速开始

```bash
cp config.example.json my.json
# 用网格图量出要特写的字的包围盒（归一化坐标）
python ../common/make_grid.py 作品_clean.png -o grid.jpg --step 100

python render.py --config my.json --out 逐字特写.mp4 --timeline timeline.csv
```

脚本会自动裁纸、校正（不改笔画），并合成与镜头停点对齐的自制拨弦氛围，不必另下配乐。

## 配置要点

| 字段 | 说明 |
|---|---|
| `artwork` / `cleaned` / `preprocess` | 同「慢揭幕」模板 |
| `title` / `subtitle` / `author` / `author_note` / `seal_char` | 片头标题与片尾落款占位 |
| `highlights` | 特写列表，按出现顺序。每项：`char`（字或词）、`note`（一句点评）、`box` `[x0,y0,x1,y1]` |
| `video_b` | `open_hold` 开场全篇秒数、`travel` 运镜、`hold` 停住、`pullback` 拉回、`end_card` 落款；若总时长对不上会自动微调 `hold` |

`box` 写在 **cleaned 图** 上。`0–1` 为归一化坐标（推荐），大于 1 视为像素。镜头会在盒子周围留一点纸边，并按画面比例补足，不会把字拉变形。

点评文字会显示在画面下部、视频号底栏安全区之上，请写一句短评（约 12–16 字内在手机上最清晰）。

## 安全区

字幕避开底部约 350px 和右侧约 130px 的平台控件区。

## 音乐

由 `../common/audio.py` 自制（Karplus–Strong 五声拨弦 + 垫音 + 混响），重音对齐各特写到达点。无第三方音源。响度约 −16 LUFS。

## 输出

1080×1920、30fps、H.264 + AAC，建议 25–35 秒（默认约 32 秒）。


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
