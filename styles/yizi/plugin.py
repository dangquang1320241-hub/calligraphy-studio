#!/usr/bin/env python3
"""一字一讲风格插件：单字特写 + 字形演变图 + 讲解 TTS。

meta:
  needs_text = False   # 讲解词走 params['sentences']（与作品释文无关）
必填 params：
  sentences: [{"text": "讲解词…", "show": "work" | "evo:<序号>"}]   # show 默认 work
可选 params：
  char: "列:字"（右起 1-based，默认 "1:1"）——特写哪个字
  evolution: [{"image": 路径, "label": "甲骨文", "crop": null}]（必须是已确认许可的真实图片）
  work_label / title / char_box(直接给像素) / tts / timing / music
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from core.profile import strip_punct  # noqa: E402
from styles.langdu.plugin import _apply_pronunciation  # noqa: E402

NAME = "一字一讲"
NEEDS_TEXT = False
GRANULARITY = "char-explain"

DEFAULTS = {
    "tts": {"voice": "zh-CN-YunjianNeural", "rate": "-20%", "pitch": "-2Hz", "volume": "+0%"},
    "timing": {"title_secs": 2.5, "intro_secs": 1.0, "gap_secs": 0.8, "tail_hold": 1.2,
               "pull_back_secs": 0.5, "end_secs": 3.0},
    "camera": {"push": 0.0, "move_secs": 1.0, "lead_secs": 0.5, "spotlight": 0},
    "grade": {"deyellow": 0.25, "contrast": 1.12, "brightness": 1.04},
    "music": {"seed": 31, "root": 146.83, "bpm": 56, "density": 0.5, "xiao": 0.8,
              "music_db": -20, "duck_db": -7},
    "loudness": {"I": -16, "TP": -1.5, "LRA": 11},
    "layout": {"width": 1080, "height": 1920, "fps": 30, "bg_color": "#1b1815",
               "stage": {"size": 720, "center_x": 480, "center_y": 760},
               "label_size": 34, "title_size": 84, "title_x": 480, "title_y": 200, "end_y": 1250,
               "focus_window": [150, 170, 810, 1270], "full_window": [50, 300, 930, 1240],
               "subtitle": {"size": 60, "color": "#efe6d2", "center_x": 480, "center_y": 1400,
                            "dim_alpha": 0.42, "stroke": 2},
               "title": {"size": 84, "sub_size": 48, "center_x": 480, "center_y": 300},
               "end": {"size": 72, "note_size": 32, "center_x": 480, "center_y": 1300}},
}


def _char_box(profile: dict, which: str, pad_ratio: float = 0.12) -> tuple:
    ci, ki = [int(v) - 1 for v in which.split(":")]
    col = profile["layout"]["columns"][ci]
    ch = col["chars"][ki]
    W, H = profile["geometry"]["size"]
    x0, y0, x1, y1 = [v * s for v, s in zip(ch["box"], (W, H, W, H))]
    pw, ph = (x1 - x0) * pad_ratio, (y1 - y0) * pad_ratio
    return [max(0, int(x0 - pw)), max(0, int(y0 - ph)), min(W, int(x1 + pw)), min(H, int(y1 + ph))]


def build_config(profile: dict, project_dir: Path, params: dict | None = None) -> dict:
    params = dict(params or {})
    if not params.get("sentences"):
        raise ValueError("一字一讲需要讲解词：params['sentences'] = [{'text': …, 'show': 'work'|'evo:n'}]")
    prof_dir = Path(profile["_dir"])
    W, H = profile["geometry"]["size"]
    pron = profile.get("pronunciation", {})
    which = params.get("char", "1:1")
    char_box = params.get("char_box") or _char_box(profile, which)
    cols = profile["layout"]["columns"]
    ci, ki = [int(v) - 1 for v in which.split(":")]
    the_char = cols[ci]["chars"][ki].get("text") or "?"

    sentences = []
    for s in params["sentences"]:
        sentences.append({
            "text": s["text"],
            "subtitle": s.get("subtitle") or strip_punct(s["text"]),
            "tts_text": _apply_pronunciation(s["text"], pron),
            "show": s.get("show", "work"),
        })

    cfg = {
        "output_name": params.get("output_name") or f"{profile['slug']}_{NAME}_{the_char}",
        "image": str((project_dir / "素材" / "作品原图.jpg").resolve()),
        "geometry": {"auto_deskew": True, "margin_px": 40,
                     "cache": str((prof_dir / "作品_校正.png").resolve())},
        "char_box": char_box,
        "grade": params.get("grade", DEFAULTS["grade"]),
        "title": params.get("title", f"一字一讲 ·「{the_char}」"),
        "work_label": params.get("work_label", "本作 · 行草"),
        "author": params.get("author", "作者名"),
        "end_card_text": params.get("end_card_text", "{author} · 书"),
        "font": None,
        "evolution": params.get("evolution", []),
        "sentences": sentences,
        "tts": {**DEFAULTS["tts"], **params.get("tts", {})},
        "tts_provider": params.get("tts_provider", "edge"),
        "timing": {**DEFAULTS["timing"], **params.get("timing", {})},
        "camera": {**DEFAULTS["camera"], **params.get("camera", {})},
        "music": {**DEFAULTS["music"], **params.get("music", {})},
        "loudness": params.get("loudness", DEFAULTS["loudness"]),
        "layout": {**DEFAULTS["layout"], **params.get("layout", {})},
        "_note": "evolution 必须是真实且已确认许可的字形演变图片；缺失时只有「本作」画面",
    }
    (project_dir / "源码").mkdir(parents=True, exist_ok=True)
    (project_dir / "素材").mkdir(exist_ok=True)
    import shutil
    shutil.copy(prof_dir / "作品原图.jpg", project_dir / "素材" / "作品原图.jpg")
    cfg_path = project_dir / "源码" / "config.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"config": str(cfg_path), "n_sentences": len(sentences), "char": the_char,
            "char_box": char_box,
            "note": "evolution 为空 → 只有本作画面（验机模式）；正式使用请提供有授权的演变图",
            "durations_estimate": "~%.0fs" % (DEFAULTS["timing"]["title_secs"] + DEFAULTS["timing"]["intro_secs"]
                                              + sum(0.8 + 0.5 * len(strip_punct(s["text"])) for s in sentences)
                                              + DEFAULTS["timing"]["tail_hold"] + DEFAULTS["timing"]["end_secs"])}
