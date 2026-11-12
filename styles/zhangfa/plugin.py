#!/usr/bin/env python3
"""章法解析风格插件：inkseg 检测参数 + 自动图层（qi/colboxes/charboxes/gaps）。

meta:
  needs_text = True   # inkseg.columns_text 需要逐列释文（也用于字框对位）
图层 caption 是草稿，请人工确认措辞（确认点 2 的精神）。
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from core.profile import layout_px_pixels  # noqa: E402

NAME = "章法解析"
NEEDS_TEXT = True
GRANULARITY = "layout-annotate"

DEFAULTS = {
    "grade": {"deyellow": 0.25, "contrast": 1.12, "brightness": 1.04},
    "tts": {"enabled": False, "voice": "zh-CN-YunjianNeural", "rate": "-30%", "pitch": "-2Hz", "volume": "+0%"},
    "timing": {"intro_secs": 3.0, "end_secs": 4.0},
    "music": {"seed": 11, "root": 146.83, "bpm": 56, "density": 0.5, "xiao": 0.8,
              "reverb_secs": 3.2, "wet": 0.32, "fade_in": 1.5, "fade_out": 3.0},
    "loudness": {"I": -18, "TP": -1.5, "LRA": 11},
    "layout": {"width": 1080, "height": 1920, "fps": 30, "bg_color": "#1b1815",
               "window": [20, 400, 1060, 1030], "caption_x": 90, "caption_y": 1140,
               "caption_width": 780, "caption_size": 46, "text_center_x": 500,
               "title_y": 150, "title_size": 80, "end_y": 1150, "end_size": 64},
}


def default_layers(n_cols: int) -> list:
    return [
        {"type": "qi", "secs": 5.5, "color": "#e0452f", "width": 7,
         "caption": f"行气：红线连起每列墨迹的左右重心（共{n_cols}列，自右向左）。"},
        {"type": "colboxes", "secs": 5.5, "color": "#d4a017", "width": 5,
         "caption": f"{n_cols}列的边界框；列与列的间距在这层看得最清楚。"},
        {"type": "charboxes", "cols": [1, 2] if n_cols >= 2 else [1], "color": "#5dade2", "width": 5,
         "label_size": 30, "caption": "字框：同一列内字形大小与重心参差。"},
        {"type": "gaps", "secs": 5.5, "keep": True, "color": "#e67e22", "width": 6,
         "caption": "疏密：色块为列间留白，越深越宽。"},
        {"type": "qi", "secs": 5.0, "color": "#e0452f", "width": 7,
         "caption": "合看：行气线、列距与字形大小，构成这幅作品的章法。"},
    ]


def build_config(profile: dict, project_dir: Path, params: dict | None = None) -> dict:
    params = dict(params or {})
    prof_dir = Path(profile["_dir"])
    per_col = profile["transcription"].get("per_column") or []
    if not per_col:
        raise ValueError("章法解析需要逐列释文（作品档案 transcription.per_column 为空）")
    cfg = {
        "output_name": params.get("output_name") or f"{profile['slug']}_{NAME}",
        "image": str((project_dir / "素材" / "作品原图.jpg").resolve()),
        "geometry": {"auto_deskew": True, "margin_px": 40,
                     "cache": str((prof_dir / "作品_校正.png").resolve())},
        "paper_crop": profile["geometry"]["paper_rect"],
        "paper_cutout": True,
        "grade": params.get("grade", DEFAULTS["grade"]),
        "layout_px": layout_px_pixels(profile),
        "inkseg": {"crop": profile["geometry"]["paper_rect"],
                   "columns_text": per_col,
                   "signature": bool(params.get("signature", False))},
        "title": params.get("title", f"《{profile['slug']}》章法解析"),
        "title_sub": params.get("title_sub", ""),
        "author": params.get("author", "作者名"),
        "end_card_text": params.get("end_card_text", "{author} · 书"),
        "font": None,
        "tts": {**DEFAULTS["tts"], **params.get("tts", {})},
        "timing": {**DEFAULTS["timing"], **params.get("timing", {})},
        "music": {**DEFAULTS["music"], **params.get("music", {})},
        "loudness": params.get("loudness", DEFAULTS["loudness"]),
        "layout": {**DEFAULTS["layout"], **params.get("layout", {})},
        "layers": params.get("layers") or default_layers(profile["layout"]["n_columns"]),
        "_note": "layers.caption 为自动草稿，发布前请人工确认措辞；columns_text 右→左",
    }
    (project_dir / "源码").mkdir(parents=True, exist_ok=True)
    (project_dir / "素材").mkdir(exist_ok=True)
    import shutil
    shutil.copy(prof_dir / "作品原图.jpg", project_dir / "素材" / "作品原图.jpg")
    cfg_path = project_dir / "源码" / "config.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"config": str(cfg_path), "n_columns": profile["layout"]["n_columns"],
            "layers": len(cfg["layers"]),
            "note": "图层与说明为自动草稿；tts 默认关闭（纯底乐）",
            "durations_estimate": "~%.0fs" % (DEFAULTS["timing"]["intro_secs"]
                                              + sum(l.get("secs", 5.5) for l in cfg["layers"])
                                              + DEFAULTS["timing"]["end_secs"])}
