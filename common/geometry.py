#!/usr/bin/env python3
"""作品照片几何校正（只做几何：旋转 / 四角透视拉正；高质量 Lanczos 插值；不改笔画、不修补、不生成）。

config 里的写法（放在作品图那一层，如 朗读版顶层 "geometry"、临帖对比 "work.geometry"）：
  "geometry": {"auto_deskew": true}                       # 自动：检测纸张四边→四角透视拉正；检测失败退回列投影求旋转角
  "geometry": {"rotate_deg": -1.2}                         # 手动旋转（度，正值=逆时针，与 OpenCV 一致）
  "geometry": {"perspective": [[x,y],[x,y],[x,y],[x,y]]}   # 手动四角（原图像素，顺序 左上、右上、右下、左下）
可选："margin_px": 40（四周保留的原图边距，供抠纸用）、"extra_rotate_deg": 0（拉正后再微转）、"cache": "../素材/作品_校正.png"（相对 config）
校正后，config 里所有坐标（paper_crop、region、work_box…）都以「校正后图像」为准。
命令行：python3 geometry.py 原图.jpg 输出.png [--rotate D | --auto] [--report 对比图.jpg]
"""
import json, sys, argparse
from pathlib import Path
import numpy as np
import cv2
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import videokit as vk  # noqa


def _robust_line(u, v):
    u = np.asarray(u, float); v = np.asarray(v, float)
    for _ in range(4):
        p = np.polyfit(u, v, 1)
        r = v - np.polyval(p, u)
        keep = np.abs(r) < max(2.5 * np.median(np.abs(r)), 1.5)
        u, v = u[keep], v[keep]
    p = np.polyfit(u, v, 1)
    return p, float(np.abs(v - np.polyval(p, u)).max())


def detect_paper_quad(img):
    """纸张四边：沿每条边逐列/逐行取遮罩边缘点，稳健直线拟合，四线求交得四角。返回 corners(4x2) 与各边角度。"""
    m = vk.paper_mask(img, None, feather=0) > 0.5
    cols = np.where(m.mean(0) > 0.3)[0]; rows = np.where(m.mean(1) > 0.3)[0]
    if len(cols) < 50 or len(rows) < 50:
        raise RuntimeError('paper not found')
    inset = 60
    xs = np.arange(cols.min() + inset, cols.max() - inset, 6)
    ys = np.arange(rows.min() + inset, rows.max() - inset, 6)
    top = [np.where(m[:, x])[0].min() for x in xs]
    bot = [np.where(m[:, x])[0].max() for x in xs]
    left = [np.where(m[y, :])[0].min() for y in ys]
    right = [np.where(m[y, :])[0].max() for y in ys]
    (pt, rt), (pb, rb) = _robust_line(xs, top), _robust_line(xs, bot)      # y = a x + b
    (pl, rl), (pr, rr) = _robust_line(ys, left), _robust_line(ys, right)   # x = c y + d

    def inter(ph, pv):
        a, b = ph; c, d = pv
        y = (a * d + b) / (1 - a * c)
        return [c * y + d, y]
    corners = np.array([inter(pt, pl), inter(pt, pr), inter(pb, pr), inter(pb, pl)], np.float64)
    info = {'edge_deg': {'top': float(np.degrees(np.arctan(pt[0]))), 'bottom': float(np.degrees(np.arctan(pb[0]))),
                         'left': float(np.degrees(np.arctan(pl[0]))), 'right': float(np.degrees(np.arctan(pr[0])))},
            'edge_max_residual_px': {'top': rt, 'bottom': rb, 'left': rl, 'right': rr}}
    return corners, info


def ink_projection_angle(img, rng=3.0, step=0.05, region=None):
    """墨迹列投影法：旋转到哪个角度时竖向投影方差最大（列最「锐」）。返回 OpenCV 旋转角（正=逆时针）。"""
    g = np.asarray(img.convert('L'))
    if region:
        g = g[region[1]:region[3], region[0]:region[2]]
    ink = (g < 90).astype(np.uint8) * 255
    h, w = ink.shape
    best = (-1, 0.0)
    for d in np.arange(-rng, rng + 1e-9, step):
        M = cv2.getRotationMatrix2D((w / 2, h / 2), d, 1)
        r = cv2.warpAffine(ink, M, (w, h), flags=cv2.INTER_LINEAR)
        v = float(np.var(r.sum(0).astype(np.float64)))
        if v > best[0]:
            best = (v, float(d))
    return best[1]


def column_tilts(img, cols, y_range=None):
    """每列墨迹质心随高度的倾角（度，正=往下走向右偏）。cols=[(x0,x1),…]"""
    g = np.asarray(img.convert('L')) < 90
    out = []
    for a, b in cols:
        sub = g[:, max(0, a - 15):b + 15]
        ys = np.where(sub.sum(1) > 2)[0]
        if y_range:
            ys = ys[(ys >= y_range[0]) & (ys <= y_range[1])]
        pts = []
        for y0 in range(ys.min(), ys.max(), 40):
            xx = np.where(sub[y0:y0 + 40])[1]
            if len(xx) > 80:
                pts.append((y0 + 20, xx.mean() + a - 15, len(xx)))
        p = np.array(pts)
        k = np.polyfit(p[:, 0], p[:, 1], 1, w=np.sqrt(p[:, 2]))[0]
        out.append(float(np.degrees(np.arctan(k))))
    return out


def rectify(img, geo):
    """返回 (校正后 PIL 图, info)。geo 见模块说明。"""
    img = img.convert('RGB')
    src = np.asarray(img)
    margin = int(geo.get('margin_px', 40))
    info = {'mode': None}
    if geo.get('perspective') or geo.get('auto_deskew'):
        if geo.get('perspective'):
            q = np.array(geo['perspective'], np.float64); info['mode'] = 'perspective(manual)'
        else:
            try:
                q, det = detect_paper_quad(img); info.update(det); info['mode'] = 'perspective(auto)'
            except Exception as e:
                info['auto_fail'] = str(e)
                q = None
        if q is not None:
            w = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
            h = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
            W, H = int(round(w)) + 2 * margin, int(round(h)) + 2 * margin
            dst = np.array([[margin, margin], [margin + w, margin], [margin + w, margin + h], [margin, margin + h]], np.float64)
            Hm = cv2.getPerspectiveTransform(q.astype(np.float32), dst.astype(np.float32))
            if geo.get('extra_rotate_deg'):  # 可选：拉正后再微调旋转（度，正=逆时针），用于让墨迹列更竖直
                R = cv2.getRotationMatrix2D((W / 2, H / 2), float(geo['extra_rotate_deg']), 1)
                Hm = np.vstack([R, [0, 0, 1]]) @ Hm
                info['extra_rotate_deg'] = float(geo['extra_rotate_deg'])
            out = cv2.warpPerspective(src, Hm, (W, H), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)
            info.update({'corners_src': q.round(2).tolist(), 'size': [W, H], 'homography': Hm.tolist(),
                         'paper_rect': [margin, margin, int(round(margin + w)), int(round(margin + h))]})
            return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)), info
        geo = dict(geo); geo['rotate_deg'] = ink_projection_angle(img)
    if geo.get('rotate_deg') is not None:
        d = float(geo['rotate_deg'])
        h, w = src.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), d, 1)
        cos, sin = abs(M[0, 0]), abs(M[0, 1])
        W, H = int(w * cos + h * sin), int(w * sin + h * cos)
        M[0, 2] += W / 2 - w / 2; M[1, 2] += H / 2 - h / 2
        out = cv2.warpAffine(src, M, (W, H), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)
        info.update({'mode': info['mode'] or 'rotate', 'rotate_deg': d, 'size': [W, H], 'affine': M.tolist()})
        return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)), info
    info['mode'] = 'none'
    return img, info


def load_work_image(path, geo, cache=None):
    """模板入口：没有 geometry 就原样返回；有就校正，并缓存到 cache（同参数同原图复用）。"""
    img = Image.open(path)
    if not geo:
        return img, {'mode': 'none'}
    key = {'src_sha256': vk.sha256(path), 'geo': {k: v for k, v in geo.items() if k != 'cache'}}
    if cache:
        cache = Path(cache)
        meta = cache.with_suffix('.json')
        if cache.exists() and meta.exists():
            m = json.loads(meta.read_text())
            if m.get('key') == key:
                return Image.open(cache), m['info']
    out, info = rectify(img, geo)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        out.save(cache)
        cache.with_suffix('.json').write_text(json.dumps({'key': key, 'info': info}, ensure_ascii=False, indent=1))
    return out, info


def before_after_sheet(before, after, out, step=100, label_font=None):
    """校正前后并排，叠等距参考竖线/横线（青色），便于目测列与纸边是否正。"""
    from PIL import ImageDraw
    ims = []
    for im, lab in [(before, '校正前'), (after, '校正后')]:
        im = im.convert('RGB').copy()
        d = ImageDraw.Draw(im)
        for x in range(0, im.width, step):
            d.line([(x, 0), (x, im.height)], fill=(0, 200, 255), width=1)
        for y in range(0, im.height, step):
            d.line([(0, y), (im.width, y)], fill=(0, 200, 255), width=1)
        d.text((20, 10), lab, font=vk.font(label_font, 48), fill=(255, 40, 40))
        ims.append(im)
    W = sum(i.width for i in ims) + 20
    H = max(i.height for i in ims)
    s = Image.new('RGB', (W, H), (30, 30, 30))
    s.paste(ims[0], (0, 0)); s.paste(ims[1], (ims[0].width + 20, 0))
    s.save(out, quality=92)
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('src'); ap.add_argument('out')
    ap.add_argument('--rotate', type=float); ap.add_argument('--auto', action='store_true')
    ap.add_argument('--report')
    a = ap.parse_args()
    geo = {'rotate_deg': a.rotate} if a.rotate is not None else {'auto_deskew': True}
    im = Image.open(a.src)
    out, info = rectify(im, geo)
    out.save(a.out)
    print(json.dumps(info, ensure_ascii=False, indent=1))
    if a.report:
        before_after_sheet(im, out, a.report)


def segment_columns(img, box=None, thr=90, min_w=40, smooth=15, on_thr=6):
    """竖排墨迹列投影分列：返回 [(x0,x1,y0,y1),…]（从左到右；竖排阅读时请倒序）。box 限定测量范围。"""
    from scipy.ndimage import uniform_filter1d
    g = np.asarray(img.convert('L')).astype(float)
    x0, y0, x1, y1 = box or (0, 0, g.shape[1], g.shape[0])
    ink = g[y0:y1, x0:x1] < thr
    col = uniform_filter1d(ink.sum(0).astype(float), smooth)
    on = col > on_thr
    runs, st = [], None
    for i, v in enumerate(on):
        if v and st is None:
            st = i
        if not v and st is not None:
            runs.append((st, i)); st = None
    if st is not None:
        runs.append((st, len(on)))
    out = []
    for a, b in runs:
        if b - a >= min_w:
            r = uniform_filter1d(ink[:, a:b].sum(1).astype(float), 9)
            ys = np.where(r > 1)[0]
            out.append((a + x0, b + x0, int(ys.min()) + y0, int(ys.max()) + y0))
    return out
