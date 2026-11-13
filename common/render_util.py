#!/usr/bin/env python3
"""新增：笔顺复现 / 作品入画 / 墨色呼吸 三个模板共用的小工具（不改动已有模块）。
- parallel_frames: fork 多进程按顺序产出帧（frame(t) 是纯函数，可并行）
- tag_pill: 左上角「示意」角标
- caption: 安全区内的居中字幕
- verify: ffprobe/解码/响度 + 关键帧联系表（带安全线）
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# macOS 上 fork 会复制已初始化的 cv2 线程池并死锁子进程——并行前必须单线程化 cv2
try:
    import cv2
    cv2.setNumThreads(0)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
except Exception:  # noqa: BLE001
    pass

COMMON = Path(__file__).resolve().parent
sys.path.insert(0, str(COMMON))
WENKAI = str(COMMON / "fonts" / "LXGWWenKai-Regular.ttf")
_FC = {}


def wk(size):
    size = int(size)
    if size not in _FC:
        _FC[size] = ImageFont.truetype(WENKAI, size)
    return _FC[size]


_FN = None


def _work(i):
    return _FN(i)


def parallel_frames(frame_fn, n_frames, workers=None, chunk=4):
    """frame_fn(i) -> bytes(RGB)。按顺序 yield。依赖 fork 共享只读数据。"""
    global _FN
    _FN = frame_fn
    workers = workers or max(1, min(8, (os.cpu_count() or 2) - 1))
    if workers <= 1:
        for i in range(n_frames):
            yield frame_fn(i)
        return
    ctx = mp.get_context("fork")
    with ctx.Pool(workers) as pool:
        for b in pool.imap(_work, range(n_frames), chunksize=chunk):
            yield b


def text_rgba(text, size, fill=(243, 232, 212), tracking=0, shadow=True):
    f = wk(size)
    d = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    if tracking:
        ws = [d.textbbox((0, 0), ch, font=f) for ch in text]
        w = sum(b[2] - b[0] for b in ws) + tracking * (len(text) - 1) + 12
        h = max(b[3] for b in ws) + 12
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dr = ImageDraw.Draw(im)
        x = 6
        for ch, b in zip(text, ws):
            dr.text((x - b[0], 6), ch, font=f, fill=fill)
            x += b[2] - b[0] + tracking
    else:
        b = d.textbbox((0, 0), text, font=f)
        im = Image.new("RGBA", (b[2] - b[0] + 12, b[3] + 12), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((6 - b[0], 6), text, font=f, fill=fill)
    if shadow:
        from PIL import ImageFilter
        a = np.array(im)[..., 3]
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        sh.putalpha(Image.fromarray((a * 0.7).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3)))
        base = Image.new("RGBA", im.size, (0, 0, 0, 0))
        base.alpha_composite(sh, (0, 0))
        base.alpha_composite(im, (0, 0))
        im = base
    return im


def tag_pill(text="示意", size=26, fg=(243, 232, 212), bg=(20, 16, 14, 150)):
    t = text_rgba(text, size, fg, shadow=False)
    pw, ph = t.width + 22, t.height + 6
    im = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, pw - 1, ph - 1), radius=ph // 2, fill=bg,
                                         outline=(196, 165, 116, 200), width=1)
    im.alpha_composite(t, (11, 2))
    return im


def blit(dst: Image.Image, src: Image.Image, x, y, opacity=1.0):
    if opacity <= 0:
        return
    if opacity < 1:
        a = np.array(src)
        a[..., 3] = (a[..., 3] * opacity).astype(np.uint8)
        src = Image.fromarray(a, "RGBA")
    dst.alpha_composite(src, (int(round(x)), int(round(y))))


def blit_center(dst, src, cx, cy, opacity=1.0):
    blit(dst, src, cx - src.width / 2, cy - src.height / 2, opacity)


def check_safe(x0, y0, x1, y1, W=1080, H=1920, bottom=350, right=130):
    """文字框是否在安全区内（底部 bottom px、右侧 right px 之外）。"""
    return y1 <= H - bottom and x1 <= W - right


def verify(mp4, check_dir, times, labels, title=""):
    from videokit import contact_sheet, ffprobe_report, grab_frames
    check_dir = Path(check_dir)
    check_dir.mkdir(parents=True, exist_ok=True)
    rep = ffprobe_report(str(mp4))
    (check_dir / "probe.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), "utf-8")
    frames = grab_frames(str(mp4), times, check_dir / "frames")
    sheet = contact_sheet(frames, labels, check_dir / "contact_sheet.jpg", cols=4, thumb_w=300)
    return rep, sheet
