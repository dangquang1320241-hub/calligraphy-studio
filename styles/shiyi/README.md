# 诗意叠化模板
复用朗读版（TTS、时间线、闪避混音、镜头逐句停列、逐字字幕），每句开头先出一幅**纯代码生成**的水墨场景（远山层叠、月、水波、孤舟、断崖，固定种子），停 `scene_hold` 秒后用 `dissolve` 秒叠化回作品对应列。
用法：`python3 render_shiyi.py --config <项目>/源码/config.json [--skip-tts] [--preview 10]`
config：在朗读版 config 基础上加 `scene`（seed、window 场景窗口、scene_hold、dissolve、max_mix）和每句 `sentences[].scene`（mountains 层数、moon/moon_x/moon_y、water/water_y、boat、cliff、paper/ink 色）。
红线：作品照片仍只裁切/调色/几何校正；场景是示意画面，发布时如需可加「示意」角标。未实际渲染测试（只做了语法检查）。

## 2026-11-10 更新（v1 已实渲）
- 改写：纸外背景用 `ink_scene()` 代码生成低对比水墨示意景（山/月/水/崖/舟），按句交叉溶解 + `drift_px` 缓慢视差；**作品本身不叠任何效果**，纸面经 paper_mask 抠出后盖在背景之上。字幕带（`subtitle_band`）下景色按 `subtitle_dim` 压暗。
- 继承 朗读版 `render_langdu.Renderer`（TTS、逐字字幕、镜头跟读列全部复用）。场景参数见 config.example.json 的 `scene` 与每句 `sentences[].scene`（mountains / mount_base / moon / moon_y / water_y / cliff / boat）。
- 实渲样片：/workspace/书法展示样片-诗意叠化/成片/后赤壁赋节选_诗意叠化_v1_1080x1920.mp4
