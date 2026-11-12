# 章法解析模板
作品全幅居中，按 `layers[]` 逐层叠加标注：`qi` 行气（每列墨迹质心连成中轴折线）、`boxes` 字框、`density` 列间留白色块并标像素宽度、`lines` 自定义线；每层配一句 `caption`。标注只画在照片之上，不改照片。`keep: true` 的层保留到片尾。
用法：`python3 render_zhangfa.py --config <项目>/源码/config.json [--preview 10]`
坐标都用几何校正后的作品图像素（与朗读版 v2 一致）。点评措辞需人工确认客观。未实际渲染测试（只做了语法检查）。

## 2026-11-10 更新（v1 已实渲，脚本已重写，上面旧段落的 layer 类型以本段为准）
- layer 类型：`qi`（每列墨迹左右重心折线）、`colboxes`（列框，`highlight_cols` 高亮）、`charboxes`（字框，`cols` 或 `chars:["列:字"]`，`emph` 加粗）、`gaps`（列间留白色块，越宽越深）。每层可设 `view`（局部放大框）与 `window`（该层画框）。
- 列/字位置默认用 common/inkseg.py 自动检测（`inkseg.crop/columns_text/signature`），结果缓存到 项目/源码/layout_px.json，可手改后复用。
- `tts.enabled=false` 时只有字幕；开启则与朗读版同一 edge-tts 设置。字幕折行不把开引号留在行尾。
- 实渲样片：/workspace/书法展示样片-章法解析/成片/后赤壁赋节选_章法解析_v1_1080x1920.mp4
