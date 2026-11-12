#!/usr/bin/env python3
"""「节奏卡点」：按 BPM（或拍点列表）在重拍上切换作品各列/各字的特写，切点帧 = round(拍点×fps)。
音乐：默认在本脚本里用 common/music_synth.py 的音色函数在拍点网格上自制（鼓点落拍、重音落切点）；
也可给 music_file（须已确认可商用并写进 LICENSES.md）+ rhythm.beats 拍点列表。
镜头可写死 box，也可用 {"col": n} / {"char": "n:k"} 引用 common/inkseg.py 的分列分字结果（layout_px 缓存，见章法解析模板）。
渲染后自动做「切点帧 vs 拍点」偏差检查（画面帧差找切点 + librosa 找音频起音），写入 检查记录/。
用法：python3 render_kadian.py --config config.json [--out-dir 项目] [--preview 秒] [--check-only]
"""
import sys, json, argparse, datetime, subprocess
from pathlib import Path
import numpy as np
import cv2
from PIL import Image, ImageDraw
from scipy.io import wavfile
from scipy.signal import fftconvolve
HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # calligraphy-studio 根目录
sys.path.insert(0, str(REPO / 'common'))
sys.path.insert(0, str(REPO))
import videokit as vk  # noqa
import geometry as geom  # noqa
import music_synth as ms  # noqa
SR = 48000


def hexrgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255


def load_cfg(p):
    p = Path(p).resolve(); c = json.loads(p.read_text(encoding='utf-8')); c['_dir'] = p.parent; return c


def rel(c, s):
    s = Path(s); return s if s.is_absolute() else (c['_dir'] / s).resolve()


def beat_grid(c, fps):
    """拍点（秒）。beats 优先；否则 bpm+offset 生成。拍点吸附到帧网格，保证切点帧精确。"""
    R = c['rhythm']
    if R.get('beats'):
        beats = list(R['beats'])
    else:
        b = 60.0 / R['bpm']; beats = list(np.arange(R.get('offset', 0.0), R['duration'] - 1e-6, b))
    return [round(x * fps) / fps for x in beats]


def beat_music(total, beats, cuts, seed=51, root=146.83, gain_drum=0.9, gain_pluck=0.5, pad_gain=0.35, reverb_secs=2.2, wet=0.25):
    """拍点网格上的自制配乐：每拍一记低鼓、切点（重拍）重鼓+宫音拨弦、拍上/半拍五声拨弦短句、底层长音。"""
    rng = np.random.default_rng(seed); n = int((total + reverb_secs) * SR); dry = np.zeros((n, 2))

    def add(sig, t, pan=0.5):
        s = int(round(t * SR)); e = min(n, s + len(sig))
        if e > s:
            dry[s:e, 0] += sig[:e - s] * np.cos(pan * np.pi / 2); dry[s:e, 1] += sig[:e - s] * np.sin(pan * np.pi / 2)

    def drum(strength):
        d = 0.5; tt = np.arange(int(d * SR)) / SR
        f = 55 + 45 * np.exp(-tt * 30)
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 9)
        click = rng.standard_normal(len(tt)) * np.exp(-tt * 300) * 0.25
        return (body + click) * strength

    add(ms._pad(root, total + 1, SR) * pad_gain, 0, 0.5)
    beat = beats[1] - beats[0] if len(beats) > 1 else 0.5
    cs = set(round(x, 4) for x in cuts)
    deg = 5
    for i, b in enumerate(beats):
        on = round(b, 4) in cs
        add(drum(1.0 if on else 0.45), b, 0.5)
        if on:
            add(ms._pluck(ms._pent_freq(root, 0), 2.5, SR, bright=0.7) * 0.8, b, 0.45)
        for sub in (0, 0.5):
            if rng.random() < (0.75 if sub == 0 else 0.35):
                deg = int(np.clip(deg + rng.choice([-2, -1, 1, 2, 0]), 3, 11))
                add(ms._pluck(ms._pent_freq(root, deg), 1.6, SR, bright=0.9) * gain_pluck * (0.6 + 0.3 * rng.random()),
                    b + sub * beat, 0.3 + 0.4 * rng.random())
    ir = ms._reverb_ir(SR, reverb_secs, rng)
    y = dry * (1 - wet) + np.stack([fftconvolve(dry[:, k], ir[:, k])[:n] for k in range(2)], 1) * wet * 1.6
    y = y[:int(total * SR)]; tt = np.arange(len(y)) / SR
    y *= (np.clip(tt / 0.05, 0, 1) * np.clip((total - tt) / 1.5, 0, 1))[:, None]
    return (y / (np.abs(y).max() + 1e-9) * 0.89).astype(np.float32)


def loudnorm(src, dst, loud):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(src), '-af', f"loudnorm=I={loud['I']}:TP={loud['TP']}:LRA={loud['LRA']}:print_format=json",
                        '-f', 'null', '-'], capture_output=True, text=True).stderr
    js = json.loads(r[r.rfind('{'):r.rfind('}') + 1])
    af = (f"loudnorm=I={loud['I']}:TP={loud['TP']}:LRA={loud['LRA']}:measured_I={js['input_i']}:measured_TP={js['input_tp']}:"
          f"measured_LRA={js['input_lra']}:measured_thresh={js['input_thresh']}:offset={js['target_offset']}:linear=true")
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(src), '-af', af, '-ar', str(SR), str(dst)], check=True)


class Kadian:
    def __init__(self, c, proj):
        self.c = c; Lc = c['layout']; self.W, self.H = Lc['width'], Lc['height']; self.bg = hexrgb(Lc['bg_color'])
        self.fps = Lc.get('fps', 30)
        geo = c.get('geometry')
        img, _ = geom.load_work_image(rel(c, c['image']), geo, rel(c, geo['cache']) if geo and geo.get('cache') else None)
        crop = c.get('paper_crop'); self.ox, self.oy = (crop[0], crop[1]) if crop else (0, 0)
        g = vk.grade(img, crop, **c.get('grade', {}))
        if c.get('paper_cutout', True):
            m = vk.paper_mask(img, crop)[..., None]; g = g * m + self.bg * (1 - m)
        self.paper = (g * 255 + 0.5).astype(np.uint8); self.ph, self.pw = g.shape[:2]
        self.layout = json.loads(rel(c, c['layout_px']).read_text(encoding='utf-8')) if c.get('layout_px') else None
        self.total = c['rhythm']['duration']
        self.beats = beat_grid(c, self.fps)
        n = c['rhythm'].get('beats_per_shot', 2)
        self.cuts = self.beats[::n]
        tend = self.total - c['end_secs']
        full = [self.ox, self.oy, self.ox + self.pw, self.oy + self.ph]
        shots = c['shots']; self.plan = []; k = 0
        for i, t0 in enumerate(self.cuts):
            t1 = self.cuts[i + 1] if i + 1 < len(self.cuts) else self.total
            if c.get('end_full', True) and t0 >= tend - 1e-6:
                self.plan.append((t0, self.total, full, '全幅')); self.t_end = t0; break
            sh = shots[k % len(shots)]; k += 1
            self.plan.append((t0, t1, self.shot_box(sh), sh.get('note', '')))
        else:
            self.t_end = tend
        self.cache = {}

    def shot_box(self, sh):
        if 'box' in sh:
            return sh['box']
        pad = sh.get('pad', 20)
        if 'col' in sh:
            b = self.layout['columns'][sh['col'] - 1]['box']
        else:
            ci, ki = map(int, sh['char'].split(':')); b = self.layout['columns'][ci - 1]['chars'][ki - 1]['box']
        return [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad]

    def fit(self, b, win):
        r = [b[0] - self.ox, b[1] - self.oy, b[2] - self.ox, b[3] - self.oy]
        s = min((win[2] - win[0]) / (r[2] - r[0]), (win[3] - win[1]) / (r[3] - r[1]), self.c['layout'].get('max_scale', 2.4))
        return (r[0] + r[2]) / 2, (r[1] + r[3]) / 2, s

    def text(self, s, size):
        k = (s, size)
        if k not in self.cache:
            f = vk.font(self.c.get('font'), size); w = int(f.getlength(s)) + 40
            im = Image.new('RGBA', (w, int(size * 1.6)), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((w / 2, size * 0.8), s, font=f, fill=(240, 232, 214, 255), anchor='mm')
            self.cache[k] = np.asarray(im).astype(np.float32) / 255
        return self.cache[k]

    def comp(self, fr, lay, cx, y, a):
        h, w = lay.shape[:2]; x = int(cx - w / 2); y = int(y); al = lay[..., 3:4] * a
        fr[y:y + h, x:x + w] = fr[y:y + h, x:x + w] * (1 - al) + lay[..., :3] * al

    def shot_at(self, t):
        cur = self.plan[0]
        for p in self.plan:
            if t >= p[0] - 1e-9:
                cur = p
        return cur

    def frame(self, t):
        Lc = self.c['layout']
        t0, t1, box, _ = self.shot_at(t)
        win = Lc['full_window'] if t0 >= self.t_end - 1e-9 else Lc['window']
        cx, cy, s = self.fit(box, win)
        u = (t - t0) / max(t1 - t0, 1e-6)
        s *= 1 + Lc.get('punch', 0.05) * (1 - vk.ease(min(1, (t - t0) / 0.2))) + Lc.get('push', 0.04) * u  # 切点「冲」一下再缓推
        fx, fy = (win[0] + win[2]) / 2, (win[1] + win[3]) / 2
        M = np.array([[s, 0, fx - s * cx], [0, s, fy - s * cy]], np.float32)
        fr = cv2.warpAffine(self.paper, M, (self.W, self.H), flags=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC,
                            borderValue=tuple(int(v * 255) for v in self.bg)).astype(np.float32) / 255
        fl = Lc.get('flash', 0.08) * max(0, 1 - (t - t0) / 0.1) if t0 > 0 and t >= t0 else 0.0
        fr = fr * (1 - fl) + fl
        if t < self.c.get('title_secs', 2.0):
            a = min(1, t / 0.3, (self.c.get('title_secs', 2.0) - t) / 0.3)
            self.comp(fr, self.text(self.c['title'], Lc['title_size']), Lc['text_center_x'], Lc['title_y'], a)
        if t >= self.t_end + 0.3:
            a = min(1, (t - self.t_end - 0.3) / 0.5)
            self.comp(fr, self.text(self.c['end_card_text'].format(author=self.c['author']), Lc['end_size']), Lc['text_center_x'], Lc['end_y'], a)
        return (np.clip(fr, 0, 1) * 255 + 0.5).astype(np.uint8)


def beat_check(mp4, K, out_json):
    """画面：逐帧（缩小）均值帧差的突变 = 切点帧；音频：librosa 起音。与设计切点逐一比对。"""
    import librosa
    fps = K.fps; w, h = 108, 192
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(mp4), '-vf', f'scale={w}:{h}', '-f', 'rawvideo', '-pix_fmt', 'gray', '-'],
                         capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.float32)
    diff = np.r_[0, np.abs(np.diff(fr, axis=0)).mean((1, 2))]
    thr = max(6.0, np.median(diff) * 6)
    det = [i for i in range(1, len(diff)) if diff[i] > thr and diff[i] >= diff[max(0, i - 2):i + 3].max()]
    sr = 22050
    pcm = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(mp4), '-vn', '-ac', '1', '-ar', str(sr), '-f', 'f32le', '-'],
                         capture_output=True, check=True).stdout
    y = np.frombuffer(pcm, np.float32)
    on = librosa.onset.onset_detect(y=y, sr=sr, units='time', backtrack=False, hop_length=128)
    rows = []
    for t in [p[0] for p in K.plan[1:]]:  # 只查真正换镜头的切点（片头首帧与片尾全幅定格后的拍点不算）
        f = round(t * fps)
        vf = min(det, key=lambda d: abs(d - f)) if det else None
        ao = float(min(on, key=lambda o: abs(o - t))) if len(on) else None
        rows.append({'cut_s': round(t, 4), 'design_frame': f, 'video_cut_frame': vf, 'video_dev_frames': None if vf is None else vf - f,
                     'audio_onset_s': None if ao is None else round(ao, 4), 'audio_dev_ms': None if ao is None else round((ao - t) * 1000, 1)})
    vd = [abs(r['video_dev_frames']) for r in rows if r['video_dev_frames'] is not None]
    ad = [abs(r['audio_dev_ms']) for r in rows if r['audio_dev_ms'] is not None]
    design = {r['design_frame'] for r in rows}
    extra = [d for d in det if all(abs(d - f) > 1 for f in design)]
    summ = {'n_cuts': len(rows), 'video_cut_frames_detected': len(det), 'extra_changes_frames': extra, 'video_max_dev_frames': max(vd) if vd else None,
            'audio_max_dev_ms': max(ad) if ad else None, 'audio_mean_dev_ms': round(float(np.mean(ad)), 1) if ad else None,
            'note': 'audio_dev 为 librosa onset 相对设计拍点（帧移≈5.8ms，onset 峰值本身略滞后于起音）；extra_changes_frames 一般是切点后 2–4 帧白闪/冲击收尾，不是切点'}
    out_json.write_text(json.dumps({'summary': summ, 'cuts': rows}, ensure_ascii=False, indent=1), encoding='utf-8')
    return summ


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--config', required=True); ap.add_argument('--out-dir'); ap.add_argument('--preview', type=float)
    ap.add_argument('--check-only', action='store_true', help='不渲染，只对已有成片做切点/拍点偏差检查')
    a = ap.parse_args(); c = load_cfg(a.config)
    proj = Path(a.out_dir).resolve() if a.out_dir else (c['_dir'].parent if c['_dir'].name == '源码' else c['_dir'])
    K = Kadian(c, proj); fps = K.fps
    if a.check_only:
        out = proj / '成片' / f"{c.get('output_name', 'kadian')}_{K.W}x{K.H}.mp4"
        chk = proj / '检查记录' / f'{datetime.datetime.now():%Y%m%d-%H%M}'; chk.mkdir(parents=True, exist_ok=True)
        print(json.dumps(beat_check(out, K, chk / '切点拍点偏差.json'), ensure_ascii=False, indent=1)); return
    aud = proj / '素材' / 'music_mix.wav'; aud.parent.mkdir(parents=True, exist_ok=True)
    tmpw = aud.with_suffix('.raw.wav')
    if c.get('music_file'):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(rel(c, c['music_file'])), '-t', str(K.total), '-ar', str(SR), '-ac', '2', str(tmpw)], check=True)
    else:
        wavfile.write(tmpw, SR, beat_music(K.total, K.beats, K.cuts, **c.get('music', {})))
    loudnorm(tmpw, aud, c.get('loudness', {'I': -16, 'TP': -1.5, 'LRA': 11})); tmpw.unlink()
    with open(proj / 'timeline.csv', 'w', encoding='utf-8') as f:
        f.write('t_sec,segment,kind,id,note\n')
        for b in K.beats:
            f.write(f'{b:.3f},,beat,beat,{"重拍/切点" if b in K.cuts else ""}\n')
        for i, (t0, t1, box, note) in enumerate(K.plan):
            f.write(f'{t0:.3f},S{i + 1:02d},cut,shot_{i + 1:02d},"{note} {box} 帧{round(t0 * fps)}"\n')
        f.write(f'{K.t_end + 0.3:.3f},S99,visual,end_card,片尾署名\n{K.total:.3f},S99,cut,end,帧数 {round(K.total * fps)}\n')
    out = proj / '成片' / f"{c.get('output_name', 'kadian')}_{K.W}x{K.H}.mp4"; out.parent.mkdir(exist_ok=True)
    nfr = round((a.preview or K.total) * fps); tmp = out.with_suffix('.tmp.mp4')
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{K.W}x{K.H}', '-r', str(fps), '-i', '-',
                          '-i', str(aud), '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
                          '-movflags', '+faststart', '-c:a', 'aac', '-b:a', '192k', '-ar', str(SR), '-t', f'{nfr / fps:.6f}', str(tmp)], stdin=subprocess.PIPE)
    for i in range(nfr):
        p.stdin.write(K.frame(i / fps).tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError('ffmpeg failed')
    if out.exists():
        out.rename(out.with_name(out.stem + f'.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}.mp4'))
    tmp.rename(out)
    rep = vk.ffprobe_report(str(out))
    chk = proj / '检查记录' / f'{datetime.datetime.now():%Y%m%d-%H%M}'; chk.mkdir(parents=True, exist_ok=True)
    (chk / 'ffprobe.json').write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding='utf-8')
    summ = beat_check(out, K, chk / '切点拍点偏差.json')
    lim = a.preview or K.total
    pts = [(min(t0 + 0.5, t1 - 0.1), f'S{i + 1:02d} {note}') for i, (t0, t1, _, note) in enumerate(K.plan)] + [(K.total - 0.4, '片尾')]
    pts = [(x, l) for x, l in pts if x < lim] or [(lim / 2, '预览')]
    frs = vk.grab_frames(str(out), [x for x, _ in pts], chk / 'frames')
    vk.contact_sheet(frs, [f'{l} {x:.2f}s' for x, l in pts], chk / '联系表.jpg', cols=6, thumb_w=270)
    print(json.dumps({'mp4': str(out), 'sheet': str(chk / '联系表.jpg'), 'beat_check': summ, 'report': rep}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
