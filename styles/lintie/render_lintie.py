#!/usr/bin/env python3
"""书法「临帖对比」竖屏视频——参数化渲染器。
用法：python3 render_lintie.py --config /path/config.json [--out-dir 项目目录] [--preview 秒]
画面：片头全幅作品+标题 → 作品缩成上方导览图（红框标出当前字）→ 每字「参照帖 | 本作」两块并排从右滑入、停留、向左滑出，
      下方一句客观笔法/结构点评 → 拉回全幅 + 「作者名 · 书」片尾卡。
红线：两张图都只做裁切和全局调色（不改笔画）；参照帖只标「参照」，不说成作者所临原帖。
"""
import argparse, json, subprocess, sys, csv, datetime
from pathlib import Path
import numpy as np
import cv2
from PIL import Image, ImageDraw
from scipy.io import wavfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # calligraphy-studio 根目录
sys.path.insert(0, str(REPO / 'common'))
sys.path.insert(0, str(REPO))
import videokit as vk  # noqa
from music_synth import synth  # noqa
import geometry as geom  # noqa

SR = 48000


def hexrgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255


def load_cfg(p):
    p = Path(p).resolve()
    c = json.loads(p.read_text(encoding='utf-8'))
    c['_dir'] = p.parent
    return c


def rel(c, s):
    s = Path(s)
    return s if s.is_absolute() else (c['_dir'] / s).resolve()


def build_timeline(c):
    tm = c['timing']
    t = tm['intro_secs'] + tm['shrink_secs']
    chars = []
    for i, ch in enumerate(c['chars']):
        d = ch.get('secs', tm['char_secs'])
        chars.append({'i': i, 'start': t, 'end': t + d, 'char': ch['char']})
        t += d
    grow0 = t
    end0 = grow0 + tm['grow_secs']
    total = end0 + tm['end_secs']
    return {'intro_end': tm['intro_secs'], 'shrink_end': tm['intro_secs'] + tm['shrink_secs'], 'chars': chars,
            'grow_start': grow0, 'end_card_start': end0, 'total': total}


def write_timeline_csv(c, tl, path, fps):
    rows = [(0.0, 'S00', 'visual', 'intro_full', '片头全幅作品 + 标题'),
            (tl['intro_end'], 'S00', 'visual', 'shrink_to_map', '全幅缩为导览图')]
    for ch in tl['chars']:
        seg = f"S{ch['i'] + 1:02d}"
        rows += [(ch['start'], seg, 'cut', f'tiles_in_{seg}', f"「{ch['char']}」参照|本作 滑入"),
                 (ch['start'], seg, 'sfx', f'accent_{seg}', '底乐重音（低音拨弦）'),
                 (ch['start'] + c['timing'].get('comment_delay', 0.45), seg, 'subtitle', f'comment_{seg}',
                  c['chars'][ch['i']]['comment']),
                 (ch['end'] - c['timing'].get('slide_out', 0.45), seg, 'visual', f'tiles_out_{seg}', '向左滑出')]
    rows += [(tl['grow_start'], 'S99', 'visual', 'grow_full', '导览图拉回全幅'),
             (tl['end_card_start'], 'S99', 'visual', 'end_card', '片尾 作者名 · 书'),
             (tl['total'], 'S99', 'cut', 'end', f"总时长；帧数 {round(tl['total'] * fps)}")]
    rows.sort(key=lambda r: r[0])
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['t_sec', 'segment', 'kind', 'id', 'note'])
        for r in rows:
            w.writerow([f'{r[0]:.3f}', *r[1:]])


def build_audio(c, tl, out_wav):
    mp = dict(c['music'])
    mp.pop('music_db', None)
    mp.pop('duck_db', None)
    accents = [ch['start'] for ch in tl['chars']] if c['music'].get('accent_on_cut', True) else []
    mp.pop('accent_on_cut', None)
    y = synth(tl['total'], sr=SR, accents=accents, **mp)
    tmp = out_wav.with_suffix('.raw.wav')
    wavfile.write(tmp, SR, y)
    loud = c.get('loudness', {'I': -18, 'TP': -1.5, 'LRA': 11})
    r = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(tmp), '-af',
                        f"loudnorm=I={loud['I']}:TP={loud['TP']}:LRA={loud['LRA']}:print_format=json", '-f', 'null', '-'],
                       capture_output=True, text=True).stderr
    js = json.loads(r[r.rfind('{'):r.rfind('}') + 1])
    af = (f"loudnorm=I={loud['I']}:TP={loud['TP']}:LRA={loud['LRA']}:measured_I={js['input_i']}:"
          f"measured_TP={js['input_tp']}:measured_LRA={js['input_lra']}:measured_thresh={js['input_thresh']}:"
          f"offset={js['target_offset']}:linear=true")
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(tmp), '-af', af, '-ar', str(SR), str(out_wav)], check=True)
    tmp.unlink()


def wrap(text, f, maxw):
    lines, cur = [], ''
    for ch in text:
        if f.getlength(cur + ch) > maxw and cur:
            # 避免标点落在行首
            if ch in '，。；：、！？）」』':
                cur += ch
                lines.append(cur)
                cur = ''
                continue
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


class Renderer:
    def __init__(self, c, tl):
        self.c, self.tl = c, tl
        L = c['layout']
        self.L = L
        self.W, self.H = L['width'], L['height']
        self.bg = hexrgb(L['bg_color'])
        W = c['work']
        geo = W.get('geometry')
        img, self.geo_info = geom.load_work_image(rel(c, W['image']), geo,
                                                  rel(c, geo['cache']) if geo and geo.get('cache') else None)
        crop = W.get('paper_crop')
        self.ox, self.oy = (crop[0], crop[1]) if crop else (0, 0)
        gi = vk.grade(img, crop, **W.get('grade', {}))
        if W.get('paper_cutout', True):
            m = vk.paper_mask(img, crop)[..., None]
            gi = gi * m + self.bg[None, None, :] * (1 - m)
        self.paper = (gi * 255 + 0.5).astype(np.uint8)
        self.ph, self.pw = self.paper.shape[:2]
        self.full = self.fit([0, 0, self.pw, self.ph], L['full_window'])
        self.mapst = self.fit([0, 0, self.pw, self.ph], L['map_window'])
        # 逐字两块裁切（各自全局调色）
        R = c['reference']
        rgeo = R.get('geometry')
        rimg, _ = geom.load_work_image(rel(c, R['image']), rgeo,
                                       rel(c, rgeo['cache']) if rgeo and rgeo.get('cache') else None)
        rimg = rimg.convert('RGB')
        self.tiles = []
        T = L['tile']
        for ch in c['chars']:
            rt = self.make_tile(rimg, ch['ref_box'], R.get('grade', {}), T)
            wt = self.make_tile(img.convert('RGB'), ch['work_box'], W.get('grade', {}), T)
            self.tiles.append((rt, wt))
        self.cache = {}

    def make_tile(self, img, box, g, T):
        """只裁切 box 区域 + 全局调色，等比缩放放进方块（不拉伸、不补画）"""
        c = vk.grade(img, box, **g) if g else np.asarray(img.crop(tuple(box))).astype(np.float32) / 255
        im = Image.fromarray((c * 255 + 0.5).astype(np.uint8))
        inner = T['size'] - 2 * T['pad']
        s = min(inner / im.width, inner / im.height)
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        # 方块底色取裁切自身的纸色中位数，避免出现补画的感觉；外框细线
        arr = np.asarray(im).astype(np.float32) / 255
        paper = np.median(arr.reshape(-1, 3), 0)
        bgc = paper if T.get('bg', 'auto') == 'auto' else hexrgb(T['bg'])  # auto=用裁切自身纸色中位数铺底（只是底色，不涉及笔画）
        tile = np.ones((T['size'], T['size'], 3), np.float32) * bgc
        x = (T['size'] - im.width) // 2
        y = (T['size'] - im.height) // 2
        tile[y:y + im.height, x:x + im.width] = arr
        b = T.get('border', 2)
        col = hexrgb(T['border_color'])
        tile[:b] = col; tile[-b:] = col; tile[:, :b] = col; tile[:, -b:] = col
        return tile

    @staticmethod
    def fit(r, win):
        rw, rh = r[2] - r[0], r[3] - r[1]
        ww, wh = win[2] - win[0], win[3] - win[1]
        s = min(ww / rw, wh / rh)
        return np.array([(r[0] + r[2]) / 2, (r[1] + r[3]) / 2, np.log(s), (win[0] + win[2]) / 2, (win[1] + win[3]) / 2])

    def cam(self, t):
        tl = self.tl
        if t < tl['intro_end']:
            st = self.full.copy()
            st[2] += np.log(1 + 0.03 * vk.ease(t / tl['intro_end']))
            return st
        a = self.full.copy(); a[2] += np.log(1.03)
        if t < tl['shrink_end']:
            return a + (self.mapst - a) * vk.ease_io_cubic((t - tl['intro_end']) / (tl['shrink_end'] - tl['intro_end']))
        if t < tl['grow_start']:
            return self.mapst
        p = vk.ease_io_cubic((t - tl['grow_start']) / max(1e-6, tl['end_card_start'] - tl['grow_start']))
        return self.mapst + (self.full - self.mapst) * p

    def warp(self, st):
        cx, cy, ls, fx, fy = st
        s = np.exp(ls)
        M = np.array([[s, 0, fx - s * cx], [0, s, fy - s * cy]], np.float32)
        fr = cv2.warpAffine(self.paper, M, (self.W, self.H), flags=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=tuple(int(v * 255) for v in self.bg))
        return fr.astype(np.float32) / 255, s

    @staticmethod
    def comp(fr, layer, x, y, alpha=1.0):
        h, w = layer.shape[:2]
        H, W = fr.shape[:2]
        x, y = int(round(x)), int(round(y))
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        if x1 <= x0 or y1 <= y0 or alpha <= 0:
            return
        L = layer[y0 - y:y1 - y, x0 - x:x1 - x]
        if L.shape[2] == 4:
            a = L[..., 3:4] * alpha
            fr[y0:y1, x0:x1] = fr[y0:y1, x0:x1] * (1 - a) + L[..., :3] * a
        else:
            fr[y0:y1, x0:x1] = fr[y0:y1, x0:x1] * (1 - alpha) + L * alpha

    def text(self, key, lines, size, color, w, line_h=None, align='mm', stroke=0):
        if key in self.cache:
            return self.cache[key]
        f = vk.font(self.c.get('font'), size)
        line_h = line_h or int(size * 1.45)
        h = line_h * len(lines) + 20
        im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for k, ln in enumerate(lines):
            y = 10 + line_h * k + line_h / 2
            if align == 'mm':
                d.text((w / 2, y), ln, font=f, fill=color, anchor='mm', stroke_width=stroke, stroke_fill=(20, 16, 12, 220))
            else:
                d.text((0, y), ln, font=f, fill=color, anchor='lm', stroke_width=stroke, stroke_fill=(20, 16, 12, 220))
        arr = np.asarray(im).astype(np.float32) / 255
        self.cache[key] = arr
        return arr

    def frame(self, t):
        c, tl, L = self.c, self.tl, self.L
        st = self.cam(t)
        fr, s = self.warp(st)
        cx, cy, ls, fx, fy = st
        # 当前字索引 + 段内时间
        k, u = None, 0.0
        for ch in tl['chars']:
            if ch['start'] <= t < ch['end']:
                k, u = ch['i'], t - ch['start']
        # 导览图红框（字在作品中的位置）
        map_a = min(1.0, max(0.0, (t - tl['shrink_end'] + 0.2) / 0.4), max(0.0, (tl['grow_start'] + 0.3 - t) / 0.3))
        if k is not None and map_a > 0:
            prevk = max(0, k - 1)
            b1 = np.array(c['chars'][k]['work_box'], np.float32)
            b0 = np.array(c['chars'][prevk]['work_box'], np.float32)
            p = vk.ease_io_cubic(u / 0.5) if k > 0 else 1.0
            b = b0 + (b1 - b0) * p
            x0 = s * (b[0] - self.ox - cx) + fx; x1 = s * (b[2] - self.ox - cx) + fx
            y0 = s * (b[1] - self.oy - cy) + fy; y1 = s * (b[3] - self.oy - cy) + fy
            col = hexrgb(L['accent'])
            th = 3
            for (a0, a1, b0_, b1_) in [(y0 - th, y0, x0 - th, x1 + th), (y1, y1 + th, x0 - th, x1 + th),
                                       (y0, y1, x0 - th, x0), (y0, y1, x1, x1 + th)]:
                ya, yb, xa, xb = int(a0), int(a1), int(b0_), int(b1_)
                fr[max(0, ya):max(0, yb), max(0, xa):max(0, xb)] = fr[max(0, ya):max(0, yb), max(0, xa):max(0, xb)] * (1 - map_a) + col * map_a
        # 片头标题
        T = L['title']
        ta = min(1.0, t / 0.6, max(0.0, (tl['intro_end'] + 0.2 - t) / 0.6))
        if ta > 0:
            lay = self.text('title', [c['title']], T['size'], (240, 232, 214, 255), 900)
            self.comp(fr, lay, T['center_x'] - 450, T['center_y'] - lay.shape[0] / 2, ta)
            lay2 = self.text('title_sub', [c['title_sub']], T['sub_size'], (214, 204, 186, 255), 900)
            self.comp(fr, lay2, T['center_x'] - 450, T['center_y'] + T['size'] * 0.9 - lay2.shape[0] / 2, ta)
            lay3 = self.text('ref_note', [c['reference']['label']], T['note_size'], (200, 186, 160, 255), 900)
            self.comp(fr, lay3, T['center_x'] - 450, L['full_window'][3] + 60, ta)
        # 常驻页眉（导览图上方）
        ha = min(1.0, max(0.0, (t - tl['shrink_end'] + 0.3) / 0.5), max(0.0, (tl['grow_start'] + 0.3 - t) / 0.3))
        if ha > 0:
            H = L['header']
            lay = self.text('header', [c['header']], H['size'], (226, 216, 196, 255), 900)
            self.comp(fr, lay, H['center_x'] - 450, H['center_y'] - lay.shape[0] / 2, ha)
        # 两块并排滑入/停留/滑出
        if k is not None:
            Tl = L['tile']
            sin = c['timing'].get('slide_in', 0.55)
            sout = c['timing'].get('slide_out', 0.45)
            dur = tl['chars'][k]['end'] - tl['chars'][k]['start']
            if u < sin:
                off = (1 - vk.ease_io_cubic(u / sin)) * Tl['slide_px']
                al = vk.ease(u / (sin * 0.7))
            elif u > dur - sout:
                q = (u - (dur - sout)) / sout
                off = -vk.ease_io_cubic(q) * Tl['slide_px']
                al = 1 - vk.ease(q)
            else:
                off, al = 0.0, 1.0
            rt, wt = self.tiles[k]
            stagger = Tl.get('stagger_px', 60)
            self.comp(fr, rt, Tl['ref_x'] + off, Tl['y'], al)
            self.comp(fr, wt, Tl['work_x'] + off * 1.0 + (stagger if u < sin else 0) * (1 - vk.ease(u / sin)), Tl['y'], al)
            lab = L['tile_label']
            l1 = self.text('lab_ref', [c['reference']['tile_label']], lab['size'], (205, 192, 168, 255), Tl['size'])
            l2 = self.text('lab_work', [c['work']['tile_label']], lab['size'], (205, 192, 168, 255), Tl['size'])
            self.comp(fr, l1, Tl['ref_x'] + off, Tl['y'] + Tl['size'] + lab['gap'], al)
            self.comp(fr, l2, Tl['work_x'] + off, Tl['y'] + Tl['size'] + lab['gap'], al)
            # 点评
            C = L['comment']
            cd = c['timing'].get('comment_delay', 0.45)
            ca = min(1.0, max(0.0, (u - cd) / 0.35), max(0.0, (dur - 0.15 - u) / 0.35))
            if ca > 0:
                key = ('cm', k)
                if key not in self.cache:
                    f = vk.font(c.get('font'), C['size'])
                    head = f"「{c['chars'][k]['char']}」"
                    lines = wrap(c['chars'][k]['comment'], f, C['width'])
                    self.cache[key] = (self.text(('cmh', k), [head], C['head_size'], tuple(int(v * 255) for v in hexrgb(L['accent'])) + (255,), C['width']),
                                       self.text(('cmb', k), lines, C['size'], (238, 230, 212, 255), C['width'], align='lm', stroke=1))
                hl, bl = self.cache[key]
                dy = 10 * (1 - vk.ease((u - cd) / 0.4))
                self.comp(fr, hl, C['x'], C['y'] + dy, ca)
                self.comp(fr, bl, C['x'], C['y'] + hl.shape[0] - 6 + dy, ca)
        # 片尾
        E = L['end']
        ea = min(1.0, max(0.0, (t - tl['end_card_start']) / 0.8))
        if ea > 0:
            fr *= (1 - E.get('dim', 0.4) * ea)
            lay = self.text('end', [c['end_card_text'].format(author=c['author'])], E['size'], (240, 232, 214, 255), 900)
            self.comp(fr, lay, E['center_x'] - 450, E['center_y'] - lay.shape[0] / 2, ea)
            lay3 = self.text('ref_note_end', [c['reference']['label']], E['note_size'], (190, 178, 154, 255), 900)
            self.comp(fr, lay3, E['center_x'] - 450, E['center_y'] + E['size'], ea)
            if c['reference'].get('credit'):
                f = vk.font(c.get('font'), E.get('credit_size', 24))
                lines = wrap(c['reference']['credit'], f, 820)
                lay4 = self.text('credit', lines, E.get('credit_size', 24), (160, 150, 130, 255), 900)
                self.comp(fr, lay4, E['center_x'] - 450, E['center_y'] + E['size'] + E['note_size'] * 1.6, ea)
        return (np.clip(fr, 0, 1) * 255 + 0.5).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    ap.add_argument('--out-dir')
    ap.add_argument('--preview', type=float)
    a = ap.parse_args()
    c = load_cfg(a.config)
    proj = Path(a.out_dir).resolve() if a.out_dir else (c['_dir'].parent if c['_dir'].name == '源码' else c['_dir'])
    fps = c['layout'].get('fps', 30)
    tl = build_timeline(c)
    write_timeline_csv(c, tl, proj / 'timeline.csv', fps)
    (proj / '源码' / 'timeline_resolved.json').write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"total {tl['total']:.2f}s")
    aud = proj / '素材' / 'music_mix.wav'
    build_audio(c, tl, aud)
    R = Renderer(c, tl)
    out = proj / '成片' / f"{c.get('output_name', 'lintie')}_{R.W}x{R.H}.mp4"
    out.parent.mkdir(exist_ok=True)
    nfr = round((a.preview or tl['total']) * fps)
    tmp = out.with_suffix('.tmp.mp4')
    cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f"{R.W}x{R.H}", '-r', str(fps),
           '-i', '-', '-i', str(aud), '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
           '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-movflags', '+faststart', '-c:a', 'aac', '-b:a', '192k',
           '-ar', str(SR), '-t', f'{nfr / fps:.6f}', str(tmp)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(nfr):
        p.stdin.write(R.frame(i / fps).tobytes())
        if i % 150 == 0:
            print(f'frame {i}/{nfr}', flush=True)
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError('ffmpeg failed')
    if out.exists():
        out.rename(out.with_name(out.stem + f".bak-{datetime.datetime.now():%Y%m%d-%H%M%S}.mp4"))
    tmp.rename(out)
    rep = vk.ffprobe_report(str(out))
    chk = proj / '检查记录' / f'{datetime.datetime.now():%Y%m%d-%H%M}'
    chk.mkdir(parents=True, exist_ok=True)
    (chk / 'ffprobe.json').write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding='utf-8')
    times = [2.0]
    labels = ['片头 2.0s']
    for ch in tl['chars']:
        tt = ch['start'] + (ch['end'] - ch['start']) * 0.6
        times.append(tt)
        labels.append(f"S{ch['i'] + 1:02d}「{ch['char']}」{tt:.1f}s")
    times.append(tl['total'] - 1.0)
    labels.append(f"片尾 {times[-1]:.1f}s")
    if a.preview:
        times = [x for x in times if x < a.preview]
        labels = labels[:len(times)]
        if not times:
            times, labels = [a.preview / 2], [f'预览 {a.preview / 2:.1f}s']
    fr = vk.grab_frames(str(out), times, chk / 'frames')
    sheet = vk.contact_sheet(fr, labels, chk / '联系表.jpg')
    print(json.dumps({'mp4': str(out), 'sheet': str(sheet), 'report': rep}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
