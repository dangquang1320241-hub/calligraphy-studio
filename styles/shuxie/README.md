# 书写路径模板
按 `strokes[]` 里逐笔的遮罩多边形和顺序，沿 `dir` 方向逐笔揭开**原作品照片**，看起来像一笔笔写出来；写完后整幅原图完整显示。只是对原图像素做遮罩显隐，不生成、不修补笔画。
用法：`python3 render_shuxie.py --config <项目>/源码/config.json [--preview 10]`
字段：`strokes[].poly` 笔画多边形（校正后作品图像素）、`secs` 时长、`dir` 揭开方向、`pause_after`；`focus[]` 推近区间（box/t0/t1）；`geometry`/`paper_crop`/`grade` 同朗读版；`music` 传给 common/music_synth.py（`accent_every` 每 N 笔一记重音）。
逐笔多边形需手工或半自动描（示例只有 2 个矩形占位）。未实际渲染测试（只做了语法检查）。
