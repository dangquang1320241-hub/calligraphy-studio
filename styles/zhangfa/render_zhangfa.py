#!/usr/bin/env python3
"""「章法解析」：作品全幅（或局部）上逐层叠加标注——行气（每列墨迹重心连线）、列框、字框（大小）、列间留白（疏密），
每层一句客观解说字幕；标注只画在照片之上，不改照片。列/字位置优先用 common/inkseg.py 自动检测（可在 config 写死覆盖）。
可选 TTS（与朗读版同一 edge-tts 设置），不配则只用字幕。底乐复用 common/music_synth.py。
用法：python3 render_zhangfa.py --config config.json [--out-dir 项目] [--preview 秒] [--skip-tts]

layers[]: {"type": "qi"|"colboxes"|"charboxes"|"gaps", "secs": 5.5, "caption": "…", "view": "full" | [x0,y0,x1,y1],
           "cols": [5,6]（1 起、右→左；qi/colboxes/gaps 缺省=全部）, "chars": ["5:1","5:3"]（charboxes 可选）,
           "highlight_cols": [4]（colboxes 加粗+淡填充）, "emph": ["5:1","5:3"]（charboxes 加粗+淡填充）, "window": [x0,y0,x1,y1]（该层镜头画框）,
           "color": "#b03a2e", "keep": false, "tts": true/false}
"""
import sys, json, argparse, datetime, subprocess, asyncio
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
import geometry as geom  # noqa
import inkseg  # noqa
from music_synth import synth  # noqa
from audio_mix import mix  # noqa
SR = 48000


def hexrgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255


def load_cfg(p):
    p = Path(p).resolve(); c = json.loads(p.read_text(encoding='utf-8')); c['_dir'] = p.parent; return c


def rel(c, s):
    s = Path(s); return s if s.is_absolute() else (c['_dir'] / s).resolve()


def layout_px(img, seg, cache=None):
    """inkseg 自动分列分字 → 像素坐标（作品图坐标系）。seg: {"crop":[x0,y0,x1,y1], "columns_text":[…], "signature":true}"""
    if cache and Path(cache).exists():
        return json.loads(Path(cache).read_text(encoding='utf-8'))
    C = seg['crop']
    rgb = np.asarray(img.convert('RGB'))[C[1]:C[3], C[0]:C[2]].copy()
    lay = inkseg.detect_layout(rgb, columns_text=seg.get('columns_text'), signature=seg.get('signature', False),
                               char_cuts=seg.get('char_cuts'))
    Wc, Hc = C[2] - C[0], C[3] - C[1]

    def px(b):
        return [round(C[0] + b[0] * Wc), round(C[1] + b[1] * Hc), round(C[0] + b[2] * Wc), round(C[1] + b[3] * Hc)]
    out = {'crop': C, 'columns': [{'index': col['index'], 'text': col['text'], 'box': px(col['box']),
                                   'chars': [{'text': ch['text'], 'box': px(ch['box'])} for ch in col['chars']]}
                                  for col in lay['columns']]}
    if cache:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        Path(cache).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    return out


def column_axis(gray, box, step=40):
    x0, y0, x1, y1 = box
    ink = gray[y0:y1, x0:x1] < 90
    pts = []
    for y in range(0, ink.shape[0] - step // 2, step):
        xs = np.where(ink[y:y + step])[1]
        if len(xs) > 30:
            pts.append((x0 + xs.mean(), y0 + y + min(step, ink.shape[0] - y) / 2))
    return pts


class Zhangfa:
    def __init__(self, c, proj):
        self.c = c; Lc = c['layout']; self.W, self.H = Lc['width'], Lc['height']; self.bg = hexrgb(Lc['bg_color'])
        geo = c.get('geometry')
        img, _ = geom.load_work_image(rel(c, c['image']), geo, rel(c, geo['cache']) if geo and geo.get('cache') else None)
        crop = c.get('paper_crop'); self.ox, self.oy = (crop[0], crop[1]) if crop else (0, 0)
        g = vk.grade(img, crop, **c.get('grade', {}))
        if c.get('paper_cutout', True):
            m = vk.paper_mask(img, crop)[..., None]; g = g * m + self.bg * (1 - m)
        self.paper = (g * 255 + 0.5).astype(np.uint8); self.ph, self.pw = g.shape[:2]
        self.gray = np.asarray(img.convert('L')).astype(np.float32)
        self.lay = c.get('layout_px') or layout_px(img, c['inkseg'], proj / '源码' / 'layout_px.json')
        self.cols = self.lay['columns']  # index 0 = 最右列
        self.cache = {}
        t = c['timing']['intro_secs']; self.layers = []
        for ly in c['layers']:
            d = ly.get('secs', 5.5)
            self.layers.append(dict(ly, t0=t, t1=t + d)); t += d
        self.total = t + c['timing']['end_secs']
        self.full = self.fit([self.ox, self.oy, self.ox + self.pw, self.oy + self.ph], Lc['window'])
        self.overlays = [self.draw_layer(ly) for ly in self.layers]

    def fit(self, b, win):
        r = [b[0] - self.ox, b[1] - self.oy, b[2] - self.ox, b[3] - self.oy]
        s = min((win[2] - win[0]) / (r[2] - r[0]), (win[3] - win[1]) / (r[3] - r[1]))
        return np.array([(r[0] + r[2]) / 2, (r[1] + r[3]) / 2, np.log(s), (win[0] + win[2]) / 2, (win[1] + win[3]) / 2])

    def view_state(self, ly):
        v = ly.get('view', 'full')
        win = ly.get('window') or self.c['layout']['window']
        return self.full if v == 'full' else self.fit(v, win)

    def camera(self, t):
        prev = self.full
        for ly in self.layers:
            st = self.view_state(ly)
            if t < ly['t0']:
                return prev
            if t < ly['t0'] + 1.0:
                return prev + (st - prev) * vk.ease_io_cubic((t - ly['t0']) / 1.0)
            prev = st
        te = self.total - self.c['timing']['end_secs']
        return prev + (self.full - prev) * vk.ease_io_cubic(max(0, t - te) / 1.0)

    def sel_cols(self, ly):
        idx = ly.get('cols') or list(range(1, len(self.cols) + 1))
        return [self.cols[i - 1] for i in idx]

    def draw_layer(self, ly):
        """标注层画在「作品纸面坐标」的透明图上（与纸面同尺寸×ss），渲染时随镜头一起变换"""
        ss = 2  # 超采样，缩放后线条更细腻
        im = Image.new('RGBA', (self.pw * ss, self.ph * ss), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
        col = tuple(int(v * 255) for v in hexrgb(ly.get('color', '#b03a2e')))
        wdt = int(ly.get('width', 5) * ss)
        P = lambda x, y: ((x - self.ox) * ss, (y - self.oy) * ss)
        f = vk.font(self.c.get('font'), int(ly.get('label_size', 30) * ss))
        if ly['type'] == 'qi':
            for cc in self.sel_cols(ly):
                pts = [P(x, y) for x, y in column_axis(self.gray, cc['box'])]
                if len(pts) > 1:
                    d.line(pts, fill=col + (235,), width=wdt, joint='curve')
                    for p in pts[::3]:
                        d.ellipse([p[0] - wdt, p[1] - wdt, p[0] + wdt, p[1] + wdt], fill=col + (235,))
        elif ly['type'] == 'colboxes':
            hi = set(ly.get('highlight_cols') or [])
            for k, cc in enumerate(self.sel_cols(ly)):
                b = cc['box']; on = (cc['index'] + 1) in hi
                d.rectangle([*P(b[0], b[1]), *P(b[2], b[3])], outline=col + (230,), width=wdt * (2 if on else 1),
                            fill=col + (60,) if on else None)
                if ly.get('label_counts', False):
                    n = len([ch for ch in cc['chars'] if ch['text'] and ch['text'] != '款'])
                    d.text(((P(b[0], 0)[0] + P(b[2], 0)[0]) / 2, P(0, b[3])[1] + 24 * ss), f"{n}字", font=f, fill=col + (255,), anchor='mt')
        elif ly['type'] == 'charboxes':
            boxes = []
            if ly.get('chars'):
                for ref in ly['chars']:
                    ci, ki = map(int, ref.split(':')); boxes.append(self.cols[ci - 1]['chars'][ki - 1])
            else:
                for cc in self.sel_cols(ly):
                    boxes += [ch for ch in cc['chars'] if ch['text'] != '款']
            emph = [self.cols[int(r.split(':')[0]) - 1]['chars'][int(r.split(':')[1]) - 1] for r in ly.get('emph', [])]
            for ch in boxes:
                b = ch['box']; on = any(ch is e for e in emph)
                d.rectangle([*P(b[0], b[1]), *P(b[2], b[3])], outline=col + (230,), width=wdt * (2 if on else 1),
                            fill=col + (55,) if on else None)
        elif ly['type'] == 'gaps':
            cs = sorted(self.sel_cols(ly), key=lambda cc: cc['box'][0])
            y0 = min(cc['box'][1] for cc in cs); y1 = max(cc['box'][3] for cc in cs)
            gaps = [(a['box'][2], b['box'][0]) for a, b in zip(cs, cs[1:]) if b['box'][0] > a['box'][2]]
            widths = [g1 - g0 for g0, g1 in gaps]
            mx = max(widths) if widths else 1
            for (g0, g1), w in zip(gaps, widths):
                alpha = int(40 + 120 * w / mx)
                d.rectangle([*P(g0, y0), *P(g1, y1)], fill=col + (alpha,))
        return np.asarray(im)

    def warp_layer(self, ov, M):
        ss = ov.shape[1] / self.pw
        M2 = M.copy(); M2[:, :2] /= ss
        out = cv2.warpAffine(ov, M2, (self.W, self.H), flags=cv2.INTER_AREA, borderValue=(0, 0, 0, 0))
        return out.astype(np.float32) / 255

    def text_layer(self, s, size):
        k = (s, size)
        if k not in self.cache:
            f = vk.font(self.c.get('font'), size); Lc = self.c['layout']
            lines, cur = [], ''
            for ch in s:
                if f.getlength(cur + ch) > Lc['caption_width'] and cur and ch not in '，。；：、」』）':
                    carry = ''
                    while cur and cur[-1] in '「『（《':  # 开引号不留在行尾
                        carry = cur[-1] + carry; cur = cur[:-1]
                    lines.append(cur); cur = carry + ch
                else:
                    cur += ch
            lines.append(cur)
            lh = int(size * 1.5)
            im = Image.new('RGBA', (Lc['caption_width'] + 40, lh * len(lines) + 20), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
            for i, l in enumerate(lines):
                d.text((20, 10 + i * lh + lh / 2), l, font=f, fill=(238, 230, 212, 255), anchor='lm', stroke_width=1, stroke_fill=(20, 16, 12, 220))
            self.cache[k] = np.asarray(im).astype(np.float32) / 255
        return self.cache[k]

    def comp(self, fr, lay, x, y, a):
        h, w = lay.shape[:2]; x, y = int(x), int(y)
        al = lay[..., 3:4] * a
        fr[y:y + h, x:x + w] = fr[y:y + h, x:x + w] * (1 - al) + lay[..., :3] * al

    def frame(self, t):
        Lc = self.c['layout']; tm = self.c['timing']
        cx, cy, ls, fx, fy = self.camera(t); s = np.exp(ls)
        M = np.array([[s, 0, fx - s * cx], [0, s, fy - s * cy]], np.float32)
        fr = cv2.warpAffine(self.paper, M, (self.W, self.H), flags=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC,
                            borderValue=tuple(int(v * 255) for v in self.bg)).astype(np.float32) / 255
        te = self.total - tm['end_secs']
        for ly, ov in zip(self.layers, self.overlays):
            if t < ly['t0'] + 0.6:
                continue
            a = vk.ease((t - ly['t0'] - 0.6) / 0.6)
            if not ly.get('keep') and t > ly['t1']:
                a *= 1 - vk.ease((t - ly['t1']) / 0.4)
            if t > te:
                a *= 1 - vk.ease((t - te) / 0.6)
            if a > 0.001:
                w = self.warp_layer(ov, M); al = w[..., 3:4] * a
                fr = fr * (1 - al) + w[..., :3] * al
            if ly['t0'] <= t < ly['t1'] and ly.get('caption'):
                ca = min(vk.ease((t - ly['t0'] - 0.8) / 0.35), vk.ease((ly['t1'] - 0.1 - t) / 0.3))
                if ca > 0:
                    self.comp(fr, self.text_layer(ly['caption'], Lc['caption_size']), Lc['caption_x'] - 20, Lc['caption_y'], ca)
        ta = min(1, t / 0.6, max(0, (tm['intro_secs'] - t) / 0.5))
        if ta > 0:
            lay = self.title_layer(self.c['title'], Lc['title_size'])
            self.comp(fr, lay, Lc['text_center_x'] - lay.shape[1] // 2, Lc['title_y'], ta)
            if self.c.get('title_sub'):
                lay2 = self.title_layer(self.c['title_sub'], Lc['title_size'] // 2)
                self.comp(fr, lay2, Lc['text_center_x'] - lay2.shape[1] // 2, Lc['title_y'] + Lc['title_size'] * 1.5, ta)
        ea = min(1, max(0, (t - te - 0.5) / 0.8))
        if ea > 0:
            lay = self.title_layer(self.c['end_card_text'].format(author=self.c['author']), Lc['end_size'])
            self.comp(fr, lay, Lc['text_center_x'] - lay.shape[1] // 2, Lc['end_y'], ea)
        return (np.clip(fr, 0, 1) * 255 + 0.5).astype(np.uint8)

    def title_layer(self, s, size):
        k = ('T', s, size)
        if k not in self.cache:
            f = vk.font(self.c.get('font'), size); w = int(f.getlength(s)) + 40
            im = Image.new('RGBA', (w, int(size * 1.6)), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((w / 2, size * 0.8), s, font=f, fill=(240, 232, 214, 255), anchor='mm')
            self.cache[k] = np.asarray(im).astype(np.float32) / 255
        return self.cache[k]


async def _tts(text, t, mp3):
    import edge_tts
    await edge_tts.Communicate(text, t['voice'], rate=t['rate'], pitch=t.get('pitch', '+0Hz'), volume=t.get('volume', '+0%')).save(str(mp3))


def build_audio(c, Z, proj):
    aud = proj / '素材' / 'music_mix.wav'; aud.parent.mkdir(parents=True, exist_ok=True)
    mp = dict(c['music']); music_db = mp.pop('music_db', -20); duck_db = mp.pop('duck_db', -7)
    music = synth(Z.total, sr=SR, accents=[ly['t0'] for ly in Z.layers], **mp)
    voice = None
    if c.get('tts', {}).get('enabled'):
        n = int(Z.total * SR); voice = np.zeros(n, np.float32)
        td = proj / '素材' / 'tts'; td.mkdir(parents=True, exist_ok=True)
        for i, ly in enumerate(Z.layers):
            if not ly.get('caption') or ly.get('tts') is False:
                continue
            mp3, wav = td / f'L{i + 1:02d}.mp3', td / f'L{i + 1:02d}.wav'
            if not wav.exists():
                asyncio.run(_tts(ly.get('tts_text') or ly['caption'], c['tts'], mp3))
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(mp3), '-ac', '1', '-ar', str(SR), str(wav)], check=True)
            sr, y = wavfile.read(wav); y = y.astype(np.float32) / 32768
            a = int((ly['t0'] + 0.8) * SR); e = min(n, a + len(y)); voice[a:e] += y[:e - a]
        out = mix(voice, music, SR, music_db=music_db, duck_db=duck_db)
    else:
        out = music * 10 ** (c.get('music_only_db', 0) / 20)
    tmp = aud.with_suffix('.raw.wav'); wavfile.write(tmp, SR, out.astype(np.float32))
    loud = c.get('loudness', {'I': -18, 'TP': -1.5, 'LRA': 11})
    r = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(tmp), '-af', f"loudnorm=I={loud['I']}:TP={loud['TP']}:LRA={loud['LRA']}:print_format=json",
                        '-f', 'null', '-'], capture_output=True, text=True).stderr
    js = json.loads(r[r.rfind('{'):r.rfind('}') + 1])
    af = (f"loudnorm=I={loud['I']}:TP={loud['TP']}:LRA={loud['LRA']}:measured_I={js['input_i']}:measured_TP={js['input_tp']}:"
          f"measured_LRA={js['input_lra']}:measured_thresh={js['input_thresh']}:offset={js['target_offset']}:linear=true")
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(tmp), '-af', af, '-ar', str(SR), str(aud)], check=True)
    tmp.unlink()
    return aud


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--config', required=True); ap.add_argument('--out-dir'); ap.add_argument('--preview', type=float)
    a = ap.parse_args(); c = load_cfg(a.config)
    proj = Path(a.out_dir).resolve() if a.out_dir else (c['_dir'].parent if c['_dir'].name == '源码' else c['_dir'])
    Z = Zhangfa(c, proj); fps = c['layout'].get('fps', 30)
    with open(proj / 'timeline.csv', 'w', encoding='utf-8') as f:
        f.write('t_sec,segment,kind,id,note\n0.000,S00,visual,title,片头\n')
        for i, ly in enumerate(Z.layers):
            f.write(f"{ly['t0']:.3f},S{i + 1:02d},visual,layer_{ly['type']},镜头/标注层\n")
            f.write(f"{ly['t0']:.3f},S{i + 1:02d},sfx,accent,底乐重音\n")
            f.write(f"{ly['t0'] + 0.8:.3f},S{i + 1:02d},subtitle,cap_{i + 1:02d},\"{ly.get('caption', '')}\"\n")
        f.write(f"{Z.total - c['timing']['end_secs']:.3f},S99,visual,end_card,片尾\n{Z.total:.3f},S99,cut,end,帧数 {round(Z.total * fps)}\n")
    aud = build_audio(c, Z, proj)
    out = proj / '成片' / f"{c.get('output_name', 'zhangfa')}_{Z.W}x{Z.H}.mp4"; out.parent.mkdir(exist_ok=True)
    nfr = round((a.preview or Z.total) * fps); tmp = out.with_suffix('.tmp.mp4')
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{Z.W}x{Z.H}', '-r', str(fps), '-i', '-',
                          '-i', str(aud), '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
                          '-movflags', '+faststart', '-c:a', 'aac', '-b:a', '192k', '-ar', str(SR), '-t', f'{nfr / fps:.6f}', str(tmp)], stdin=subprocess.PIPE)
    for i in range(nfr):
        p.stdin.write(Z.frame(i / fps).tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError('ffmpeg failed')
    if out.exists():
        out.rename(out.with_name(out.stem + f'.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}.mp4'))
    tmp.rename(out)
    rep = vk.ffprobe_report(str(out))
    chk = proj / '检查记录' / f'{datetime.datetime.now():%Y%m%d-%H%M}'; chk.mkdir(parents=True, exist_ok=True)
    (chk / 'ffprobe.json').write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding='utf-8')
    times = [1.8] + [ly['t1'] - 0.8 for ly in Z.layers] + [Z.total - 0.8]
    labels = ['片头'] + [f"L{i + 1} {ly['type']}" for i, ly in enumerate(Z.layers)] + ['片尾']
    lim = a.preview or Z.total
    keep = [(x, l) for x, l in zip(times, labels) if x < lim] or [(lim / 2, '预览')]
    fr = vk.grab_frames(str(out), [x for x, _ in keep], chk / 'frames')
    vk.contact_sheet(fr, [f'{l} {x:.1f}s' for x, l in keep], chk / '联系表.jpg')
    print(json.dumps({'mp4': str(out), 'sheet': str(chk / '联系表.jpg'), 'report': rep}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
