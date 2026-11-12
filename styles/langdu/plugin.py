#!/usr/bin/env python3
"""朗读版风格插件：作品档案 + 参数 → config.json（自动化了 2026-11-11 手工做的全部步骤）。

meta:
  needs_text = True   释文必填（朗读的对象）
"""
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from core.profile import PUNCT, split_clauses, strip_punct  # noqa: E402

NAME = "朗读版"
NEEDS_TEXT = True
GRANULARITY = "sentence"

# 默认版式/节奏/音乐（沿用模板 config.example.json 的风格基线）
DEFAULTS = {
    "tts": {"voice": "zh-CN-YunjianNeural", "rate": "-30%", "pitch": "-2Hz", "volume": "+0%"},
    "timing": {"title_secs": 3.6, "intro_secs": 2.6, "gap_secs": 1.1, "tail_hold": 2.0,
               "pull_back_secs": 2.0, "end_secs": 4.5},
    "camera": {"push": 0.045, "move_secs": 1.15, "lead_secs": 0.6, "spotlight": 0.3},
    "grade": {"deyellow": 0.25, "contrast": 1.12, "brightness": 1.04},
    "music": {"seed": 7, "root": 146.83, "bpm": 56, "density": 0.6, "xiao": 1.0,
              "reverb_secs": 3.2, "wet": 0.32, "music_db": -20, "duck_db": -7},
    "loudness": {"I": -16, "TP": -1.5, "LRA": 11},
    "layout": {
        "width": 1080, "height": 1920, "fps": 30, "bg_color": "#1b1815",
        "focus_window": [150, 170, 810, 1270],
        "full_window": [50, 300, 930, 1240],
        "subtitle": {"size": 76, "color": "#efe6d2", "center_x": 480, "center_y": 1400,
                     "dim_alpha": 0.42, "stroke": 2},
        "title": {"size": 96, "sub_size": 56, "center_x": 480, "center_y": 300, "dim": 0.3},
        "end": {"size": 76, "note_size": 36, "center_x": 480, "center_y": 1300, "dim": 0.45},
    },
}


def _apply_pronunciation(clause: str, pron: dict) -> str:
    out = []
    for ch in clause:
        entry = pron.get(ch)
        if isinstance(entry, dict) and entry.get("tts_char"):
            out.append(entry["tts_char"])
        else:
            out.append(ch)
    return "".join(out)


def build_config(profile: dict, project_dir: Path, params: dict | None = None) -> dict:
    """生成 config.json 与项目骨架。params 可覆盖：title/title_sub/author/sentences/tts/timing/…"""
    params = dict(params or {})
    project_dir = Path(project_dir)
    prof_dir = Path(profile["_dir"])
    W, H = profile["geometry"]["size"]
    ink = profile["ink_bbox"]
    y0, y1 = max(0, ink[1] - 8), min(H, ink[3] + 8)
    pron = profile.get("pronunciation", {})

    # 1) 字 → 列 映射（档案 layout 的 chars 顺序 = 全篇阅读顺序）
    cols = profile["layout"]["columns"]
    char_cols = []  # [(char, col_index)]
    for c in cols:
        for ch in c["chars"]:
            if ch.get("text"):
                char_cols.append((ch["text"], c["index"]))
    col_px = {}
    for c in cols:
        x0n, y0n, x1n, y1n = c["box"]
        col_px[c["index"]] = (x0n * W, x1n * W)

    # 2) 释文 → 短语 → 句（默认逐短语；params['sentences'] 可完全自定义）
    text = profile["transcription"]["text"]
    if not text:
        raise ValueError("作品档案缺释文：请先 build_profile --text 或补 释文.txt")
    flat = strip_punct(text)
    if flat != "".join(ch for ch, _ in char_cols):
        raise ValueError("释文与档案 layout 字序列不一致（疑列框/释文改动后未重建档案）")
    custom = params.get("sentences")
    if custom:
        # 自定义句：按字序在全文中定位，映射列范围
        pos = 0
        sentences = []
        for s in custom:
            sub = strip_punct(s["text"])
            i = flat.find(sub, pos)
            if i < 0:
                raise ValueError(f"自定义句在释文中找不到或顺序不符：{s['text']}")
            idxs = sorted({ci for _, ci in char_cols[i:i + len(sub)]})
            pos = i + len(sub)
            sentences.append({**s, "_cols": idxs})
    else:
        clauses = split_clauses(text)
        # 给每个短语找列范围
        pos = 0
        sentences = []
        for cl in clauses:
            n = len(strip_punct(cl))
            idxs = [ci for _, ci in char_cols[pos:pos + n]]
            pos += n
            sentences.append({"text": cl, "_cols": sorted(set(idxs))})

    # 3) region：句覆盖列的并集（校正图像素）
    pad_x = 10
    out_sentences = []
    for i, s in enumerate(sentences):
        idxs = s.get("_cols")
        if idxs is None:  # 自定义句：按字序重新映射
            idxs = sorted({ci for _, ci in char_cols})
        x0 = min(col_px[j][0] for j in idxs) - pad_x
        x1 = max(col_px[j][1] for j in idxs) + pad_x
        sub = strip_punct(s["text"])
        out_sentences.append({
            "text": s["text"],
            "subtitle": s.get("subtitle") or sub,
            "tts_text": s.get("tts_text") or _apply_pronunciation(s["text"], pron),
            "cols": "第" + "、".join(str(j + 1) for j in reversed(idxs)) + "列（右起）",
            "region": [int(x0), int(y0), int(x1), int(y1)],
            **({"gap_after": s["gap_after"]} if s.get("gap_after") else ({"gap_after": 0.8} if i == len(sentences) - 1 else {})),
        })

    # 4) 组装 config
    slug = profile["slug"]
    author = params.get("author", "作者名")
    cfg = {
        "output_name": params.get("output_name") or f"{slug}_{NAME}",
        "image": str((project_dir / "素材" / "作品原图.jpg").resolve()),
        "paper_crop": profile["geometry"]["paper_rect"],
        "paper_cutout": True,
        "grade": params.get("grade", DEFAULTS["grade"]),
        "title": params.get("title", slug),
        "title_sub": params.get("title_sub", ""),
        "author": author,
        "end_card_text": params.get("end_card_text", "{author} · 书"),
        "end_card_note": params.get("end_card_note", ""),
        "font": None,
        "tts": {**DEFAULTS["tts"], **params.get("tts", {})},
        "tts_provider": params.get("tts_provider", "edge"),
        "timing": {**DEFAULTS["timing"], **params.get("timing", {})},
        "camera": {**DEFAULTS["camera"], **params.get("camera", {})},
        "sentences": out_sentences,
        "music": {**DEFAULTS["music"], **params.get("music", {})},
        "loudness": params.get("loudness", DEFAULTS["loudness"]),
        "layout": {**DEFAULTS["layout"], **params.get("layout", {})},
        "geometry": {"auto_deskew": True, "margin_px": 40,
                     "cache": str((prof_dir / "作品_校正.png").resolve())},
        "_note_regions": "region 为校正后图像像素（paper_rect 内）；由作品档案 layout 自动生成",
    }
    cfg["title"] = cfg["title"] if cfg["title"] != slug else f"《{slug}》"
    if not cfg["title_sub"]:
        cfg["title_sub"] = params.get("title_sub", "行草")

    # 5) 项目骨架
    (project_dir / "源码").mkdir(parents=True, exist_ok=True)
    (project_dir / "素材").mkdir(exist_ok=True)
    shutil.copy(prof_dir / "作品原图.jpg", project_dir / "素材" / "作品原图.jpg")
    cfg_path = project_dir / "源码" / "config.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    lic = project_dir / "素材" / "LICENSES.md"
    lic.write_text(
        f"# 素材来源与许可\n\n"
        f"| 素材 | 来源 | 许可 |\n|---|---|---|\n"
        f"| 作品原图.jpg | {profile['source']['file']}（sha256 {profile['source']['sha256'][:16]}…） | 委托方自有 |\n"
        f"| 字体 | 霞鹜文楷（OFL 1.1） | SIL OFL |\n"
        f"| 底乐 | common/music_synth.py 自制 | 可商用 |\n"
        f"| 朗读 | {cfg['tts_provider']}-tts | **商用许可未确认** |\n", encoding="utf-8")
    report = {
        "config": str(cfg_path),
        "n_sentences": len(out_sentences),
        "n_columns": profile["layout"]["n_columns"],
        "durations_estimate": "~%.0fs" % (
            DEFAULTS["timing"]["title_secs"] + DEFAULTS["timing"]["intro_secs"]
            + sum(0.9 + 0.55 * len(strip_punct(s["text"])) for s in out_sentences)
            + DEFAULTS["timing"]["tail_hold"] + DEFAULTS["timing"]["pull_back_secs"]
            + DEFAULTS["timing"]["end_secs"]),
        "pronunciation_applied": {k: v.get("tts_char") for k, v in pron.items() if isinstance(v, dict)},
    }
    return report
