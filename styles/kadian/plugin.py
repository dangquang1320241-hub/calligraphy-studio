#!/usr/bin/env python3
"""节奏卡点风格插件：按拍切镜，默认每列一镜，镜头引用档案 layout.json。

meta:
  needs_text = False   # 不需要释文（纯视觉节奏）
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from core.profile import layout_px_pixels  # noqa: E402

NAME = "节奏卡点"
NEEDS_TEXT = False
GRANULARITY = "beat-shots"

DEFAULTS = {
    "grade": {"deyellow": 0.25, "contrast": 1.12, "brightness": 1.04},
    "music": {"seed": 51, "root": 146.83},
    "loudness": {"I": -16, "TP": -1.5, "LRA": 11},
    "layout": {"width": 1080, "height": 1920, "fps": 30, "bg_color": "#1b1815",
               "window": [100, 300, 860, 1440], "full_window": [20, 560, 1060, 1190],
               "max_scale": 2.4, "punch": 0.05, "push": 0.04, "flash": 0.08,
               "text_center_x": 480, "title_y": 150, "title_size": 64,
               "end_y": 1260, "end_size": 64},
}


def build_config(profile: dict, project_dir: Path, params: dict | None = None) -> dict:
    params = dict(params or {})
    prof_dir = Path(profile["_dir"])
    n_cols = profile["layout"]["n_columns"]
    rhythm = {"bpm": 90, "offset": 0.0, "beats_per_shot": 2}
    rhythm.update(params.get("rhythm", {}))
    shots = params.get("shots") or [{"col": i + 1, "note": f"第{i + 1}列"} for i in range(n_cols)]
    title_secs = params.get("title_secs", 2.0)
    end_secs = params.get("end_secs", 4.0)
    duration = round(len(shots) * rhythm["beats_per_shot"] * 60.0 / rhythm["bpm"], 2)
    cfg = {
        "output_name": params.get("output_name") or f"{profile['slug']}_{NAME}",
        "image": str((project_dir / "素材" / "作品原图.jpg").resolve()),
        "geometry": {"auto_deskew": True, "margin_px": 40,
                     "cache": str((prof_dir / "作品_校正.png").resolve())},
        "paper_crop": profile["geometry"]["paper_rect"],
        "paper_cutout": True,
        "grade": params.get("grade", DEFAULTS["grade"]),
        "layout_px": str((project_dir / "源码" / "layout_px.json").resolve()),
        "title": params.get("title", f"《{profile['slug']}》"),
        "title_secs": title_secs,
        "author": params.get("author", "作者名"),
        "end_card_text": params.get("end_card_text", "{author} · 书"),
        "end_secs": end_secs,
        "font": None,
        "rhythm": {**rhythm, "duration": duration},
        "shots": shots,
        "end_full": params.get("end_full", True),
        "music": {**DEFAULTS["music"], **params.get("music", {})},
        "loudness": params.get("loudness", DEFAULTS["loudness"]),
        "layout": {**DEFAULTS["layout"], **params.get("layout", {})},
        "_note": "layout_px 指向作品档案 layout.json（inkseg 归一化坐标）；shots 可用 {'col':n} 或 {'char':'n:k','pad':40}",
    }
    (project_dir / "源码").mkdir(parents=True, exist_ok=True)
    (project_dir / "素材").mkdir(exist_ok=True)
    import shutil
    shutil.copy(prof_dir / "作品原图.jpg", project_dir / "素材" / "作品原图.jpg")
    # layout_px（像素坐标，同章法解析缓存格式）→ 项目源码目录
    (project_dir / "源码" / "layout_px.json").write_text(
        json.dumps(layout_px_pixels(profile), ensure_ascii=False, indent=1), encoding="utf-8")
    cfg_path = project_dir / "源码" / "config.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"config": str(cfg_path), "n_shots": len(shots), "duration": duration,
            "note": f"默认每列一镜（{n_cols} 镜）；shots 可自定义字级特写",
            "durations_estimate": f"~{title_secs + duration + end_secs:.0f}s"}
