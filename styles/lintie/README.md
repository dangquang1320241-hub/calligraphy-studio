# 书法「临帖对比」模板

换作品图 + 参照帖图 + 一份逐字裁切坐标与点评的 config.json，一条命令出竖屏视频：片头全幅作品 → 作品缩成上方导览图（红框标当前字）→ 每字「参照 | 本作」两块从右滑入、停留、向左滑出，下方一句点评 → 拉回全幅 +「作者名 · 书」+ 参照图署名。无朗读，底乐在每次换字处加一记重音。

## 用法
```bash
cp -r /workspace/shared/video-template /workspace/<项目名>
cp <作品照片> /workspace/<项目名>/素材/作品原图.jpg
cp <参照帖图> /workspace/<项目名>/素材/参照帖/
cp /workspace/shared/书法视频模板/临帖对比/config.example.json /workspace/<项目名>/源码/config.json
python3 /workspace/shared/书法视频模板/临帖对比/render_lintie.py --config /workspace/<项目名>/源码/config.json [--preview 10]
```
产出同朗读版：成片（旧版自动 .bak）、timeline.csv、检查记录/<时间>/联系表.jpg（片头+每字一帧+片尾）+ ffprobe.json。

## config.json 字段
| 字段 | 说明 |
|---|---|
| `title` / `title_sub` / `header` | 片头标题、副标题、导览图上方常驻页眉（须含「参照：…」） |
| `author` / `end_card_text` | 片尾卡（`{author}` 替换） |
| `work` | `image`、`paper_crop`、`paper_cutout`、`grade`（同朗读版）、`tile_label`（如「本作」） |
| `reference` | `image` 参照帖图、`label`（片头片尾的参照说明）、`tile_label`（如「参照 · 赵孟頫」）、`credit`（图片许可署名，片尾显示）、`grade` |
| `chars[]` | 每字：`char`、`ref_box` 参照帖图上的裁切框、`work_box` 作品原图上的裁切框（均为原图像素 `[x0,y0,x1,y1]`，框内不要带进邻字笔画）、`comment` 点评（客观描述可见差异：重心、笔势、连带、开合、粗细；不吹捧不贬低）、`secs`（可选，单字时长） |
| `timing` | `intro_secs`、`shrink_secs`、`char_secs`（默认 4.615=52bpm 的 4 拍）、`slide_in`、`slide_out`、`comment_delay`、`grow_secs`、`end_secs` |
| `music` | 同 common/music_synth.py 参数 + `accent_on_cut` 换字重音开关 |
| `loudness` | 纯音乐建议 I=-18 |
| `layout` | `full_window`、`map_window`（导览图）、`tile`（size/pad/y/ref_x/work_x/slide_px/bg，`bg:"auto"`=用裁切自身纸色铺底）、`comment`（x/y/width/字号）、`title`、`header`、`end` |

两块方格：裁切框等比缩放进方格，不拉伸、不补画；方格底色只铺在裁切图外。默认版式避开底部 20%、右侧 15%。

## 定位字的办法
先把参照帖页面按 50px 画网格看图找字，再给 `ref_box`/`work_box` 一个略宽的框；用 config 出一张逐字对照小图自查（框里是否带进邻字）。

## 许可记录
- 参照帖须确认公有领域；图片若有署名要求（如故宫 Open Data 的 CC BY 4.0），写进 `reference.credit`，并记录在项目 `素材/LICENSES.md`。
- 画面一律写「参照」，不要说成作者所临原帖，除非作者确认。
- 底乐 common/music_synth.py 自制可商用；字体霞鹜文楷 OFL。

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

