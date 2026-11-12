#!/usr/bin/env python3
"""临帖对比风格插件：本作字框自动取自档案，参照帖字框与点评必须人工提供。

meta:
  needs_text = False   # 需要 params['chars']（对比哪些字）与 params['reference']（参照帖）
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

NAME = "临帖对比"
NEEDS_TEXT = False
GRANULARITY = "char-pair"

DEFAULTS = {
    "grade": {"deyellow": 0.25, "contrast": 1.12, "brightness": 1.04},
    "timing": {"intro_secs": 4.2, "shrink_secs": 1.0, "char_secs": 4.615, "slide_in": 0.55,
               "slide_out": 0.45, "comment_delay": 0.45, "grow_secs": 1.3, "end_secs": 4.0},
    "music": {"seed": 11, "root": 146.83, "bpm": 52, "density": 0.55, "xiao": 1.0,
              "reverb_secs": 3.4, "wet": 0.34, "accent_on_cut": True},
    "loudness": {"I": -18, "TP": -1.5, "LRA": 11},
    "layout": {"width": 1080, "height": 1920, "fps": 30, "bg_color": "#1b1815", "accent": "#b03a2e",
               "full_window": [50, 420, 930, 1200], "map_window": [90, 230, 870, 690],
               "title": {"size": 96, "sub_size": 48, "note_size": 32, "center_x": 490, "center_y": 250},
               "header": {"size": 32, "center_x": 480, "center_y": 190},
               "tile": {"size": 400, "pad": 22, "y": 750, "ref_x": 50, "work_x": 490, "slide_px": 520,
                        "stagger_px": 80, "bg": "auto", "border_color": "#8c7b62", "border": 2},
               "tile_label": {"size": 30, "gap": 8},
               "comment": {"x": 70, "y": 1228, "width": 800, "size": 42, "head_size": 48},
               "end": {"size": 76, "note_size": 30, "center_x": 490, "center_y": 1330, "dim": 0.4,
                       "credit_size": 24}},
}


def _find_work_box(profile: dict, ch: str, pad: float = 0.10) -> list:
    W, H = profile["geometry"]["size"]
    for col in profile["layout"]["columns"]:
        for c in col["chars"]:
            if c.get("text") == ch:
                x0, y0, x1, y1 = [v * s for v, s in zip(c["box"], (W, H, W, H))]
                pw, ph = (x1 - x0) * pad, (y1 - y0) * pad
                return [int(x0 - pw), int(y0 - ph), int(x1 + pw), int(y1 + ph)]
    raise ValueError(f"作品档案中找不到「{ch}」；请确认释文/列框确认图")


def build_config(profile: dict, project_dir: Path, params: dict | None = None) -> dict:
    params = dict(params or {})
    prof_dir = Path(profile["_dir"])
    if not params.get("chars"):
        raise ValueError("临帖对比需要 params['chars']：[{'char':'江','ref_box':[x0,y0,x1,y1],'comment':'…'}]（ref_box 为参照帖像素）")
    if not params.get("reference", {}).get("image"):
        raise ValueError("临帖对比需要 params['reference']['image']（参照帖图片，须已确认许可）")
    chars = []
    for c in params["chars"]:
        wb = c.get("work_box") or _find_work_box(profile, c["char"])
        chars.append({"char": c["char"], "ref_box": c["ref_box"], "work_box": wb,
                      "comment": c.get("comment", "（点评草稿，请人工撰写）")})
    ref = params["reference"]
    cfg = {
        "output_name": params.get("output_name") or f"{profile['slug']}_{NAME}",
        "work": {"image": str((project_dir / "素材" / "作品原图.jpg").resolve()),
                 "paper_crop": profile["geometry"]["paper_rect"], "paper_cutout": True,
                 "grade": DEFAULTS["grade"], "tile_label": params.get("work_tile_label", "本作"),
                 "geometry": {"auto_deskew": True, "margin_px": 40,
                              "cache": str((prof_dir / "作品_校正.png").resolve())}},
        "reference": {"image": ref["image"], "label": ref.get("label", "参照"),
                      "tile_label": ref.get("tile_label", "参照"),
                      "grade": ref.get("grade", {"deyellow": 0.1, "contrast": 1.06, "brightness": 1.02}),
                      "credit": ref.get("credit", "")},
        "chars": chars,
        "title": params.get("title", f"《{profile['slug']}》临帖对比"),
        "title_sub": params.get("title_sub", ""),
        "header": params.get("header", ""),
        "author": params.get("author", "作者名"),
        "end_card_text": params.get("end_card_text", "{author} · 书"),
        "font": None,
        "timing": {**DEFAULTS["timing"], **params.get("timing", {})},
        "music": {**DEFAULTS["music"], **params.get("music", {})},
        "loudness": params.get("loudness", DEFAULTS["loudness"]),
        "layout": {**DEFAULTS["layout"], **params.get("layout", {})},
        "_note": "work_box 自动取自作品档案；ref_box/点评必须人工提供（或 LLM 草稿经确认）",
    }
    (project_dir / "源码").mkdir(parents=True, exist_ok=True)
    (project_dir / "素材").mkdir(exist_ok=True)
    import shutil
    shutil.copy(prof_dir / "作品原图.jpg", project_dir / "素材" / "作品原图.jpg")
    cfg_path = project_dir / "源码" / "config.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"config": str(cfg_path), "n_chars": len(chars),
            "note": "参照帖与点评由 params 提供；验机可用自参照模式",
            "durations_estimate": "~%.0fs" % (DEFAULTS["timing"]["intro_secs"]
                                              + len(chars) * DEFAULTS["timing"]["char_secs"]
                                              + DEFAULTS["timing"]["grow_secs"] + DEFAULTS["timing"]["end_secs"])}
