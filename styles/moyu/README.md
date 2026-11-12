# 墨色呼吸模板

治愈系慢片：镜头贴近作品，缓慢漂移扫过 5 处局部（飞白、积墨、纸纹），镜头间 1.4 秒叠化，画面有很轻的"呼吸"光（整体亮度 ±3.5%、一团慢慢游走的暖光 + 暗角）；声音是合成的屋檐细雨 + 换镜时的翻纸声 + 稀疏古琴/箫。几乎无字：只有开场一句和落款片尾。竖屏 1080×1920、30fps、H.264+AAC，默认 34 秒，响度 −17 LUFS。

## 清晰度原则（重要）

放大倍数按**原照片像素**算（`cleaned_scale` = 裁切图相对原照片放大了几倍，`preprocess_artwork` 默认 2）。默认 `min_magnification` 2.6 / `max_magnification` 3.2，只做 Lanczos 放大 + 轻度锐化（`sharpen`），**不做 AI 超分、不补细节**。手机照片（约 1700px 宽）在 3 倍左右就开始发软、能看到 JPEG 块，这是照片本身的极限；想要真正的"微距"，请让作者用更高像素正面拍（或分区拍），再把倍数调大。

## 换一张作品

```bash
cd /workspace/shared/书法视频模板/墨色呼吸
cp config.example.json /path/to/my/config.json      # 改 artwork / cleaned / title / author / opening_line

# 1) 自动找焦点：把作品从右到左分成 n 条，每条挑一个最"耐看"的位置，飞白/积墨交替
python3 render.py --config /path/to/my/config.json --check-dir /path/to/检查记录/墨色呼吸 --detect-only
#    → config 同目录 focus.auto.json + 检查记录/墨色呼吸/focus_overlay.jpg（红=起始取景，蓝=结束取景）

# 2) 静帧预览
python3 render.py --config /path/to/my/config.json --out /path/to/墨色呼吸.mp4 --check-dir /path/to/检查记录/墨色呼吸 --preview

# 3) 正式渲染（约 3 分钟），自动校验并出联系表
python3 render.py --config /path/to/my/config.json --out /path/to/墨色呼吸.mp4 \
    --timeline /path/to/timeline.csv --check-dir /path/to/检查记录/墨色呼吸
```

想手动挑镜头，把 `focus_points` 从 `"auto"` 改成列表（可以先拷 `focus.auto.json` 再改）：

```json
"focus_points": [
  {"center": [0.52, 0.45], "kind": "飞白", "mag_from": 2.6, "mag_to": 3.0, "drift": [0.0, 0.06]},
  {"center": [0.75, 0.30], "kind": "积墨"}
]
```
`center` 归一化中心；`mag_from`/`mag_to` 起止放大倍数（会被 `max_magnification` 限住）；`drift` 镜头内漂移量（归一化，默认上下交替 0.06）。

## 其他字段

| 字段 | 说明 |
|---|---|
| `opening_line` | 开场一句（默认「墨落纸上，自有呼吸」），标题作小字跟在下面 |
| `n_shots` | 自动模式挑几个镜头 |
| `timing` | `shot_seconds` 每镜、`crossfade` 叠化、`end_card_seconds`、`opening_text_seconds`、`breath_period` 呼吸周期 |
| `audio` | 底乐 `seed/root/bpm/density/xiao`；`rain_intensity`、`rain_db` 雨声；`rustle_db` 翻纸声 |
| `end_paper_top_px` | 片尾整幅作品的顶部位置 |


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
