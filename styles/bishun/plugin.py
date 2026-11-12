#!/usr/bin/env python3
"""笔顺复现风格插件（B 族）：墨迹按示意笔顺"写"出来。needs_text=True（columns_text 对位）。"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

NAME = "笔顺复现"
NEEDS_TEXT = True
GRANULARITY = "stroke-order"

_DEFAULTS = json.loads((Path(__file__).parent / "config.defaults.json").read_text(encoding="utf-8"))


def build_config(profile: dict, project_dir: Path, params: dict | None = None) -> dict:
    params = dict(params or {})
    prof_dir = Path(profile["_dir"])
    per_col = profile["transcription"].get("per_column") or []
    if not per_col:
        raise ValueError("笔顺复现需要逐列释文（columns_text 用于字序对位）")
    cfg = dict(_DEFAULTS)
    corners = profile["geometry"]["info"].get("corners_src")
    cfg.update({
        "output_name": params.get("output_name") or f"{profile['slug']}_{NAME}",
        "artwork": str((project_dir / "素材" / "作品原图.jpg").resolve()),
        "geometry": {"perspective": corners} if corners else {"auto_deskew": True},
        "geometry_prep": params.get("geometry_prep", {"scale": 2.0, "inset_px": 6}),
        "cleaned": str((project_dir / "素材" / "作品_clean_校正.png").resolve()),
        "cleaned_scale": 2.0,
        "title": params.get("title", f"《{profile['slug']}》"),
        "title_sub": params.get("title_sub", "行草"),
        "author": params.get("author", "作者名"),
        "author_note": params.get("author_note", ""),
        "seal_char": params.get("seal_char", (params.get("author", "书") or "书")[0]),
        "columns_text": per_col,
        "has_signature": bool(params.get("has_signature", False)),
        "char_cuts": params.get("char_cuts", {}),
        "layout": "auto",
        "tag_text": params.get("tag_text", "笔顺为示意"),
    })
    for k in ("stage_top_px", "paper_height_px", "caption_y_px", "end_paper_width_px",
              "end_paper_top_px", "timing", "music", "loudness_lufs", "fps"):
        if k in params:
            cfg[k] = params[k]
    (project_dir / "源码").mkdir(parents=True, exist_ok=True)
    (project_dir / "素材").mkdir(exist_ok=True)
    import shutil
    shutil.copy(prof_dir / "作品原图.jpg", project_dir / "素材" / "作品原图.jpg")
    cfg_path = project_dir / "源码" / "config.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    t = cfg["timing"]
    est = t["title_seconds"] + t["lead_in"] + t["writing_seconds"] + t["zoom_out_seconds"] + t["end_card_seconds"]
    return {"config": str(cfg_path), "note": "笔顺为算法示意（角标已固定）；char_cuts 可修切字",
            "durations_estimate": f"~{est:.0f}s"}
