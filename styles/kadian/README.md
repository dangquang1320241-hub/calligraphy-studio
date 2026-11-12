# 节奏卡点模板
按 `rhythm.bpm`+`offset`（或直接给 `rhythm.beats` 拍点列表）每 `beats_per_shot` 拍切一次镜头，依次特写 `shots[]` 里的列/字区域（校正后作品图像素），切点带轻微「冲」+白闪，最后一镜回全幅并出「作者名 · 书」。
音乐默认用 common/music_synth.py 自制，重音落在切点；若用 `music_file`，须已确认可商用，并用节拍检测（如 librosa）量出 `beats` 填进来，写进 素材/LICENSES.md。
用法：`python3 render_kadian.py --config <项目>/源码/config.json [--preview 8]`。未实际渲染测试（只做了语法检查）。

## 2026-11-10 更新（v1 已实渲）
- 拍点吸附到帧网格（90 BPM@30fps = 每拍 20 帧），每 `beats_per_shot` 拍一切；`end_secs` 之后第一个切点起全幅定格 + 署名。
- 镜头可用 `{"col":n}` / `{"char":"n:k","pad":40}` 引用 layout_px（inkseg 结果，见 layout_px.example.json），`max_scale` 限制放大倍数。
- 默认配乐 `beat_music()`（本脚本内新增，只调用 common/music_synth 的音色函数）：每拍低鼓、切点重鼓+宫音拨弦、五声短句；两遍 loudnorm。
- 渲染后自动写 检查记录/<时间>/切点拍点偏差.json（帧差找切点、librosa 找起音）；`--check-only` 可对已有成片单独复查。
- 实渲样片：/workspace/书法展示样片-节奏卡点/成片/后赤壁赋节选_节奏卡点_v1_1080x1920.mp4
