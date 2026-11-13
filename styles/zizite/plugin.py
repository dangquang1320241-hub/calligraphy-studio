#!/usr/bin/env python3
"""逐字特写风格插件（B 族）：Ken Burns 走4-5处精彩点 + 点评。needs_text=False（highlights 可全自定义）。"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from core import stylekit  # noqa: E402

NAME = "逐字特写"
NEEDS_TEXT = False
GRANULARITY = "char-focus"

_DEFAULTS = json.loads((Path(__file__).parent / "config.defaults.json").read_text(encoding="utf-8"))


def build_config(profile: dict, project_dir: Path, params: dict | None = None) -> dict:
    params = dict(params or {})
    prof_dir = Path(profile["_dir"])
    cfg = dict(_DEFAULTS)
    cfg.update(stylekit.common_artwork_fields(profile, project_dir, prof_dir))
    cfg.update({
        "output_name": params.get("output_name") or f"{profile['slug']}_{NAME}",
        "title": params.get("title", f"《{profile['slug']}》"),
        "subtitle": params.get("subtitle", "行草"),
        "author": params.get("author", "作者名"),
        "author_note": params.get("author_note", ""),
        "seal_char": params.get("seal_char", (params.get("author", "书") or "书")[0]),
        "passage": params.get("passage", profile["transcription"].get("text", "")),
        "passage_lines": params.get("passage_lines") or stylekit.passage_lines(profile["transcription"].get("text", "")),
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
    return {"config": str(cfg_path), "note": "highlights 为自动选点+草稿点评，请人工改写 note",
            "durations_estimate": f"~{cfg['video_b']['duration']:.0f}s"}


def _draft_highlights(profile: dict) -> list:
    n = len(profile["layout"]["columns"])
    picks = [(1, 1), (2, 1), (4, 1), (6, 1), (max(1, n), 1)]
    seen, out = set(), []
    for ci, ki in picks:
        if ci <= n and ki <= len(profile["layout"]["columns"][ci - 1]["chars"]):
            ch = profile["layout"]["columns"][ci - 1]["chars"][ki - 1]
            if ch.get("text") and ch["text"] not in seen:
                seen.add(ch["text"])
                out.append({"char": ch["text"], "note": "（点评草稿，请人工确认）",
                            "box": stylekit.char_box_norm(profile, ci, ki)})
    return out[:5]

# 该渲染器 CLI 为 --config/--out（B 族约定），编排器经 render_cmd 调用
render_cmd = stylekit.render_cmd_b("zizite", "render_zizite.py")
