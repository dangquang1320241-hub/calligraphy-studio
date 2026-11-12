# 书法「诗文朗读版」模板

换一张作品图 + 一份 config.json，一条命令出竖屏朗读视频：TTS 逐句朗读，镜头缓移并停在每句对应的列，字幕逐句出现、按 TTS 字边界逐字提亮，自制古风底乐压低并在人声处闪避，片头标题卡、片尾「作者名 · 书」。

## 用法
```bash
# 1. 新建项目（按 video-production-pipeline 目录规范）
cp -r /workspace/shared/video-template /workspace/<项目名>
cp <作品照片> /workspace/<项目名>/素材/作品原图.jpg
cp /workspace/shared/书法视频模板/朗读版/config.example.json /workspace/<项目名>/源码/config.json
# 2. 改 config.json（见下表）；量列区域可先画框自查
# 3. 一条命令出片（含 ffprobe 校验 + 联系表）
python3 /workspace/shared/书法视频模板/朗读版/render_langdu.py --config /workspace/<项目名>/源码/config.json
#   --preview 12   只渲前 12 秒
#   --skip-tts     不联网，直接用 素材/tts/ 缓存（文本/声音/语速没变时也会自动复用缓存）
```
产出：`成片/<output_name>_1080x1920.mp4`（旧版自动改名 .bak-时间戳，不删）、`timeline.csv`（唯一时间线）、`源码/timeline_resolved.json`、`素材/tts/`（逐句 mp3/wav/字边界 json）、`素材/mix.wav`、`素材/music_only.wav`、`检查记录/<时间>/联系表.jpg + ffprobe.json + frames/`。

## config.json 字段
| 字段 | 说明 |
|---|---|
| `output_name` | 成片文件名前缀 |
| `image` | 作品照片路径（相对 config 所在目录或绝对路径） |
| `paper_crop` | `[x0,y0,x1,y1]` 原图像素，先粗裁到纸张外接矩形（留一点墙面没关系） |
| `paper_cutout` | true=纸外墙面换成背景色（纸内像素不动） |
| `grade` | `deyellow` 0–1 去黄程度、`contrast` 对比（1=不变）、`brightness` |
| `title` / `title_sub` | 片头标题、副标题（如「行草」） |
| `author` / `end_card_text` / `end_card_note` | 片尾卡；`end_card_text` 中 `{author}` 会被替换 |
| `font` | 字体路径，null=霞鹜文楷 |
| `tts.voice` / `rate` / `pitch` / `volume` | edge-tts 参数，如 `zh-CN-YunjianNeural`、`-30%`、`-2Hz` |
| `timing` | `title_secs` 标题卡、`intro_secs` 全幅亮相、`gap_secs` 句间停顿、`tail_hold` 末句后停留、`pull_back_secs` 拉回全幅、`end_secs` 片尾卡 |
| `camera` | `push` 停留时缓推比例、`move_secs` 移镜时长、`lead_secs` 开口前多久开始移镜、`spotlight` 区域外纸面压暗比例 |
| `sentences[]` | 每句：`text` 原文（含标点）、`subtitle` 字幕文字、`tts_text`（可选，多音字可用同音字替换，只影响读音不影响字幕）、`region` 该句对应画面区域（原图像素 `[x0,y0,x1,y1]`）、`cols` 备注、`gap_after`（可选） |
| `music` | 传给 `common/music_synth.py` 的参数（seed/root/bpm/density/xiao/reverb_secs/wet）+ `music_db` 底乐整体电平、`duck_db` 人声处额外下压 |
| `loudness` | 成片响度目标 `I`/`TP`/`LRA`（两遍 loudnorm） |
| `layout` | `focus_window` 每句区域在画面里的放置窗口、`full_window` 全幅窗口、`subtitle`（字号/颜色/中心/未读字透明度）、`title`、`end` |

默认版式已避开视频号 UI：字幕中心 (480,1400)，底部 y≥1536（20%）和右侧 x≥918（15%）不放文字。

## 量列区域的办法
对墨迹做列投影即可得到每列 x 区间（见 /workspace/书法展示样片-朗读版/检查记录/列定位/列框.jpg）。一句跨两列时，`region` 取两列并集；镜头按区域高度适配窗口，所以全高区域会得到一致的缩放。

## 多音字
默认 edge-tts 读「曾日月之几何」的「曾」为 céng（已用音频比对验证）。遇到读错的字，在该句 `tts_text` 里换同音字。

## 许可记录
- 底乐：common/music_synth.py 自制，可商用。
- 字体：霞鹜文楷 SIL OFL 1.1。
- 朗读：edge-tts（微软 Edge 在线朗读）——**商用许可未确认**，公开商用前建议换 Azure 语音或已确认许可的本地模型。
- 每个项目在 `素材/LICENSES.md` 记录来源、许可、sha256。

## 照片摆正（几何校正，v2 起）
在作品图那一层加 `geometry`（朗读版放顶层，临帖对比放 `work.geometry`；参照帖也可加 `reference.geometry`）：
```json
"geometry": {"auto_deskew": true, "margin_px": 40, "cache": "../素材/作品_校正.png"}
"geometry": {"rotate_deg": -1.2}
"geometry": {"perspective": [[58.8,111.7],[1645.1,77.1],[1655.8,1015.4],[60.8,1051.0]]}
```
- `auto_deskew`：检测纸张四条边（抠纸遮罩边缘点 + 稳健直线拟合），四线求交得四角，透视拉正成正矩形；检测失败时退回墨迹列投影法求旋转角。
- `rotate_deg`：手动旋转，度，正值=逆时针。`perspective`：手动四角，原图像素，顺序左上、右上、右下、左下。
- `margin_px`：拉正后四周保留的原图边距（供抠纸）；`extra_rotate_deg`：拉正后再微转（让墨迹列更竖直时用，会让纸边略斜）；`cache`：校正图缓存路径（相对 config），原图或参数变了自动重算，旁边的 .json 记录检测角度、四角和单应矩阵。
- 插值：Lanczos（`cv2.INTER_LANCZOS4`）。只做几何变换，不改笔画、不修补、不生成。
- **注意**：开启后 config 里所有坐标（`paper_crop`、`region`、`work_box`）都按「校正后图像」量。可用 `common/geometry.py` 的 `segment_columns()` 在校正图上重新量列，`before_after_sheet()` 出带参考线的前后对比图。命令行：`python3 common/geometry.py 原图.jpg 校正.png --auto --report 对比.jpg`

