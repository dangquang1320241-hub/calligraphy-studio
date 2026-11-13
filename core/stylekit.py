#!/usr/bin/env python3
"""M5 B 族共享小助手：档案 → 慢揭幕/逐字特写/笔顺复现 的公共字段。"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from core.profile import strip_punct  # noqa: E402


def common_artwork_fields(profile: dict, project_dir: Path, prof_dir: Path) -> dict:
    corners = profile["geometry"]["info"].get("corners_src")
    return {
        "artwork": str((project_dir / "素材" / "作品原图.jpg").resolve()),
        "geometry": {"perspective": corners} if corners else {"auto_deskew": True},
        "geometry_prep": {"scale": 2.0, "inset_px": 6},
        "cleaned": str((project_dir / "素材" / "作品_clean_校正.png").resolve()),
        "cleaned_scale": 2.0,
    }


def columns_normalized(profile: dict) -> list:
    out = []
    for i, c in enumerate(profile["layout"]["columns"]):
        out.append({"id": i + 1, "text": c.get("text", ""), "x0": c["box"][0], "x1": c["box"][2]})
    return out


def char_box_norm(profile: dict, col_i: int, char_i: int, pad=0.06) -> list:
    """列/字（右起 1-based）→ 归一化字框（含少量呼吸边）。"""
    col = profile["layout"]["columns"][col_i - 1]
    ch = col["chars"][char_i - 1]
    x0, y0, x1, y1 = ch["box"]
    pw, ph = (x1 - x0) * pad, (y1 - y0) * pad
    return [max(0.0, x0 - pw), max(0.0, y0 - ph), min(1.0, x1 + pw), min(1.0, y1 + ph)]


def passage_lines(text: str) -> list:
    out, buf = [], ""
    for ch in text:
        buf += ch
        if ch in "。？！；":
            out.append(buf)
            buf = ""
    if strip_punct(buf):
        out.append(buf)
    return out


def render_cmd_b(style_dir: str, renderer_filename: str):
    """B 族渲染器 CLI 工厂：--config --out（必填 --out 直出文件，无 --preview/--skip-tts）。"""
    def _cmd(cfg_path, project_dir, preview=None, skip_tts=False):
        cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
        name = cfg.get("output_name", "out")
        W, H = cfg.get("width", 1080), cfg.get("height", 1920)
        out = Path(project_dir) / "成片" / f"{name}_{W}x{H}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        return [sys.executable, str(Path(REPO) / "styles" / style_dir / renderer_filename),
                "--config", str(cfg_path), "--out", str(out)]
    return _cmd
