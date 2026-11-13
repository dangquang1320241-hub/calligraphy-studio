#!/usr/bin/env python3
"""作品入画风格插件（B 族）：作品装裱进 4 个场景，无需释文。

meta:
  needs_text = False
场景底图为工具库自带示意示意 scenes/*.jpg；字幕/角标可在 params 覆盖。
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

NAME = "作品入画"
NEEDS_TEXT = False
GRANULARITY = "scenes"

_DEFAULTS = json.loads((Path(__file__).parent / "config.defaults.json").read_text(encoding="utf-8"))
SCENES_DIR = Path(__file__).parent / "scenes"


def build_config(profile: dict, project_dir: Path, params: dict | None = None) -> dict:
    params = dict(params or {})
    prof_dir = Path(profile["_dir"])
    cfg = dict(_DEFAULTS)
    corners = profile["geometry"]["info"].get("corners_src")
    scenes = []
    for s in _DEFAULTS.get("scenes", []):
        s = dict(s)
        bg = Path(s["background"]).name
        local = SCENES_DIR / bg
        s["background"] = str(local.resolve()) if local.exists() else s["background"]
        scenes.append(s)
    cfg.update({
        "output_name": params.get("output_name") or f"{profile['slug']}_{NAME}",
        "artwork": str((project_dir / "素材" / "作品原图.jpg").resolve()),
        "geometry": {"perspective": corners} if corners else {"auto_deskew": True},
        "geometry_prep": params.get("geometry_prep", {"scale": 2.0, "inset_px": 6}),
        "cleaned": str((project_dir / "素材" / "作品_clean_校正.png").resolve()),
        "cleaned_scale": 2.0,
        "title": params.get("title", f"《{profile['slug']}》"),
        "title_sub": params.get("title_sub", "行草"),
        "subtitle": params.get("subtitle", params.get("title_sub", "行草")),
        "author": params.get("author", "作者名"),
        "author_note": params.get("author_note", ""),
        "seal_char": params.get("seal_char", (params.get("author", "书") or "书")[0]),
        "tag_text": params.get("tag_text", "场景为示意"),
        "scenes": params.get("scenes") or scenes,
    })
    for k in ("timing", "music", "loudness_lufs", "caption_y_px"):
        if k in params:
            cfg[k] = params[k]
    (project_dir / "源码").mkdir(parents=True, exist_ok=True)
    (project_dir / "素材").mkdir(exist_ok=True)
    import shutil
    shutil.copy(prof_dir / "作品原图.jpg", project_dir / "素材" / "作品原图.jpg")
    cfg_path = project_dir / "源码" / "config.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    est = cfg["timing"]["title_seconds"] + len(cfg["scenes"]) * cfg["timing"]["scene_seconds"] + cfg["timing"]["end_card_seconds"]
    return {"config": str(cfg_path), "note": f"B 族：{len(cfg['scenes'])} 个场景（示意底图随工具库分发，角标「场景为示意」）",
            "durations_estimate": f"~{est:.0f}s"}

# 该渲染器 CLI 为 --config/--out（B 族约定），编排器经 render_cmd 调用
from core import stylekit as _sk  # noqa: E402
render_cmd = _sk.render_cmd_b("ruhua", "render_ruhua.py")
