#!/usr/bin/env python3
"""「书写路径」：按 config 里逐笔的遮罩多边形和顺序，逐笔「揭开」原作品照片（未揭开处显示空白纸色），
模拟书写过程。只用原图像素做遮罩显隐，不生成、不修补笔画。无朗读，底乐复用 common/music_synth.py。
用法：python3 render_shuxie.py --config config.json [--out-dir 项目] [--preview 秒]
config.strokes[]: {"poly": [[x,y],…], "secs": 0.5, "dir": [dx,dy] 揭开方向(可选，默认自上而下), "pause_after": 0.1}
坐标为（几何校正后的）作品图像素。
"""
import sys, json, argparse, datetime, subprocess
from pathlib import Path
import numpy as np
import cv2
from PIL import Image
from scipy.io import wavfile
HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # calligraphy-studio 根目录
sys.path.insert(0, str(REPO / 'common'))
sys.path.insert(0, str(REPO))
import videokit as vk  # noqa
import geometry as geom  # noqa
from music_synth import synth  # noqa
SR = 48000


def hexrgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255


def load_cfg(p):
    p = Path(p).resolve(); c = json.loads(p.read_text(encoding='utf-8')); c['_dir'] = p.parent; return c


def rel(c, s):
    s = Path(s); return s if s.is_absolute() else (c['_dir'] / s).resolve()


class Shuxie:
    def __init__(self, c):
        self.c = c
        L = c['layout']; self.W, self.H = L['width'], L['height']
        self.bg = hexrgb(L['bg_color'])
        geo = c.get('geometry')
        img, _ = geom.load_work_image(rel(c, c['image']), geo, rel(c, geo['cache']) if geo and geo.get('cache') else None)
        crop = c.get('paper_crop'); self.ox, self.oy = (crop[0], crop[1]) if crop else (0, 0)
        g = vk.grade(img, crop, **c.get('grade', {}))
        m = vk.paper_mask(img, crop)[..., None] if c.get('paper_cutout', True) else np.ones(g.shape[:2] + (1,), np.float32)
        self.ink = g
        # 「空白纸」：用纸色中位数铺底（不是修补，只是未揭开时的底）
        paper_col = np.median(g[(g.mean(2) > np.percentile(g.mean(2), 50))], 0)
        self.blank = np.ones_like(g) * paper_col
        self.mask_paper = m
        self.ph, self.pw = g.shape[:2]
        # 逐笔遮罩与时间
        t = c['timing']['intro_secs']
        self.strokes = []
        for s in c['strokes']:
            poly = np.array(s['poly'], np.float32) - [self.ox, self.oy]
            mk = np.zeros((self.ph, self.pw), np.uint8)
            cv2.fillPoly(mk, [poly.astype(np.int32)], 255)
            mk = cv2.GaussianBlur(mk.astype(np.float32) / 255, (0, 0), 1.2)
            d = np.array(s.get('dir', [0, 1]), np.float32); d /= (np.linalg.norm(d) + 1e-9)
            ys, xs = np.mgrid[0:self.ph, 0:self.pw]
            proj = xs * d[0] + ys * d[1]
            sel = mk > 0.01
            lo, hi = (proj[sel].min(), proj[sel].max()) if sel.any() else (0, 1)
            self.strokes.append({'mask': mk, 'proj': ((proj - lo) / max(hi - lo, 1)).astype(np.float32),
                                 't0': t, 't1': t + s.get('secs', 0.5)})
            t += s.get('secs', 0.5) + s.get('pause_after', c['timing'].get('pause', 0.08))
        self.write_end = t
        self.total = t + c['timing']['hold_secs'] + c['timing']['end_secs']
        self.cam_full = self.fit([0, 0, self.pw, self.ph], L['full_window'])
        self.cam_focus = [self.fit(np.array(f['box'], float) - [self.ox, self.oy, self.ox, self.oy], L['focus_window'])
                          for f in c.get('focus', [])]

    @staticmethod
    def fit(r, win):
        s = min((win[2] - win[0]) / (r[2] - r[0]), (win[3] - win[1]) / (r[3] - r[1]))
        return np.array([(r[0] + r[2]) / 2, (r[1] + r[3]) / 2, np.log(s), (win[0] + win[2]) / 2, (win[1] + win[3]) / 2])

    def reveal(self, t):
        a = np.zeros((self.ph, self.pw), np.float32)
        for s in self.strokes:
            if t <= s['t0']:
                continue
            p = 1.0 if t >= s['t1'] else (t - s['t0']) / (s['t1'] - s['t0'])
            front = np.clip((p * 1.08 - s['proj']) / 0.08, 0, 1)
            a = np.maximum(a, s['mask'] * front)
        if t > self.write_end + 0.2:  # 写完后完整显示原图（含遮罩未覆盖的落款等）
            a = np.maximum(a, min(1, (t - self.write_end - 0.2) / 0.8))
        return a[..., None]

    def camera(self, t):
        # focus: [{"box":[...], "t0":秒, "t1":秒}] 期间推近；否则全幅
        for f, st in zip(self.c.get('focus', []), self.cam_focus):
            if f['t0'] - 0.8 <= t <= f['t1'] + 0.8:
                p = min(vk.ease_io_cubic((t - f['t0'] + 0.8) / 0.8), vk.ease_io_cubic((f['t1'] + 0.8 - t) / 0.8))
                return self.cam_full + (st - self.cam_full) * p
        return self.cam_full

    def frame(self, t):
        a = self.reveal(t)
        paper = (self.blank * (1 - a) + self.ink * a)
        paper = paper * self.mask_paper + self.bg * (1 - self.mask_paper)
        cx, cy, ls, fx, fy = self.camera(t); s = np.exp(ls)
        M = np.array([[s, 0, fx - s * cx], [0, s, fy - s * cy]], np.float32)
        fr = cv2.warpAffine((paper * 255).astype(np.uint8), M, (self.W, self.H), flags=cv2.INTER_CUBIC,
                            borderValue=tuple(int(v * 255) for v in self.bg)).astype(np.float32) / 255
        L = self.c['layout']
        from PIL import ImageDraw
        def txt(s, size, cy, alpha):
            if alpha <= 0:
                return
            im = Image.new('RGBA', (900, size * 2), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((450, size), s, font=vk.font(self.c.get('font'), size), fill=(240, 232, 214, 255), anchor='mm')
            lay = np.asarray(im).astype(np.float32) / 255
            x, y = L['text_center_x'] - 450, int(cy - size)
            al = lay[..., 3:4] * alpha
            fr[y:y + lay.shape[0], x:x + 900] = fr[y:y + lay.shape[0], x:x + 900] * (1 - al) + lay[..., :3] * al
        tt = self.c['timing']
        txt(self.c['title'], L['title_size'], L['title_y'], min(1, t / 0.6, max(0, (tt['intro_secs'] - t) / 0.5)))
        ea = min(1, max(0, (t - (self.total - tt['end_secs'])) / 0.8))
        txt(self.c['end_card_text'].format(author=self.c['author']), L['end_size'], L['end_y'], ea)
        return (np.clip(fr, 0, 1) * 255 + 0.5).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--config', required=True); ap.add_argument('--out-dir'); ap.add_argument('--preview', type=float)
    a = ap.parse_args(); c = load_cfg(a.config)
    proj = Path(a.out_dir).resolve() if a.out_dir else (c['_dir'].parent if c['_dir'].name == '源码' else c['_dir'])
    R = Shuxie(c); fps = c['layout'].get('fps', 30)
    aud = proj / '素材' / 'music_mix.wav'; aud.parent.mkdir(parents=True, exist_ok=True)
    mp = dict(c['music']); y = synth(R.total, sr=SR, accents=[s['t0'] for s in R.strokes][::mp.pop('accent_every', 4)], **mp)
    wavfile.write(aud, SR, y * 10 ** (c.get('music_gain_db', -6) / 20))
    out = proj / '成片' / f"{c.get('output_name', 'shuxie')}_{R.W}x{R.H}.mp4"; out.parent.mkdir(exist_ok=True)
    nfr = round((a.preview or R.total) * fps); tmp = out.with_suffix('.tmp.mp4')
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{R.W}x{R.H}', '-r', str(fps), '-i', '-',
                          '-i', str(aud), '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p',
                          '-c:a', 'aac', '-b:a', '192k', '-t', f'{nfr / fps:.6f}', str(tmp)], stdin=subprocess.PIPE)
    for i in range(nfr):
        p.stdin.write(R.frame(i / fps).tobytes())
    p.stdin.close(); p.wait()
    if out.exists():
        out.rename(out.with_name(out.stem + f'.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}.mp4'))
    tmp.rename(out)
    rep = vk.ffprobe_report(str(out)); print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
