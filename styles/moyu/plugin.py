#!/usr/bin/env python3
"""墨色呼吸风格插件（B 族）：治愈系慢镜头，无需释文。

meta:
  needs_text = False
几何复用档案：perspective 四角直接取自档案（与校正图同一结果，免去重复检测）。
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

NAME = "墨色呼吸"
NEEDS_TEXT = False
GRANULARITY = "ambient"

_DEFAULTS = json.loads((Path(__file__).parent / "config.defaults.json").read_text(encoding="utf-8"))


def build_config(profile: dict, project_dir: Path, params: dict | None = None) -> dict:
    params = dict(params or {})
    prof_dir = Path(profile["_dir"])
    cfg = dict(_DEFAULTS)
    corners = profile["geometry"]["info"].get("corners_src")
    cfg.update({
        "output_name": params.get("output_name") or f"{profile['slug']}_{NAME}",
        "artwork": str((project_dir / "素材" / "作品原图.jpg").resolve()),
        # 用档案四角透视拉正（确定性复用，等价 auto_deskew 的结果）
        "geometry": {"perspective": corners} if corners else {"auto_deskew": True},
        "geometry_prep": params.get("geometry_prep", {"scale": 2.0, "inset_px": 6}),
        "cleaned": str((project_dir / "素材" / "作品_clean_校正.png").resolve()),
        "cleaned_scale": 2.0,
        "title": params.get("title", f"《{profile['slug']}》"),
        "subtitle": params.get("subtitle", "行草"),
        "opening_line": params.get("opening_line", "墨落纸上，自有呼吸"),
        "author": params.get("author", "作者名"),
        "author_note": params.get("author_note", ""),
        "seal_char": params.get("seal_char", (params.get("author", "书") or "书")[0]),
    })
    for k in ("focus_points", "n_shots", "min_magnification", "max_magnification", "sharpen",
              "timing", "audio", "loudness_lufs"):
        if k in params:
            cfg[k] = params[k]
    (project_dir / "源码").mkdir(parents=True, exist_ok=True)
    (project_dir / "素材").mkdir(exist_ok=True)
    import shutil
    shutil.copy(prof_dir / "作品原图.jpg", project_dir / "素材" / "作品原图.jpg")
    cfg_path = project_dir / "源码" / "config.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    n_shots = cfg.get("n_shots", 5)
    est = cfg["timing"]["shot_seconds"] * n_shots + cfg["timing"]["opening_text_seconds"] + cfg["timing"]["end_card_seconds"]
    return {"config": str(cfg_path), "note": "B 族：无需释文；镜头自动选 5 处局部",
            "durations_estimate": f"~{est:.0f}s"}
