#!/usr/bin/env python3
"""作品档案构建（M1）：原图 → EXIF 归正 → 几何摆正 → inkseg 列/字检测 → 档案.json + 确认图。

用法（一般经 core.orchestrate 调用；也可独立运行）：
  python -m core.profile <作品图> --text 释文.txt --slug 三槐堂铭节选 [--data-root …] [--signature]
"""
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "common"))

import geometry as geom  # noqa: E402
import inkseg  # noqa: E402
from videokit import sha256  # noqa: E402

DEFAULT_DATA_ROOT = Path("/Users/nolan/Workspace/10-Projects/VideoCreationStorage/CalligraphyWorksShow")
PUNCT = set("，。；：、！？…,.;:!?“”「」《》·— \u3000\n\r\t")
SCHEMA_VERSION = "0.1.0"


def strip_punct(s: str) -> str:
    return "".join(ch for ch in s if ch not in PUNCT)


def split_clauses(text: str):
    """按标点切短语，标点跟随前字；返回 [(短语含标点)]。"""
    out, buf = [], ""
    for ch in text:
        buf += ch
        if ch in PUNCT and strip_punct(buf):
            out.append(buf)
            buf = ""
    if strip_punct(buf):
        out.append(buf)
    return out


def _deskew(src: Path, prof_dir: Path):
    cache = prof_dir / "作品_校正.png"
    geo = {"auto_deskew": True, "margin_px": 40}
    img, info = geom.load_work_image(src, geo, cache)
    # 与渲染器同规则写缓存元数据（键一致才能命中）
    cache.with_suffix(".json").write_text(
        json.dumps({"key": {"src_sha256": sha256(str(src)), "geo": geo}, "info": info}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return img, info


def _ink_bbox(rgb: np.ndarray):
    soft, binary = inkseg.ink_masks(rgb)
    ys, xs = np.nonzero(binary)
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def _distribute(text: str, counts: list):
    """把无标点文本按各列字数顺序分配；返回每列文本列表，不匹配时抛错。"""
    chars = strip_punct(text)
    total = sum(counts)
    if total != len(chars):
        raise ValueError(
            f"释文字数（{len(chars)}，不含标点）与画面检出字数（{total}）不一致。\n"
            f"各列检出字数：{counts}（右→左，共 {len(counts)} 列）。\n"
            f"请核对释文是否与作品一致、是否漏了落款外的字，或用 --n-cols 指定列数。")
    out, i = [], 0
    for n in counts:
        out.append(chars[i:i + n])
        i += n
    return out


def build_profile(source: Path, text: str | None, slug: str, data_root: Path = DEFAULT_DATA_ROOT,
                  signature: bool = False, pronunciation: dict | None = None,
                  col_counts: list | None = None, per_column: list | None = None) -> Path:
    source = Path(source).resolve()
    prof_dir = Path(data_root) / "作品档案" / slug
    prof_dir.mkdir(parents=True, exist_ok=True)

    # 1) 输入副本 + EXIF 归正
    work = prof_dir / "作品原图.jpg"
    im = ImageOps.exif_transpose(Image.open(source))
    im.convert("RGB").save(work, quality=95)

    # 2) 几何摆正（缓存）
    img, geo_info = _deskew(work, prof_dir)
    paper_rect = geo_info.get("paper_rect")

    # 3) 释文 → 列切字（优先级：逐列文本 > 释文全文字数分配 > 无释文占位切字/纯自动）
    rgb = np.asarray(img.convert("RGB"))
    if per_column:  # ③ 屏逐列确认：最权威，直接按列切字
        per_col = [strip_punct(c) for c in per_column]
        text = text or "".join(per_col)
    elif text:
        counts = col_counts
        if not counts:
            lay_auto = inkseg.detect_layout(rgb)
            counts = [len(c["chars"]) for c in lay_auto["columns"]]
        per_col = _distribute(text, counts)
    elif col_counts:  # 无释文 + 人工列字数：占位切字（只修字框，释文待③）
        n_last = col_counts[-1] - 1 if signature else col_counts[-1]
        per_col = ["□" * n for n in col_counts[:-1]] + (["□" * n_last] if col_counts else [])
    else:
        per_col = None  # 纯自动估列估字
    if per_col is not None:
        # 逐列文本已含落款，不再让 inkseg 追加；签名语义只在「全文分配」分支生效
        layout = inkseg.detect_layout(rgb, columns_text=per_col,
                                      signature=signature if (text and not per_column) else False)
        if text and sum(len(c["chars"]) for c in layout["columns"]) != len(strip_punct(text)):
            raise ValueError("列检测与释文分配不一致，请查看列框确认图后用 char_cuts 人工修正。")
    else:
        layout = inkseg.detect_layout(rgb)  # 无释文也出列框（字数估计可能不准，②里人工校）
        per_col = []

    # 4) 产物：确认图 / layout / 档案
    overlay = inkseg.draw_layout_overlay(rgb, layout, prof_dir / "列框确认图.jpg")
    (prof_dir / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=1), encoding="utf-8")
    if text is not None:
        (prof_dir / "释文.txt").write_text(text.strip() + "\n", encoding="utf-8")
    profile = {
        "slug": slug,
        "schema_version": SCHEMA_VERSION,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": {"file": str(source), "sha256": sha256(str(source)), "exif_transposed": True},
        "geometry": {"info": geo_info, "paper_rect": paper_rect, "size": list(img.size)},
        "layout": layout,
        "transcription": {
            "text": (text or "").strip(),
            "per_column": per_col,
            "confidence": "human-provided" if text else "undetected",
        },
        "pronunciation": pronunciation or {},
        "ink_bbox": _ink_bbox(rgb),
        "confirm_images": {"列框确认图": str(overlay)},
    }
    (prof_dir / "档案.json").write_text(json.dumps(profile, ensure_ascii=False, indent=1), encoding="utf-8")
    return prof_dir


def layout_px_pixels(profile: dict) -> dict:
    """inkseg 归一化布局 → 章法解析/节奏卡点使用的像素坐标格式（校正后图像坐标系）。"""
    W, H = profile["geometry"]["size"]
    cols = []
    for c in profile["layout"]["columns"]:
        cols.append({
            "index": c["index"], "text": c.get("text", ""),
            "box": [round(v * s) for v, s in zip(c["box"], (W, H, W, H))],
            "chars": [{"text": ch.get("text", ""),
                       "box": [round(v * s) for v, s in zip(ch["box"], (W, H, W, H))]} for ch in c["chars"]],
        })
    return {"crop": profile["geometry"]["paper_rect"], "columns": cols}


def load_profile(slug: str, data_root: Path = DEFAULT_DATA_ROOT, expect_sha: str | None = None) -> dict:
    p = Path(data_root) / "作品档案" / slug / "档案.json"
    if not p.exists():
        raise FileNotFoundError(f"作品档案不存在：{p}")
    prof = json.loads(p.read_text(encoding="utf-8"))
    if expect_sha and prof["source"]["sha256"] != expect_sha:
        raise ValueError(f"档案 {slug} 与原图不匹配（sha256 不同）")
    return prof


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--text")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    ap.add_argument("--signature", action="store_true")
    ap.add_argument("--col-counts", help="人工指定各列字数（右→左，逗号分隔），列字数估错时用")
    ap.add_argument("--columns-text", help='逐列权威文本（右→左，竖线|分隔，不含标点），最可靠的切字方式')
    a = ap.parse_args()
    text = Path(a.text).read_text(encoding="utf-8") if a.text else None
    counts = [int(v) for v in a.col_counts.split(",")] if a.col_counts else None
    per_column = a.columns_text.split("|") if a.columns_text else None
    d = build_profile(Path(a.source), text, a.slug, Path(a.data_root), a.signature,
                      col_counts=counts, per_column=per_column)
    print("档案目录：", d)
