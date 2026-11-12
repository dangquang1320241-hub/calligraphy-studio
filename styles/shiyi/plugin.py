#!/usr/bin/env python3
"""诗意叠化风格插件：完全复用朗读版 config，加上 scene 场景参数。

meta:
  needs_text = True
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from styles.langdu import plugin as langdu  # noqa: E402

NAME = "诗意叠化"
NEEDS_TEXT = True
GRANULARITY = "sentence"

DEFAULT_SCENE = {
    "seed": 3,
    "bg": "#1b1815",
    "intro_scene": {"mountains": 3, "moon": True},
    "drift_px": 60,
    "subtitle_band": 110,
    "subtitle_dim": 0.6,
}


def build_config(profile: dict, project_dir: Path, params: dict | None = None) -> dict:
    params = dict(params or {})
    params.setdefault("output_name", f"{profile['slug']}_{NAME}")
    report = langdu.build_config(profile, project_dir, params)
    cfg_path = Path(report["config"])
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    scene = dict(DEFAULT_SCENE)
    scene.update(params.get("scene", {}))
    cfg["scene"] = scene
    cfg["_note"] = "scene 为代码生成水墨示意背景；如需发布可加「示意」角标"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    report["note"] = "复用朗读版插件生成句/列/region；scene 用默认水墨参数（可 params['scene'] 覆盖）"
    return report
