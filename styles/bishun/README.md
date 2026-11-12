# 笔顺复现模板

把一幅书法作品做成竖屏（1080×1920，30fps，H.264+AAC，约 30–34 秒）短视频：墨迹像被毛笔"写"出来——**自右向左逐列、每列自上而下逐字**，笔锋处有一点柔光，镜头跟着正在写的字横移；写完拉远看全篇，落款片尾。

- 只揭示照片里**真实的墨像素**：每个像素在某一时刻从"空白纸色"切换成照片原像素，最后一帧就是原作品，不重画、不改笔画。
- "空白纸"是从照片本身去掉墨区后估出来的纸色 + 纸纹，只出现在"还没写到"的位置。
- **笔顺是算法示意**：每个字的墨迹先按"先上后下、同高先左后右"给连通块排序，再沿骨架（Zhang–Suen 细化）从左上端点按测地距离生长，墨宽靠"最近骨架点"自动对上。这不是作者真实笔顺，画面左上角固定标「笔顺为示意」。

## 换一张作品，三步出片

```bash
cd /workspace/shared/书法视频模板/笔顺复现
cp config.example.json /path/to/my/config.json      # 改 artwork / title / columns_text / author

# 1) 自动检测列和字，出叠框检查图（先看一眼切得对不对）
python3 ../common/inkseg.py detect --image /path/to/作品_clean.png --out /path/to/my/layout.json \
    --columns-text "于是携酒与|鱼复游于赤|壁之下江流|有声断岸|千尺山高月|小水落石出|曾日月之几" \
    --signature --overlay /path/to/检查记录/layout.jpg

# 2) 先出 8 张静帧预览（几秒钟）
python3 render.py --config /path/to/my/config.json --out /path/to/笔顺复现.mp4 --check-dir /path/to/检查记录/笔顺复现 --preview 8

# 3) 正式渲染（约 2–3 分钟），自动做 ffprobe/解码/响度检查并出联系表
python3 render.py --config /path/to/my/config.json --out /path/to/笔顺复现.mp4 \
    --timeline /path/to/timeline.csv --check-dir /path/to/检查记录/笔顺复现
```

`cleaned` 指向的裁切图不存在时，会用 `common/media.py` 的 `preprocess_artwork` 从 `artwork` 原照片自动裁到纸边（只做透视和整体影调）。不给 `columns_text` 也能跑：列数、字数按投影自动估计（字幕会空着）。

## 配置字段

| 字段 | 说明 |
|---|---|
| `artwork` / `cleaned` | 原照片 / 裁到纸边的作品图 |
| `title` / `title_sub` / `subtitle` | 片头标题、片头副题、片尾副题 |
| `author` / `author_note` / `seal_char` | 落款占位（默认「作者名 · 书」），请换成作者名号 |
| `columns_text` | 右→左每列原文（决定每列切几个字、底部字幕内容） |
| `has_signature` | 最后一列末尾有落款小字时为 true（多切一个，字幕不显示） |
| `layout` | `"auto"`（自动检测，结果缓存在 config 同目录 `layout.auto.json`）或一个 layout.json 路径 |
| `char_cuts` | **人工修正**：`{"列号(1 起，右→左)": [字与字之间的 y 切线，0–1]}`。行草连笔多、自动切错时用，读叠框图上的位置填 |
| `timing` | `title_seconds` 片头、`writing_seconds` 书写总长、`column_gap`/`char_gap` 列间/字间停顿、`zoom_out_seconds`、`end_card_seconds`、`edge_softness` 笔触前沿软度（秒） |
| `paper_height_px` / `stage_top_px` | 书写时纸面高度与顶部位置（竖屏里纸横向超出，镜头跟随平移） |
| `caption_y_px` | 字幕中心 y（默认 1430，底部 350px 以内不放字） |
| `music` | 古琴式底乐参数（`common/music_synth.py`）+ `brush_db` 毛笔沙声音量 |
| `reveal_halo_px` | 墨边外延多少像素一起揭示（默认 10，照片墨晕大时加大） |

字时长 ∝ 骨架长度^0.6，总长对齐 `writing_seconds`；每列第一个字处底乐加一记低音拨弦重音，每个字配一声很轻的毛笔擦纸声（合成噪声）。所有时间写入 `timeline.csv`。

## 已知局限

- 行草里跨字连笔会被按切线一分为二，分别在两个字里出现。
- 切字靠投影 + 动态规划，字形差异大的列可能切偏，用 `char_cuts` 修。
- 空白纸是估出来的，墨晕很重或纸有大褶皱时，未写到的地方能隐约看到浅痕。


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
