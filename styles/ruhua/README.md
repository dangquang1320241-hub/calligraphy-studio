# 作品入画模板

把真实作品照片装裱后放进 3–4 个场景里（默认：茶室横披 / 书房镜框 / 园林门匾 / 展厅卡纸框），每景约 6.5 秒缓慢推拉，场景之间自右向左柔边横扫转场，每景一句短字幕；片头平铺作品 + 标题，片尾压暗落款。竖屏 1080×1920、30fps、H.264+AAC，默认约 33.5 秒。

- 作品像素只做**缩放、透视、整体明暗/色温匹配（乘法）**，不重画笔画。
- 装裱（绫、隔水、轴杆、木框、卡纸、玻璃反光）都由程序生成。
- `plaque`（匾额）模式把照片里提取的墨色遮罩按原形"转印"到木纹上，加一点浮雕，属于示意效果。
- 场景底图是生成的示意图，画面左上角固定标「场景为示意」。

## 换一张作品

只改 `artwork` / `cleaned` / `title` / `author`，四个场景会自动按新作品的宽高比在角点框里居中摆放（横幅、竖幅、斗方都行，装裱尺寸跟着变）。

```bash
cd /workspace/shared/书法视频模板/作品入画
cp config.example.json /path/to/my/config.json

# 1) 预览：每景一张合成静帧 + 片头/片尾（约 30 秒）
python3 render.py --config /path/to/my/config.json --out /path/to/作品入画.mp4 --check-dir /path/to/检查记录/作品入画 --preview

# 2) 正式渲染（约 3–4 分钟），自动校验并出联系表
python3 render.py --config /path/to/my/config.json --out /path/to/作品入画.mp4 \
    --timeline /path/to/timeline.csv --check-dir /path/to/检查记录/作品入画
```

## 换 / 加场景

`scenes` 是一个列表，每项：

| 字段 | 说明 |
|---|---|
| `background` | 背景图路径（9:16 最好；其他比例按铺满裁切） |
| `corners` | 作品（含装裱）要放进的四边形，**归一化坐标**，顺序 左上→右上→右下→左下。斜拍的墙面就填斜的四个点，会按透视贴上 |
| `style` | `scroll_h` 横披（绫裱+轴杆）/ `frame` 木框镜框 / `gallery` 黑框白卡纸 / `plaque` 匾额转印 |
| `caption` / `caption_sub` | 本景字幕（两行），在 y≈1420，避开底部 350px |
| `camera` | `zoom_from`/`zoom_to`（1.0 = 整幅背景）、`focus_from`/`focus_to`（归一化中心，`focus_to` 默认作品中心） |
| `shadow` | `dx`/`dy`/`blur`/`opacity`（相对画幅）——按场景光源方向调 |
| `light_follow` / `brightness` / `tint_strength` | 光照匹配强度：作品亮度跟背景低频亮度走多少、整体增益、跟背景色温走多少 |
| `emboss` | 仅 plaque：浮雕强度 |

量角点：`python3 ../common/make_grid.py 场景.jpg -o grid.jpg --step 50`，在网格上读像素再除以宽高。新背景请用"墙面留空、没有文字"的图。现成的四张在 `scenes/`（见 `scenes/README.md`）。

其他：`timing`（`title_seconds`、`scene_seconds`、`crossfade`、`end_card_seconds`）、`music`（`common/music_synth.py` 参数，换景处加重音）、`tag_text`（默认「场景为示意」）、`loudness_lufs`。


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
