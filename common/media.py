#!/usr/bin/env python3
"""Shared visual helpers for calligraphy showcase templates."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

_FONT_DIR = Path(__file__).resolve().parent / "fonts"
_NOTO = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"  # Debian 共享电脑原约定
if Path(_NOTO).exists():
    FONT_REG = _NOTO
    _bold = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"
    FONT_BOLD = _bold if Path(_bold).exists() else str(_FONT_DIR / "LXGWWenKai-Regular.ttf")
    FONT_INDEX_SC = 2
else:  # macOS 等：回落到随库分发的霞鹜文楷（SIL OFL）
    FONT_REG = str(_FONT_DIR / "LXGWWenKai-Regular.ttf")
    FONT_BOLD = FONT_REG
    FONT_INDEX_SC = 0

# Dark ink / warm paper palette — unique to this template family
INK = (28, 22, 18)
PAPER = (228, 215, 190)
PAPER_DEEP = (214, 196, 164)
CREAM = (243, 232, 212)
GOLD = (196, 165, 116)
SEAL = (180, 58, 44)
SEAL_DARK = (138, 38, 30)
BG = (22, 18, 16)
MUTED = (120, 104, 84)

SAFE_BOTTOM = 350
SAFE_RIGHT = 130
STAGE_TOP = 140
STAGE_BOT = 1360
CAPTION_TOP = 1380
CAPTION_BOT = 1540

_FONT_CACHE: dict[tuple, ImageFont.FreeTypeFont] = {}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (int(size), bool(bold), FONT_INDEX_SC)
    if key not in _FONT_CACHE:
        path = FONT_BOLD if bold else FONT_REG
        _FONT_CACHE[key] = ImageFont.truetype(path, int(size), index=FONT_INDEX_SC)
    return _FONT_CACHE[key]


def clamp(v, a, b):
    return a if v < a else b if v > b else v


def lerp(a, b, t):
    return a + (b - a) * t


def smoothstep(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def smootherstep(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def ease_in_out(t: float) -> float:
    return smootherstep(t)


def as_norm(v: float, total: float) -> float:
    """Values > 1 are treated as pixels; otherwise already normalized."""
    if total <= 0:
        return 0.0
    return float(v) / total if float(v) > 1.0 else float(v)


def norm_rect(box, w, h):
    x0, y0, x1, y1 = box
    return (as_norm(x0, w), as_norm(y0, h), as_norm(x1, w), as_norm(y1, h))


def order_corners(pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=np.float32)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).ravel()
    tl, br = pts[np.argmin(s)], pts[np.argmax(s)]
    tr, bl = pts[np.argmin(diff)], pts[np.argmax(diff)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def detect_paper_corners(bgr: np.ndarray):
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    L, A, B = cv2.split(lab)
    mask = cv2.bitwise_and(cv2.inRange(L, 140, 255), cv2.inRange(B, 132, 255))
    if mask.mean() < 5:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 10, 140), (40, 120, 255))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=3)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=1)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if n <= 1:
        raise RuntimeError("未能从照片中分出纸张，请换一张背景更干净的正面照片。")
    idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    mask = np.uint8((labels == idx) * 255)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=4)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnt = max(contours, key=cv2.contourArea)
    peri = cv2.arcLength(cnt, True)
    approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
    if len(approx) == 4:
        corners = approx.reshape(4, 2)
    else:
        hull = cv2.convexHull(cnt)
        found = None
        for eps in np.linspace(0.01, 0.06, 20):
            ap = cv2.approxPolyDP(hull, eps * peri, True)
            if len(ap) == 4:
                found = ap.reshape(4, 2)
                break
        corners = found if found is not None else cv2.boxPoints(cv2.minAreaRect(cnt))
    return order_corners(corners), mask


def inset_corners(corners: np.ndarray, px: float = 10.0) -> np.ndarray:
    c = corners.mean(axis=0)
    vec = c - corners
    n = np.linalg.norm(vec, axis=1, keepdims=True)
    n = np.maximum(n, 1e-3)
    return corners + vec / n * px


def mild_levels(rgb: np.ndarray) -> np.ndarray:
    arr = rgb.astype(np.float32)
    lo = np.percentile(arr, 0.8)
    hi = np.percentile(arr, 99.4)
    stretched = np.clip((arr - lo) / max(hi - lo, 1.0) * 255.0, 0, 255)
    mixed = stretched * 0.40 + arr * 0.60
    # keep a touch of warm paper in the highlights
    lum = mixed.mean(axis=2, keepdims=True)
    paper = np.clip((lum - 150.0) / 80.0, 0.0, 1.0)
    mixed[..., 0] = np.clip(mixed[..., 0] + paper[..., 0] * 5.0, 0, 255)
    mixed[..., 1] = np.clip(mixed[..., 1] + paper[..., 0] * 2.5, 0, 255)
    mixed[..., 2] = np.clip(mixed[..., 2] - paper[..., 0] * 1.5, 0, 255)
    return mixed.astype(np.uint8)


def preprocess_artwork(src: Path, dst: Path, scale: float = 2.0, inset: float = 10.0) -> Path:
    """Crop to paper, mild perspective + levels. Does not redraw strokes."""
    src, dst = Path(src), Path(dst)
    bgr = cv2.imread(str(src))
    if bgr is None:
        raise FileNotFoundError(f"无法读取作品照片: {src}")
    corners, _mask = detect_paper_corners(bgr)
    corners = inset_corners(corners, inset)
    tl, tr, br, bl = corners
    W = int(round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)) * scale))
    H = int(round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)) * scale))
    dst_pts = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(corners, dst_pts)
    warped = cv2.warpPerspective(
        bgr, M, (W, H), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE
    )
    rgb = mild_levels(cv2.cvtColor(warped, cv2.COLOR_BGR2RGB))
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb).save(dst)
    return dst


def load_rgb(path: Path) -> np.ndarray:
    im = Image.open(path).convert("RGB")
    return np.asarray(im)


def sample_paper_color(work: np.ndarray) -> tuple[int, int, int]:
    gray = work.mean(axis=2)
    # brightest band is usually paper
    lo = np.percentile(gray, 70)
    sel = work[gray >= lo]
    if len(sel) == 0:
        return PAPER
    c = sel.mean(axis=0)
    return tuple(int(round(x)) for x in c)


def make_paper_texture(w: int, h: int, color=PAPER, seed: int = 20261110) -> np.ndarray:
    rng = np.random.RandomState(seed)
    base = np.ones((h, w, 3), np.float32) * np.array(color, dtype=np.float32)
    noise = np.zeros((h, w), np.float32)
    for scale, amp in ((7, 11.0), (14, 6.5), (28, 3.5), (3, 4.0)):
        sh, sw = max(2, h // scale), max(2, w // scale)
        n = rng.randn(sh, sw).astype(np.float32)
        n = cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC)
        noise += n * amp
    fw = max(2, w // 48)
    fibers = rng.randn(h, fw).astype(np.float32)
    fibers = cv2.resize(fibers, (w, h), interpolation=cv2.INTER_LINEAR)
    fibers = cv2.GaussianBlur(fibers, (1, 13), 0)
    blot_s = (max(2, h // 90), max(2, w // 90))
    blot = cv2.resize(rng.randn(*blot_s).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
    blot = cv2.GaussianBlur(blot, (0, 0), 25)
    out = (
        base
        + noise[:, :, None]
        + fibers[:, :, None] * np.array([0.85, 0.7, 0.45]) * 3.5
        + blot[:, :, None] * np.array([6.0, 4.0, 2.0])
    )
    return np.clip(out, 0, 255).astype(np.uint8)


def make_vignette(w: int, h: int, strength: float = 0.50, power: float = 1.8) -> np.ndarray:
    y, x = np.ogrid[0:h, 0:w]
    nx = (x - w / 2.0) / (w * 0.68)
    ny = (y - h / 2.0) / (h * 0.62)
    r = np.sqrt(nx * nx + ny * ny)
    v = 1.0 - strength * np.clip(r, 0.0, 1.0) ** power
    return v.astype(np.float32)


def apply_vignette(rgb: np.ndarray, vig: np.ndarray) -> np.ndarray:
    out = rgb.astype(np.float32) * vig[:, :, None]
    return np.clip(out, 0, 255).astype(np.uint8)


def make_background(work: np.ndarray, w: int, h: int, blur: int = 51, dark: float = 0.16) -> np.ndarray:
    ih, iw = work.shape[:2]
    scale = max(w / iw, h / ih) * 1.15
    nw, nh = int(iw * scale), int(ih * scale)
    resized = cv2.resize(work, (nw, nh), interpolation=cv2.INTER_AREA)
    x0 = max(0, (nw - w) // 2)
    y0 = max(0, (nh - h) // 2)
    crop = resized[y0 : y0 + h, x0 : x0 + w]
    if crop.shape[0] != h or crop.shape[1] != w:
        canvas = np.zeros((h, w, 3), np.uint8)
        canvas[: crop.shape[0], : crop.shape[1]] = crop
        crop = canvas
    # shrink-blur-enlarge so large characters dissolve into tone
    sm = cv2.resize(crop, (max(8, w // 10), max(8, h // 10)), interpolation=cv2.INTER_AREA)
    sm = cv2.GaussianBlur(sm, (21, 21), 0)
    blurred = cv2.resize(sm, (w, h), interpolation=cv2.INTER_CUBIC)
    out = blurred.astype(np.float32) * dark
    out = out * np.array([1.02, 0.90, 0.75], dtype=np.float32) + np.array([10, 7, 4], dtype=np.float32)
    return np.clip(out, 0, 255).astype(np.uint8)


def blurred_work(work: np.ndarray, radius: int = 21) -> np.ndarray:
    k = radius if radius % 2 == 1 else radius + 1
    return cv2.GaussianBlur(work, (k, k), 0)


def alpha_blit(dst: Image.Image, src: Image.Image, x: int, y: int, opacity: float = 1.0) -> Image.Image:
    if opacity <= 0:
        return dst
    if dst.mode != "RGBA":
        dst = dst.convert("RGBA")
    if src.mode != "RGBA":
        src = src.convert("RGBA")
    if opacity < 1.0:
        a = np.array(src)
        a[..., 3] = (a[..., 3].astype(np.float32) * opacity).astype(np.uint8)
        src = Image.fromarray(a, "RGBA")
    layer = Image.new("RGBA", dst.size, (0, 0, 0, 0))
    layer.paste(src, (int(round(x)), int(round(y))), src)
    return Image.alpha_composite(dst, layer)


def text_image(text: str, font_obj, fill, tracking: int = 0, stroke_width: int = 0, stroke_fill=None) -> Image.Image:
    if not text:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    dummy = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(dummy)
    if tracking == 0:
        bbox = d.textbbox((0, 0), text, font=font_obj, stroke_width=stroke_width)
        w, h = bbox[2] - bbox[0] + 4, bbox[3] - bbox[1] + 4
        im = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
        ImageDraw.Draw(im).text(
            (2 - bbox[0], 2 - bbox[1]),
            text,
            font=font_obj,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        return im
    # character-by-character tracking
    chars = list(text)
    widths = []
    heights = []
    boxes = []
    for ch in chars:
        b = d.textbbox((0, 0), ch, font=font_obj, stroke_width=stroke_width)
        boxes.append(b)
        widths.append(b[2] - b[0])
        heights.append(b[3] - b[1])
    w = int(sum(widths) + tracking * (len(chars) - 1) + 8)
    h = int(max(heights) + 8)
    im = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    x = 4
    for ch, b, cw, chh in zip(chars, boxes, widths, heights):
        dr.text(
            (x - b[0], (h - chh) // 2 - b[1]),
            ch,
            font=font_obj,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        x += cw + tracking
    return im


def vertical_text_image(text: str, font_obj, fill, gap: int = 6) -> Image.Image:
    parts = []
    total_h, max_w = 0, 1
    for ch in text:
        im = text_image(ch, font_obj, fill)
        parts.append(im)
        total_h += im.height + gap
        max_w = max(max_w, im.width)
    total_h = max(1, total_h - gap)
    canvas = Image.new("RGBA", (max_w, total_h), (0, 0, 0, 0))
    y = 0
    for im in parts:
        canvas.paste(im, ((max_w - im.width) // 2, y), im)
        y += im.height + gap
    return canvas


def paste_centered(dst: Image.Image, src: Image.Image, cx: int, cy: int, opacity: float = 1.0) -> Image.Image:
    return alpha_blit(dst, src, int(cx - src.width / 2), int(cy - src.height / 2), opacity)


def make_seal(char: str = "书", size: int = 168, angle: float = -5.0, seed: int = 7) -> Image.Image:
    """Simple cinnabar seal, clearly a placeholder mark — not a real name seal."""
    s = max(64, int(size))
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = 3
    d.rounded_rectangle((m, m, s - 1 - m, s - 1 - m), radius=2, fill=SEAL + (235,))
    # inner rim in a slightly darker red
    g = m + max(5, s // 18)
    d.rounded_rectangle((g, g, s - 1 - g, s - 1 - g), radius=1, outline=SEAL_DARK + (255,), width=max(2, s // 32))
    f = font(int(s * 0.54), bold=True)
    # 阴文: character carved out in warm paper color
    tb = d.textbbox((0, 0), char, font=f)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    d.text(((s - tw) / 2 - tb[0], (s - th) / 2 - tb[1] - s * 0.02), char, font=f, fill=(236, 220, 190, 255))
    # slight irregularity
    rng = np.random.RandomState(seed)
    arr = np.array(im)
    jitter = rng.randn(s, s).astype(np.float32)
    jitter = cv2.GaussianBlur(jitter, (0, 0), 1.2)
    arr[..., 3] = np.clip(arr[..., 3].astype(np.float32) * (0.92 + 0.12 * np.clip(jitter, -1, 1)), 0, 255).astype(np.uint8)
    im = Image.fromarray(arr, "RGBA")
    if abs(angle) > 0.01:
        im = im.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    return im


def composite_work(bg: Image.Image, work: Image.Image, x, y, shadow=True) -> Image.Image:
    dst = bg.convert("RGBA")
    x, y = int(round(x)), int(round(y))
    if shadow:
        sh = Image.new("RGBA", dst.size, (0, 0, 0, 0))
        blob = Image.new("RGBA", (work.width, work.height), (0, 0, 0, 90))
        sh.paste(blob, (x, y + 12), blob)
        sh = sh.filter(ImageFilter.GaussianBlur(radius=20))
        dst = Image.alpha_composite(dst, sh)
    dst = alpha_blit(dst, work if work.mode == "RGBA" else work.convert("RGBA"), x, y, 1.0)
    return dst


def resize_work(work: np.ndarray, dw: int, dh: int) -> Image.Image:
    dw, dh = max(1, int(round(dw))), max(1, int(round(dh)))
    im = Image.fromarray(work)
    return im.resize((dw, dh), Image.Resampling.LANCZOS)


def crop_and_resize(work: np.ndarray, rect, dw: int, dh: int) -> Image.Image:
    """rect = (x0,y0,x1,y1) in pixel coordinates of work."""
    h, w = work.shape[:2]
    x0, y0, x1, y1 = rect
    x0 = int(np.floor(clamp(x0, 0, w - 1)))
    y0 = int(np.floor(clamp(y0, 0, h - 1)))
    x1 = int(np.ceil(clamp(x1, x0 + 1, w)))
    y1 = int(np.ceil(clamp(y1, y0 + 1, h)))
    patch = work[y0:y1, x0:x1]
    return Image.fromarray(patch).resize((max(1, int(round(dw))), max(1, int(round(dh)))), Image.Resampling.LANCZOS)


def fit_in_stage(src_w, src_h, stage_w, stage_h):
    scale = min(stage_w / src_w, stage_h / src_h)
    dw, dh = src_w * scale, src_h * scale
    dx = (stage_w - dw) / 2
    dy = (stage_h - dh) / 2
    return dx, dy, dw, dh


def pad_rect_to_aspect(x0, y0, x1, y1, aspect, img_w, img_h, pad=0.14):
    """Expand a box (normalized or px already converted to px) to the given w/h aspect, with padding."""
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    w = (x1 - x0) * (1.0 + pad)
    h = (y1 - y0) * (1.0 + pad)
    if w / h < aspect:
        w = h * aspect
    else:
        h = w / aspect
    # Expand only within the image; if aspect cannot be met, leave it
    # (the renderer letterboxes) rather than swallowing neighbouring columns.
    w = min(w, img_w)
    h = min(h, img_h)
    if w / max(h, 1e-6) < aspect:
        w = min(img_w, h * aspect)
    else:
        h = min(img_h, w / max(aspect, 1e-6))
    x0n = cx - w / 2.0
    y0n = cy - h / 2.0
    x0n = clamp(x0n, 0, img_w - w)
    y0n = clamp(y0n, 0, img_h - h)
    return (x0n, y0n, x0n + w, y0n + h)


def lerp_rect(a, b, t):
    return tuple(lerp(a[i], b[i], t) for i in range(4))


def ink_wipe_mask(h, w, columns, progresses, feather_ratio=0.12) -> np.ndarray:
    """columns: list of (x0,x1) in pixels. progresses: list of 0-1 reveal amounts (top-to-bottom)."""
    mask = np.zeros((h, w), np.float32)
    feather = max(8.0, h * feather_ratio)
    ys = np.arange(h, dtype=np.float32)
    for (x0, x1), p in zip(columns, progresses):
        if p <= 0:
            continue
        xa, xb = int(max(0, np.floor(x0))), int(min(w, np.ceil(x1)))
        if xb <= xa:
            continue
        if p >= 1.0:
            mask[:, xa:xb] = np.maximum(mask[:, xa:xb], 1.0)
            continue
        y_front = p * (h + 2 * feather) - feather
        a = np.clip((y_front - ys) / feather, 0.0, 1.0)
        a = a * a * (3.0 - 2.0 * a)
        mask[:, xa:xb] = np.maximum(mask[:, xa:xb], a[:, None])
    # soft horizontal seam between revealed and still-veiled columns
    if float(mask.max()) > 0:
        mask = cv2.GaussianBlur(mask, (31, 1), 0)
    return mask


def mix_reveal(sharp: np.ndarray, blur: np.ndarray, mask: np.ndarray) -> np.ndarray:
    m = mask[:, :, None]
    out = blur.astype(np.float32) * (1.0 - m) + sharp.astype(np.float32) * m
    return np.clip(out, 0, 255).astype(np.uint8)


def fade(t, t0, t1):
    if t1 <= t0:
        return 1.0 if t >= t0 else 0.0
    return smoothstep((t - t0) / (t1 - t0))


def hold_fade(t, start, end, fade_in=0.35, fade_out=0.30):
    """1 during [start,end], with fade in/out."""
    if t < start or t > end:
        # still allow fade windows
        if start - fade_in <= t < start:
            return fade(t, start - fade_in, start)
        if end < t <= end + fade_out:
            return 1.0 - fade(t, end, end + fade_out)
        return 0.0
    if t < start + fade_in:
        return fade(t, start, start + fade_in)
    if t > end - fade_out:
        return 1.0 - fade(t, end - fade_out, end)
    return 1.0


def rgb_to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(arr, "RGB")


def frame_to_rgb_bytes(im: Image.Image) -> bytes:
    if im.mode != "RGB":
        im = im.convert("RGB")
    return im.tobytes()


def load_config(path: Path) -> dict:
    import json

    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["_config_dir"] = path.parent.resolve()
    return cfg


def resolve_path(cfg: dict, value, default=None) -> Path | None:
    if value is None:
        value = default
    if value is None:
        return None
    p = Path(value)
    if p.is_absolute() and p.exists():
        return p
    # try relative to config, then cwd（旧共享机的绝对路径候选已移除）
    for root in (cfg.get("_config_dir"), Path.cwd()):
        if root is None:
            continue
        cand = (Path(root) / p).resolve()
        if cand.exists():
            return cand
    return (Path(cfg.get("_config_dir", Path.cwd())) / p).resolve()
