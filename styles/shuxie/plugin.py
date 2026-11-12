#!/usr/bin/env python3
"""书写路径风格插件（A 族）：strokes 自动草稿 = 逐字矩形块（自上而下逐字揭开）。

meta:
  needs_text = False
⚠️ 半自动：矩形块 ≠ 真实笔画级多边形；正式出片请人工在 config 上把 strokes 精修为笔画级。
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

NAME = "书写路径"
NEEDS_TEXT = False
GRANULARITY = "stroke-reveal"

DEFAULTS = {
    "grade": {"deyellow": 0.25, "contrast": 1.12, "brightness": 1.04},
    "timing": {"intro_secs": 2.5, "pause": 0.08, "hold_secs": 2.0, "end_secs": 3.5},
    "music": {"seed": 21, "root": 146.83, "bpm": 50, "density": 0.45, "xiao": 0.8,
              "accent_every": 3, "music_db": -20, "duck_db": -7},
    "loudness": {"I": -16, "TP": -1.5, "LRA": 11},
    "layout": {"width": 1080, "height": 1920, "fps": 30, "bg_color": "#1b1815"},
}


def draft_strokes(profile: dict) -> list:
    """逐列逐字的矩形揭示块（示意笔顺：先列后字、字内自上而下）。"""
    strokes = []
    for col in profile["layout"]["columns"]:
        for ch in col["chars"]:
            x0, y0, x1, y1 = [round(v * s) for v, s in zip(ch["box"], (profile["geometry"]["size"][0],
                                                                       profile["geometry"]["size"][1],
                                                                       profile["geometry"]["size"][0],
                                                                       profile["geometry"]["size"][1]))]
            if x1 <= x0 or y1 <= y0:
                continue
            strokes.append({"poly": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                            "secs": max(0.4, round((y1 - y0) / 380.0, 2)), "dir": [0, 1]})
    return strokes


def build_config(profile: dict, project_dir: Path, params: dict | None = None) -> dict:
    params = dict(params or {})
    prof_dir = Path(profile["_dir"])
    strokes = params.get("strokes") or draft_strokes(profile)
    total = sum(s.get("secs", 0.6) for s in strokes) + DEFAULTS["timing"]["pause"] * len(strokes)
    cfg = {
        "output_name": params.get("output_name") or f"{profile['slug']}_{NAME}",
        "image": str((project_dir / "素材" / "作品原图.jpg").resolve()),
        "geometry": {"auto_deskew": True, "margin_px": 40,
                     "cache": str((prof_dir / "作品_校正.png").resolve())},
        "paper_crop": profile["geometry"]["paper_rect"],
        "grade": params.get("grade", DEFAULTS["grade"]),
        "title": params.get("title", f"《{profile['slug']}》"),
        "author": params.get("author", "作者名"),
        "end_card_text": params.get("end_card_text", "{author} · 书"),
        "font": None,
        "strokes": strokes,
        "focus": params.get("focus", []),
        "timing": {**DEFAULTS["timing"], **params.get("timing", {})},
        "music": {**DEFAULTS["music"], **params.get("music", {})},
        "loudness": params.get("loudness", DEFAULTS["loudness"]),
        "layout": {**DEFAULTS["layout"], **params.get("layout", {})},
        "_note": "strokes 为逐字矩形草稿（示意），正式使用请精修为笔画级多边形",
    }
    (project_dir / "源码").mkdir(parents=True, exist_ok=True)
    (project_dir / "素材").mkdir(exist_ok=True)
    import shutil
    shutil.copy(prof_dir / "作品原图.jpg", project_dir / "素材" / "作品原图.jpg")
    cfg_path = project_dir / "源码" / "config.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    t = cfg["timing"]
    return {"config": str(cfg_path), "n_strokes": len(strokes),
            "note": "strokes=逐字矩形示意草稿（40 块级）；正式出片前请人工精修笔画级",
            "durations_estimate": f"~{t['intro_secs'] + total + t['hold_secs'] + t['end_secs']:.0f}s"}
