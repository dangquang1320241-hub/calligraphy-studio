#!/usr/bin/env python3
"""新增：带「拉正」的作品预处理（geometry → 裁纸 → 轻影调 → 放大），给各模板共用。

不改 geometry.py / media.py 的行为，只是把两者串起来：
  1) geometry.load_work_image(artwork, cfg["geometry"])  —— 自动或手动四角透视拉正（Lanczos），不改笔画
  2) 按 info["paper_rect"] 裁到纸面（再内缩 inset 像素，去掉纸边外的墙面）
  3) media.mild_levels 全局影调（与 v1 preprocess_artwork 相同）
  4) Lanczos 放大 scale 倍 → 写到 cfg["cleaned"]（同参数同原图时直接复用缓存）

config 写法：
  "geometry": {"auto_deskew": true}
  或 "geometry": {"perspective": [[58.75,111.67],[1645.05,77.12],[1655.77,1015.43],[60.75,1051.03]]}
  可选 "geometry_prep": {"scale": 2.0, "inset_px": 6}
命令行：python3 artwork_prep.py --config config.json   （只生成 cleaned 图 + 前后对比图）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geometry  # noqa: E402
from media import mild_levels, resolve_path  # noqa: E402


def _key(art: Path, geo: dict, prep: dict) -> str:
    h = hashlib.sha256(Path(art).read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps({"src": h, "geo": {k: v for k, v in geo.items() if k != "cache"},
                                      "prep": prep, "v": 1}, sort_keys=True).encode()).hexdigest()


def prepare_cleaned(cfg: dict, report: str | Path | None = None) -> Path:
    """cfg 里有 geometry 时生成（或复用）拉正后的 cleaned 图并返回路径；没有 geometry 返回 cfg['cleaned']。"""
    geo = cfg.get("geometry")
    cleaned = resolve_path(cfg, cfg.get("cleaned"))
    if not geo:
        return cleaned
    art = resolve_path(cfg, cfg["artwork"])
    prep = {"scale": 2.0, "inset_px": 6, **(cfg.get("geometry_prep") or {})}
    if cleaned is None:
        cleaned = Path(cfg.get("_config_dir", ".")) / "作品_clean_校正.png"
    meta = cleaned.with_suffix(".prep.json")
    key = _key(art, geo, prep)
    if cleaned.exists() and meta.exists() and json.loads(meta.read_text("utf-8")).get("key") == key:
        return cleaned
    rect_im, info = geometry.rectify(Image.open(art), geo)
    x0, y0, x1, y1 = info.get("paper_rect") or [0, 0, rect_im.width, rect_im.height]
    k = int(prep["inset_px"])
    paper = rect_im.crop((x0 + k, y0 + k, x1 - k, y1 - k))
    rgb = mild_levels(np.asarray(paper.convert("RGB")))
    s = float(prep["scale"])
    out = Image.fromarray(rgb)
    if s != 1.0:
        out = out.resize((int(round(out.width * s)), int(round(out.height * s))), Image.Resampling.LANCZOS)
    cleaned.parent.mkdir(parents=True, exist_ok=True)
    out.save(cleaned)
    meta.write_text(json.dumps({"key": key, "geometry_info": {kk: v for kk, v in info.items() if kk != "homography"},
                                "prep": prep, "size": list(out.size)}, ensure_ascii=False, indent=1), "utf-8")
    if report:
        geometry.before_after_sheet(Image.open(art), rect_im, report)
    print(f"拉正 {info.get('mode')} → {cleaned} {out.size}", flush=True)
    return cleaned


if __name__ == "__main__":
    from media import load_config
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--report", default="")
    a = ap.parse_args()
    print(prepare_cleaned(load_config(Path(a.config)), a.report or None))
