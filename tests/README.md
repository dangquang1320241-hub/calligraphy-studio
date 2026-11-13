# 测试设计（分支覆盖矩阵）

原则：**单元/接口测试用合成作品图 + 临时数据根**（绝不触碰真实档案）；联网/渲染类测试用环境变量门控。

## 分支 → 用例映射

### core/profile.py
| 分支 | 用例 |
|---|---|
| strip_punct 含换行/全角标点 | test_strip_punct（回归：\n 曾被当字计数） |
| split_clauses 短语切分/尾部无标点 | test_split_clauses |
| _distribute 精确分配 | test_distribute_ok |
| _distribute 字数不匹配 → 报错并列出各列检出 | test_distribute_mismatch（回归：三槐堂铭 33≠40） |
| build_profile：释文+自动估列 | test_build_auto_estimate |
| build_profile：释文+col_counts | test_build_with_counts |
| build_profile：释文+per_column（权威切字，落款不加1） | test_build_per_column |
| build_profile：无释文（纯自动出列框，回归 layout=None 崩溃） | test_build_no_text |
| build_profile：无释文+col_counts（占位切字，回归②重建崩溃） | test_build_no_text_counts |
| build_profile：per_column 与全文不一致 → 报错（回归 35≠33） | test_confirm_mismatch |
| per_column 模式 signature 不追加（落款含在末列文本里） | test_per_column_signature_no_extra |
| layout_px_pixels 归一化→像素 | test_layout_px_pixels |
| load_profile 缺失/sha 不匹配 | test_load_profile_errors |

### styles/langdu/plugin.py
| 分支 | 用例 |
|---|---|
| 默认逐短语切句 + 句↔列映射 + region 单调左移 | test_build_config_default |
| 自定义 sentences（含 region 覆盖） | test_build_config_custom |
| 读音替换写入 tts_text | test_pron_substitution |
| 末句 gap_after | test_last_gap |
| 释文与档案字序列不一致 → 报错 | test_text_layout_mismatch |

### core/tts.py
| 分支 | 用例 |
|---|---|
| PROVIDERS 注册返回实例而非类（回归） | test_provider_instance |
| cache_key 稳定性 | test_cache_key |
| synthesize_cached 命中缓存不联网（mock Provider） | test_cache_hit |
| cache miss 调 Provider 并写元数据 | test_cache_miss |

### core/llm.py
| 分支 | 用例 |
|---|---|
| import_transcription_draft 解析 JSON/容错前后文本 | test_import_draft |
| 回复无 JSON → 报错 | test_import_draft_bad |
| annotate_pronunciation 多音字标记 | test_annotate |
| export 包生成 | test_export_pkg |

### core/qc.py
| 分支 | 用例 |
|---|---|
| audio_align 人声在窗内 → ✓；窗外 → ✗ | test_audio_align（合成 wav+timeline） |
| ffprobe_check 对真实 mp4 | test_ffprobe_check（ffmpeg 生成 1s 测试片） |
| sheet_for 产出联系表 | test_sheet_for |

### webapp/app.py（TestClient + 临时数据根）
| 分支 | 用例 |
|---|---|
| /api/library、/api/styles | test_web_library_styles |
| /api/profiles/{slug} 摘要 + 裁图生成 | test_web_profile |
| /api/profiles/{slug}/pron | test_web_pron |
| rebuild：无释文档案按占位切字 | test_web_rebuild_no_text |
| confirm：逐列权威 + 读音写入 + 状态翻转 | test_web_confirm |
| 同作品任务互斥 → 409 | test_web_busy |
| make：任务启动与状态查询（预览渲，需网络 → 门控） | test_web_make（门控） |

### 集成（门控 RUN_RENDER_TESTS=1）
- 朗读版 --preview 5 全链路（真实 TTS）

## 运行
```bash
.venv/bin/python -m pytest tests/ -q          # 单元+接口（无网络）
RUN_RENDER_TESTS=1 .venv/bin/python -m pytest tests/ -q  # 含集成
.venv/bin/python -m pytest tests/ -q --cov=core --cov=styles --cov-report=term-missing
```
