#!/usr/bin/env python3
"""墨迹分割与版面自动检测（新增模块，不改动 common 里已有文件的行为）。

只"读"作品像素：生成墨迹遮罩、列框、字框、骨架与揭示时间图，绝不重画笔画。

命令行（给换新照片用）：
  python inkseg.py detect --image 作品_clean.png --out layout.json \
      [--columns-text "于是携酒与|鱼复游于赤|..."] [--n-cols 7] [--signature] \
      [--overlay 检查记录/layout.jpg]
不给原文时按投影自动估计列数与字数；给了原文（| 分列）就按原文字数切字。
"""
from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT = str(Path(__file__).resolve().parent / "fonts" / "LXGWWenKai-Regular.ttf")


# ---------------------------------------------------------------- ink mask
def paper_background(gray: np.ndarray, stroke_px: float) -> np.ndarray:
    """估计无墨纸面亮度（闭运算去墨 + 平滑），只用于判断哪里是墨。"""
    k = int(max(9, stroke_px * 3)) | 1
    se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, se)
    bg = cv2.GaussianBlur(bg, (0, 0), k / 2)
    return np.maximum(bg, gray)


def ink_masks(rgb: np.ndarray, min_area_ratio: float = 2e-5):
    """返回 (soft 0..1 float32, binary uint8 0/1)。soft = 墨的覆盖程度，可做抗锯齿边。"""
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    h, w = g.shape
    stroke_guess = max(6.0, min(h, w) / 45.0)
    bg = paper_background(g, stroke_guess)
    dark = np.clip((bg - g) / np.maximum(bg, 1.0), 0, 1)  # 0 纸 .. ~1 墨
    d8 = (dark * 255).astype(np.uint8)
    thr, _ = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = max(thr, 40)
    binary = (d8 > thr).astype(np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(binary, 8)
    min_area = max(6, int(h * w * min_area_ratio))
    keep = np.zeros(n, bool)
    keep[1:] = st[1:, cv2.CC_STAT_AREA] >= min_area
    binary = keep[lab].astype(np.uint8)
    lo = thr / 255.0 * 0.55
    hi = min(0.95, thr / 255.0 * 1.6)
    soft = np.clip((dark - lo) / max(hi - lo, 1e-3), 0, 1).astype(np.float32)
    near = cv2.dilate(binary, np.ones((5, 5), np.uint8))
    soft *= near
    return soft, binary


def stroke_width(binary: np.ndarray) -> float:
    dt = cv2.distanceTransform(binary, cv2.DIST_L2, 3)
    v = dt[binary > 0]
    return float(np.median(v) * 2.0) if v.size else 8.0


# ---------------------------------------------------------------- skeleton
def zhang_suen(binary: np.ndarray, max_iter: int = 200) -> np.ndarray:
    """矢量化 Zhang–Suen 细化（不依赖 scikit-image）。"""
    img = np.pad((binary > 0).astype(np.uint8), 1)
    for _ in range(max_iter):
        changed = False
        for step in (0, 1):
            P = img
            p2 = P[:-2, 1:-1]; p3 = P[:-2, 2:]; p4 = P[1:-1, 2:]; p5 = P[2:, 2:]
            p6 = P[2:, 1:-1]; p7 = P[2:, :-2]; p8 = P[1:-1, :-2]; p9 = P[:-2, :-2]
            c = P[1:-1, 1:-1]
            nb = [p2, p3, p4, p5, p6, p7, p8, p9]
            B = sum(x.astype(np.int16) for x in nb)
            A = sum(((nb[i] == 0) & (nb[(i + 1) % 8] == 1)).astype(np.int16) for i in range(8))
            if step == 0:
                m = (c == 1) & (B >= 2) & (B <= 6) & (A == 1) & ((p2 * p4 * p6) == 0) & ((p4 * p6 * p8) == 0)
            else:
                m = (c == 1) & (B >= 2) & (B <= 6) & (A == 1) & ((p2 * p4 * p8) == 0) & ((p2 * p6 * p8) == 0)
            if m.any():
                changed = True
                inner = img[1:-1, 1:-1]
                inner[m] = 0
        if not changed:
            break
    return img[1:-1, 1:-1]


# ---------------------------------------------------------------- layout
def _smooth(v, sigma):
    sigma = max(1.0, float(sigma))
    r = int(sigma * 3)
    x = np.arange(-r, r + 1)
    k = np.exp(-x * x / (2 * sigma * sigma)); k /= k.sum()
    return np.convolve(v, k, mode="same")


def _pick_cuts(profile, n_cuts, lo, hi, min_gap, raw=None):
    """在 [lo,hi] 内选 n_cuts 个切点，彼此至少相隔 min_gap。
    优先选真正的空白（raw 投影为 0 的连续段，越长越优先，取段中点），其次选最低的局部极小。"""
    if n_cuts <= 0:
        return []
    p = profile
    cands = []  # (score, pos)
    if raw is not None:
        r = np.asarray(raw)
        eps = r[lo:hi].max() * 0.02 if hi > lo else 0
        i = lo
        while i < hi:
            if r[i] <= eps:
                j = i
                while j < hi and r[j] <= eps:
                    j += 1
                if i > lo and j < hi:
                    cands.append((-(j - i), (i + j) // 2))
                i = j
            else:
                i += 1
    idx = np.arange(max(lo + 1, 1), min(hi - 1, len(p) - 1))
    mx = p[lo:hi].max() + 1e-9 if hi > lo else 1.0
    for i in idx:
        if p[i] <= p[i - 1] and p[i] <= p[i + 1]:
            cands.append((p[i] / mx, int(i)))
    cands.sort()
    chosen = []
    for _, i in cands:
        if all(abs(i - j) >= min_gap for j in chosen) and i - lo >= min_gap * 0.5 and hi - i >= min_gap * 0.5:
            chosen.append(i)
        if len(chosen) == n_cuts:
            break
    if len(chosen) < n_cuts:  # 补等分
        step = (hi - lo) / (n_cuts + 1)
        for k in range(1, n_cuts + 1):
            c = int(lo + k * step)
            if all(abs(c - j) >= min_gap * 0.5 for j in chosen):
                chosen.append(c)
            if len(chosen) == n_cuts:
                break
    return sorted(chosen)[:n_cuts]


def _dp_cuts(profile, raw, k, lo, hi, lam=0.6, bins=240):
    """动态规划切字：切点处墨越少越好（真空白最好），各字高度尽量接近平均值。"""
    if k <= 1:
        return []
    L = hi - lo
    nb = min(bins, max(k * 4, L))
    pos = np.clip(np.linspace(lo, hi, nb + 1).astype(int), 0, len(profile) - 1)
    mx = profile[lo:hi].max() + 1e-9
    r = np.asarray(raw, np.float32)
    rmx = r[lo:hi].max() + 1e-9
    cut_cost = np.array([min(profile[p] / mx, 1.0) + (0.0 if r[min(p, len(r) - 1)] <= rmx * 0.02 else 0.25)
                         for p in pos])
    avg = nb / k
    INF = 1e18
    # D[j][i]: 前 j 个字结束于 pos[i] 的最小代价
    D = np.full((k + 1, nb + 1), INF); P = np.zeros((k + 1, nb + 1), int)
    D[0][0] = 0.0
    minlen = max(1, int(avg * 0.3)); maxlen = int(avg * 2.6) + 1
    for j in range(1, k + 1):
        for i in range(1, nb + 1):
            best, arg = INF, 0
            for l in range(minlen, min(maxlen, i) + 1):
                prev = D[j - 1][i - l]
                if prev >= INF:
                    continue
                c = prev + lam * ((l - avg) / avg) ** 2 + (cut_cost[i] if j < k else 0.0)
                if c < best:
                    best, arg = c, i - l
            D[j][i], P[j][i] = best, arg
    cuts = []
    i = nb
    for j in range(k, 0, -1):
        i = P[j][i]
        if j > 1:
            cuts.append(int(pos[i]))
    return sorted(cuts)


def _estimate_count(profile, min_gap):
    """无原文时：数投影上的"峰"估计个数。"""
    p = profile / (profile.max() + 1e-9)
    peaks = [i for i in range(1, len(p) - 1) if p[i] >= p[i - 1] and p[i] > p[i + 1] and p[i] > 0.15]
    out = []
    for i in sorted(peaks, key=lambda i: -p[i]):
        if all(abs(i - j) >= min_gap for j in out):
            out.append(i)
    return max(1, len(out))


def detect_layout(rgb: np.ndarray, columns_text=None, n_cols=None, signature=False, char_cuts=None):
    """返回 dict：image_size、stroke_px、columns（右→左），每列含 box 与 chars（上→下）。
    坐标均为归一化 0..1（相对 rgb）。columns_text: list[str] 右→左每列原文。
    char_cuts: 可选人工修正 {列号(1 起，右→左): [字与字之间的 y 切线（归一化）...]}，给了就不自动切该列。"""
    char_cuts = {int(k): v for k, v in (char_cuts or {}).items()}
    H, W = rgb.shape[:2]
    soft, binary = ink_masks(rgb)
    sw = stroke_width(binary)
    n, lab, st, cen = cv2.connectedComponentsWithStats(binary, 8)
    colsum = binary.sum(0).astype(np.float32)
    xs = np.where(colsum > 0)[0]
    x_lo, x_hi = (int(xs.min()), int(xs.max())) if xs.size else (0, W - 1)
    if columns_text:
        n_cols = len(columns_text)
    prof = _smooth(colsum, W / 120)
    if not n_cols:
        n_cols = _estimate_count(_smooth(colsum, W / 60), min_gap=W / 14)
    avg = (x_hi - x_lo) / max(n_cols, 1)
    cuts = _pick_cuts(prof, n_cols - 1, x_lo, x_hi, min_gap=avg * 0.55, raw=colsum)
    bounds = [x_lo] + cuts + [x_hi + 1]
    # 列号：右→左 0..n-1
    comp_col = np.full(n, -1)
    for i in range(1, n):
        cx = cen[i][0]
        k = int(np.searchsorted(bounds[1:-1], cx))
        comp_col[i] = n_cols - 1 - k
    columns = []
    for c in range(n_cols):
        ids = [i for i in range(1, n) if comp_col[i] == c]
        if not ids:
            continue
        x0 = min(st[i, 0] for i in ids); y0 = min(st[i, 1] for i in ids)
        x1 = max(st[i, 0] + st[i, 2] for i in ids); y1 = max(st[i, 1] + st[i, 3] for i in ids)
        colmask = np.isin(lab, ids)
        rowsum = colmask.sum(1).astype(np.float32)
        text = columns_text[c] if columns_text and c < len(columns_text) else ""
        k = len(text) if text else 0
        if signature and c == n_cols - 1:
            k = k + 1 if k else 0
        rp = _smooth(rowsum, max(2.0, sw * 0.8))
        if not k:
            k = _estimate_count(_smooth(rowsum, (x1 - x0) * 0.25), min_gap=(x1 - x0) * 0.6)
        if (c + 1) in char_cuts:
            ycuts = sorted(int(v * H) if v <= 1 else int(v) for v in char_cuts[c + 1])
            k = len(ycuts) + 1
        else:
            ycuts = _dp_cuts(rp, rowsum, k, y0, y1)
        yb = [y0] + ycuts + [y1]
        chars = []
        for j in range(k):
            ya, yb_ = yb[j], yb[j + 1]
            band = colmask[ya:yb_]
            xs_ = np.where(band.any(0))[0]
            if xs_.size == 0:
                xa, xb = x0, x1
            else:
                xa, xb = int(xs_.min()), int(xs_.max()) + 1
            t = text[j] if text and j < len(text) else ("款" if signature and c == n_cols - 1 and j == k - 1 else "")
            chars.append({"text": t, "box": [xa / W, ya / H, xb / W, yb_ / H]})
        columns.append({
            "index": c,
            "text": text,
            "box": [x0 / W, y0 / H, x1 / W, y1 / H],
            "x0": x0 / W, "x1": x1 / W,
            "chars": chars,
            "component_ids": [int(i) for i in ids],
        })
    return {
        "image_size": [W, H],
        "stroke_px": sw,
        "n_columns": len(columns),
        "columns": columns,
    }


def char_label_map(binary: np.ndarray, layout: dict) -> np.ndarray:
    """逐像素：属于第几个字（全篇顺序，右列先、上字先），无墨 = -1。
    组件按质心归列；同列内按字框纵向范围逐像素归字（跨字的连笔按行切开）。"""
    H, W = binary.shape
    n, lab, st, cen = cv2.connectedComponentsWithStats(binary.astype(np.uint8), 8)
    out = np.full((H, W), -1, np.int32)
    # layout 框为归一化坐标，直接乘当前分辨率（旧"按 image_size 换算"方案已废弃）
    col_x = []
    for col in layout["columns"]:
        col_x.append(((col["box"][0] + col["box"][2]) / 2) * W)
    order = 0
    char_ids = []
    for ci, col in enumerate(layout["columns"]):
        ids = []
        for cid in range(1, n):
            cx = cen[cid][0]
            # 最近列中心 + 在列框内
            dists = [abs(cx - x) for x in col_x]
            if int(np.argmin(dists)) == ci:
                ids.append(cid)
        m = np.isin(lab, ids)
        if not col["chars"]:
            continue  # 外部传入的 layout 可能含空列（无检出字），跳过标号
        ycuts = [ch["box"][1] * H for ch in col["chars"]] + [col["chars"][-1]["box"][3] * H]
        yy = np.arange(H)[:, None]
        for j in range(len(col["chars"])):
            ya = -1e9 if j == 0 else ycuts[j]
            yb = 1e9 if j == len(col["chars"]) - 1 else ycuts[j + 1]
            sel = m & (yy >= ya) & (yy < yb)
            out[sel] = order
            char_ids.append((ci, j))
            order += 1
    return out


def geodesic_order(skel: np.ndarray, start) -> np.ndarray:
    """骨架上 8 邻接 BFS 的步长距离（像素数）。未连通的骨架段依次从其左上点再出发并接续。"""
    H, W = skel.shape
    dist = np.full((H, W), -1.0, np.float32)
    pts = set(zip(*np.nonzero(skel)))
    if not pts:
        return dist
    offset = 0.0
    nb = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    s = start
    while pts:
        if s not in pts:
            s = min(pts, key=lambda p: p[0] + 0.6 * p[1])
        dq = deque([s]); dist[s] = offset; pts.discard(s)
        last = offset
        while dq:
            y, x = dq.popleft()
            d = dist[y, x]
            for dy, dx in nb:
                q = (y + dy, x + dx)
                if q in pts:
                    pts.discard(q)
                    dist[q] = d + (1.41421 if dy and dx else 1.0)
                    last = max(last, dist[q])
                    dq.append(q)
        offset = last + 1.0
        s = None if not pts else min(pts, key=lambda p: p[0] + 0.6 * p[1])
    return dist


def reveal_progress_map(binary: np.ndarray, labels: np.ndarray, n_chars: int, region_dilate: int = 4):
    """对每个字：组件按 先上后下、同高先左后右 排序，沿骨架从左上端点生长。
    返回 (prog, region, char_of)：prog=该像素在其字内的揭示进度 0..1；region=需要做揭示的像素（墨+抗锯齿边）；
    char_of=像素所属字号。skeleton 点的进度传给最近的墨像素（相当于按笔宽膨胀）。"""
    from scipy import ndimage as ndi

    H, W = binary.shape
    prog = np.zeros((H, W), np.float32)
    region = cv2.dilate(binary.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * region_dilate + 1,) * 2)) > 0
    char_of = np.full((H, W), -1, np.int32)
    weights = np.zeros(n_chars, np.float32)
    skel_all = zhang_suen(binary)
    for ci in range(n_chars):
        cm = labels == ci
        if not cm.any():
            continue
        ys, xs = np.nonzero(cm)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        pad = region_dilate + 2
        Y0, Y1, X0, X1 = max(0, y0 - pad), min(H, y1 + pad), max(0, x0 - pad), min(W, x1 + pad)
        sub = cm[Y0:Y1, X0:X1].astype(np.uint8)
        sk = (skel_all[Y0:Y1, X0:X1] > 0) & (sub > 0)
        n, lab, st, cen = cv2.connectedComponentsWithStats(sub, 8)
        ch_h = max(1, y1 - y0)
        comps = []
        for k in range(1, n):
            comps.append((int(st[k, 1] // (0.18 * ch_h)), st[k, 0], k))
        comps.sort()
        seg_len = []
        dist_maps = []
        for _, _, k in comps:
            ck = lab == k
            sk_k = sk & ck
            if not sk_k.any():  # 很小的点：用自身像素当骨架
                sk_k = ck.copy()
            ys_, xs_ = np.nonzero(sk_k)
            j = int(np.argmin(ys_ + 0.6 * xs_))
            d = geodesic_order(sk_k, (int(ys_[j]), int(xs_[j])))
            dist_maps.append((k, d))
            seg_len.append(float(d.max()) + 1.0)
        total = sum(seg_len)
        weights[ci] = total
        acc = 0.0
        sprog = np.full(sub.shape, -1.0, np.float32)
        for (k, d), L in zip(dist_maps, seg_len):
            on = d >= 0
            sprog[on] = (acc + d[on]) / total
            acc += L
        # 传播到墨像素 + 边
        has = sprog >= 0
        _, (iy, ix) = ndi.distance_transform_edt(~has, return_indices=True)
        full = sprog[iy, ix]
        reg = region[Y0:Y1, X0:X1]
        # 只给离本字墨最近的边缘像素赋值
        dist_c, (cy_, cx_) = ndi.distance_transform_edt(sub == 0, return_indices=True)
        own = reg & (dist_c <= region_dilate + 0.5) & (char_of[Y0:Y1, X0:X1] < 0)
        tgt = prog[Y0:Y1, X0:X1]
        tgt[own] = full[own]
        co = char_of[Y0:Y1, X0:X1]
        co[own] = ci
    region &= char_of >= 0
    return prog, region, char_of, weights


# ---------------------------------------------------------------- overlay
def draw_layout_overlay(rgb: np.ndarray, layout: dict, out_path) -> Path:
    im = Image.fromarray(rgb).convert("RGB")
    W, H = im.size
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype(FONT, max(16, W // 70))
    except Exception:
        f = ImageFont.load_default()
    pal = [(220, 40, 40), (30, 120, 220), (20, 160, 80), (200, 120, 0), (150, 40, 180), (0, 150, 160), (120, 120, 0)]
    for col in layout["columns"]:
        c = pal[col["index"] % len(pal)]
        x0, y0, x1, y1 = [v * s for v, s in zip(col["box"], (W, H, W, H))]
        d.rectangle([x0, y0, x1, y1], outline=c, width=max(2, W // 600))
        d.text((x0 + 4, max(0, y0 - W // 60)), f"列{col['index'] + 1}", fill=c, font=f)
        for j, ch in enumerate(col["chars"]):
            a0, b0, a1, b1 = [v * s for v, s in zip(ch["box"], (W, H, W, H))]
            d.rectangle([a0, b0, a1, b1], outline=c + (0,), width=1)
            d.text((a1 + 3, b0 + 2), ch.get("text") or str(j + 1), fill=c, font=f)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path, quality=90)
    return out_path


def load_or_detect(cfg_layout, rgb, columns_text=None, signature=False, cache_path=None, char_cuts=None):
    """cfg_layout: None/'auto' → 自动检测（有 cache 则读缓存）；dict → 直接用；路径 → 读 json。"""
    if isinstance(cfg_layout, dict):
        return cfg_layout
    if isinstance(cfg_layout, str) and cfg_layout not in ("", "auto") and Path(cfg_layout).exists():
        return json.loads(Path(cfg_layout).read_text("utf-8"))
    lay = detect_layout(rgb, columns_text=columns_text, signature=signature, char_cuts=char_cuts)
    if cache_path:
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        Path(cache_path).write_text(json.dumps(lay, ensure_ascii=False, indent=1), "utf-8")
    return lay


def main():
    ap = argparse.ArgumentParser(description="自动检测书法作品的列与字")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect")
    d.add_argument("--image", required=True, help="裁好纸边的作品图（如 作品_clean.png）")
    d.add_argument("--out", required=True)
    d.add_argument("--columns-text", default="", help="右→左每列原文，用 | 分隔")
    d.add_argument("--n-cols", type=int, default=0)
    d.add_argument("--signature", action="store_true", help="最后一列末尾有落款，多切一个")
    d.add_argument("--overlay", default="")
    d.add_argument("--cuts", default="", help='人工切字 JSON，如 \'{"4":[0.24,0.62,0.80]}\'')
    d.add_argument("--max-side", type=int, default=1800, help="检测时缩到这个尺寸以内（坐标仍是归一化）")
    a = ap.parse_args()
    rgb = np.asarray(Image.open(a.image).convert("RGB"))
    s = min(1.0, a.max_side / max(rgb.shape[:2]))
    if s < 1:
        rgb = cv2.resize(rgb, (int(rgb.shape[1] * s), int(rgb.shape[0] * s)), interpolation=cv2.INTER_AREA)
    texts = [t for t in a.columns_text.split("|")] if a.columns_text else None
    lay = detect_layout(rgb, columns_text=texts, n_cols=a.n_cols or None, signature=a.signature,
                        char_cuts=json.loads(a.cuts) if a.cuts else None)
    Path(a.out).write_text(json.dumps(lay, ensure_ascii=False, indent=1), "utf-8")
    print(f"列数 {lay['n_columns']}，笔宽约 {lay['stroke_px']:.1f}px → {a.out}")
    for c in lay["columns"]:
        print(f"  列{c['index'] + 1}: x {c['x0']:.3f}–{c['x1']:.3f}  字数 {len(c['chars'])}  {c['text']}")
    if a.overlay:
        draw_layout_overlay(rgb, lay, a.overlay)
        print("叠框检查图 →", a.overlay)


if __name__ == "__main__":
    main()
