# 一字一讲模板
单字特写（作品 `char_box` 裁切，只裁切+调色+几何校正）+ 字形演变图片序列（`evolution[]`，必须是**真实、已确认许可**的图片，示例里是占位路径）+ 讲解 TTS 与逐字提亮字幕（复用朗读版）。每句 `sentences[].show` 指定画面：`"work"` 或 `"evo:序号"`，画面在该句开口前 0.4 秒交叉淡化切换。
用法：`python3 render_yizi.py --config <项目>/源码/config.json [--skip-tts] [--preview 10]`
讲解里的字形演变说法属于事实句，需按流程核查（Codex+DeepSeek）后再用。未实际渲染测试（只做了语法检查）。
