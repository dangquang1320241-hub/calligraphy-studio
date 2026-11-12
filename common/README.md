# common：共用脚本

## music_synth.py — 自制古风底乐
纯代码合成，无采样、无第三方旋律：低音持续音垫（根音+五度）+ 古琴感拨弦（加性谐波、指数衰减、偶尔按弦上滑音，宫商角徵羽五声音阶）+ 箫感长音（正弦+带通气声+颤音）+ 合成混响。固定种子，同参数结果逐字节一致。
```bash
python3 music_synth.py --duration 40 --out music.wav --seed 7 --root 146.83 --bpm 56
python3 music_synth.py --duration 38 --out m.wav --params '{"seed":11,"bpm":52,"accents":[5.2,9.8]}'
```
参数：`seed` 随机种子（换种子=换旋律）；`root` 宫音频率 Hz（146.83=D3）；`bpm` 速度；`density` 0–1 拨弦密度；`xiao` 箫音量（0 关）；`reverb_secs`、`wet` 混响；`fade_in`/`fade_out`；`accents` 重音时刻列表（秒，画面换镜处加一记低音拨弦）。输出峰值约 -1 dBFS，混音时再压低。

**许可**：由本脚本生成的音频为 Alaina 自制作品，可商用，无需署名。

## audio_mix.py — 压低 + 闪避
`mix(voice, music, sr, music_db=-20, duck_db=-6, attack=0.25, release=0.6)`：底乐先整体压到 `music_db`，有人声时再按人声包络（带 0.25 秒提前量）额外下压 `duck_db`。

## videokit.py
- `grade(img, crop, deyellow, contrast, brightness)`：只做裁切 + 逐像素全局调色（白平衡去黄、以纸色为白点的对比），**不做局部修补**。
- `paper_mask(img, crop)`：纸张抠底遮罩（暖亮色最大连通区 + 填洞，纸内一切含墨迹原样保留），只用于把纸外墙面换成背景色。
- `ffprobe_report(mp4)`：分辨率/帧率/实际帧数/音轨/时长/解码/响度/sha256。
- `grab_frames` + `contact_sheet`：从**编码后的 mp4** 抽帧，拼联系表并叠加平台 UI 安全线（红虚线：底部 80%、右侧 85%）。

## 依赖
```bash
pip install numpy scipy pillow opencv-python-headless edge-tts   # 版本见 requirements.txt
apt install ffmpeg poppler-utils
```
字体：`fonts/LXGWWenKai-Regular.ttf`（霞鹜文楷 v1.520，SIL OFL 1.1，见 fonts/OFL.txt）；缺失时自动回退 Noto Serif CJK。

## 其他共用模块（由「慢揭幕 / 逐字特写」模板维护，本说明未覆盖）
`audio.py`（另一套 Karplus-Strong 拨弦合成 + loudnorm）、`cards.py`、`encode.py`、`media.py`、`make_grid.py`。朗读版与临帖对比不依赖它们，互不影响。

## geometry.py — 照片摆正（只做几何）
`load_work_image(path, geo, cache)`（模板入口）、`rectify(img, geo)`、`detect_paper_quad(img)`（纸张四边拟合→四角+各边角度）、`ink_projection_angle(img)`（列投影求旋转角）、`column_tilts(img, cols)`（每列倾角）、`segment_columns(img, box)`（重新量列）、`before_after_sheet(before, after, out)`（前后对比图+参考网格）。geo 写法：`{"auto_deskew": true}` / `{"rotate_deg": d}` / `{"perspective": [[x,y]×4]}`，可加 `margin_px`、`extra_rotate_deg`、`cache`。Lanczos 插值，不改笔画。

## 新增模块（笔顺复现 / 作品入画 / 墨色呼吸 用；只新增，不改上面任何文件的行为）
- `inkseg.py`：`ink_masks(rgb)` 墨迹软/硬遮罩；`detect_layout(rgb, columns_text, signature, char_cuts)` 列/字自动检测（列=投影切分+连通块归列；字=动态规划切线，可人工 `char_cuts` 修正）；`zhang_suen` 骨架；`char_label_map` + `reveal_progress_map` 笔顺示意的逐像素揭示进度；`draw_layout_overlay` 叠框检查图。CLI：`python3 inkseg.py detect --image 作品_clean.png --out layout.json --columns-text "列1|列2|…" [--signature] [--cuts JSON] --overlay check.jpg`
- `ambient.py`：`rain()`、`paper_rustle()`、`brush_swish()`、`place()`、`fade_edges()`、`master(layers, out_wav, target_lufs)`（求和→峰值 0.89→调用 `audio.loudnorm` 两遍归一）。纯合成、固定种子。
- `render_util.py`：`parallel_frames(frame_fn, n)`（fork 多进程按序出帧）、`tag_pill("场景为示意")`、`text_rgba`（霞鹜文楷 + 阴影）、`verify(mp4, dir, times, labels)`（调用 videokit 的 ffprobe_report / grab_frames / contact_sheet）。

## artwork_prep.py（v2 新增）
`prepare_cleaned(cfg, report=None)`：cfg 有 `geometry` 时，`geometry.rectify` 拉正 → 按 `paper_rect` 裁纸并内缩 `geometry_prep.inset_px`（默认 6）→ `media.mild_levels` → Lanczos 放大 `geometry_prep.scale`（默认 2）→ 写 `cfg["cleaned"]`，旁边 `.prep.json` 记录参数与缓存键。CLI：`python3 artwork_prep.py --config x.json --report 前后.jpg`。
`cards.py` 小改：`author_note` 为空时不画「（）」。

## audio.py — 配乐音色（v3 新增，默认行为不变）
`render_music(..., tone="legacy")` 仍是原来的明亮拨弦+轻度阻尼混响。`tone="warm"`（慢揭幕 / 逐字特写模板默认）会：用低通拨弦激励、16 ms 软起音、减弱八度泛音、拨弦 2.8 kHz 低通、混响 2.5 kHz 低通，并在混音上做约 −5 dB / 3.5 kHz 的高架衰减；垫音音量提到 0.16，避免去掉尖锐起音后拨弦中频盖住低音。也可把同样的关键字直接传给 `synth_ambience`（`pluck_opts`、`reverb_damp_hz`、`high_shelf_hz`/`high_shelf_db`）。`loudnorm` 未改。新增 `high_shelf()`、`tone_settings()`。
