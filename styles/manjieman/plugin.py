#!/usr/bin/env python3
"""慢揭幕风格插件（B 族）：逐行揭开 + 原文字幕。needs_text=True（passage 用释文）。"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from core import stylekit  # noqa: E402

NAME = "慢揭幕"
NEEDS_TEXT = True
GRANULARITY = "reveal"

_DEFAULTS = json.loads((Path(__file__).parent / "config.defaults.json").read_text(encoding="utf-8"))


def build_config(profile: dict, project_dir: Path, params: dict | None = None) -> dict:
    params = dict(params or {})
    prof_dir = Path(profile["_dir"])
    text = profile["transcription"]["text"]
    if not text:
        raise ValueError("慢揭幕需要释文（揭开时逐行显示原文）")
    cfg = dict(_DEFAULTS)
    cfg.update(stylekit.common_artwork_fields(profile, project_dir, prof_dir))
    cfg.update({
        "output_name": params.get("output_name") or f"{profile['slug']}_{NAME}",
        "title": params.get("title", f"《{profile['slug']}》"),
        "subtitle": params.get("subtitle", "行草"),
        "author": params.get("author", "作者名"),
        "author_note": params.get("author_note", ""),
        "seal_char": params.get("seal_char", (params.get("author", "书") or "书")[0]),
        "passage": text,
        "passage_lines": params.get("passage_lines") or stylekit.passage_lines(text),
        "columns": stylekit.columns_normalized(profile),
        "highlights": params.get("highlights") or _draft_highlights(profile),
    })
    for k in ("preprocess", "video_a", "video_b", "audio_tone", "safe_bottom_px", "safe_right_px",
              "width", "height", "fps", "loudness_lufs"):
        if k in params:
            cfg[k] = params[k]
    (project_dir / "源码").mkdir(parents=True, exist_ok=True)
    (project_dir / "素材").mkdir(exist_ok=True)
    import shutil
    shutil.copy(prof_dir / "作品原图.jpg", project_dir / "素材" / "作品原图.jpg")
    cfg_path = project_dir / "源码" / "config.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"config": str(cfg_path), "note": "highlights 的 note 为草稿占位，请人工润色",
            "durations_estimate": f"~{cfg['video_b']['duration']:.0f}s"}


def _draft_highlights(profile: dict) -> list:
    picks = [(1, 1), (3, 1), (5, 1), (7, 1)]
    out = []
    for ci, ki in picks:
        if ci <= len(profile["layout"]["columns"]) and ki <= len(profile["layout"]["columns"][ci - 1]["chars"]):
            ch = profile["layout"]["columns"][ci - 1]["chars"][ki - 1]
            if ch.get("text"):
                out.append({"char": ch["text"], "note": "（点评草稿，请人工确认）",
                            "box": stylekit.char_box_norm(profile, ci, ki)})
    return out

# 该渲染器 CLI 为 --config/--out（B 族约定），编排器经 render_cmd 调用
render_cmd = stylekit.render_cmd_b("manjieman", "render_manjieman.py")
