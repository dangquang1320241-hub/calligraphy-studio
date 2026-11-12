#!/usr/bin/env python3
"""书法作品「诗文朗读版」竖屏视频——参数化渲染器。
用法：python3 render_langdu.py --config /path/config.json [--out-dir 项目目录] [--skip-tts] [--preview 秒]
流程：图像裁切调色 → edge-tts 逐句合成（取字边界）→ 生成统一 timeline.csv → 合成底乐并闪避混音 →
      逐帧纯函数渲染（镜头停在每句对应区域）→ ffmpeg 编码 → ffprobe 校验 + 关键帧联系表。
"""
import argparse, asyncio, json, subprocess, sys, csv, shutil, datetime, os
from pathlib import Path
import numpy as np
import cv2
from PIL import Image, ImageDraw
from scipy.io import wavfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # calligraphy-studio 根目录
sys.path.insert(0, str(REPO / 'common'))
sys.path.insert(0, str(REPO))
import core.tts as tts  # noqa: E402
import videokit as vk  # noqa
from music_synth import synth  # noqa
from audio_mix import mix  # noqa
import geometry as geom  # noqa

SR = 48000
PUNCT = set('，。；：、！？…,.;:!?“”「」《》 ')


def load_cfg(p):
    p = Path(p).resolve()
    c = json.loads(p.read_text(encoding='utf-8'))
    c['_dir'] = p.parent
    return c


def rel(c, s):
    s = Path(s)
    return s if s.is_absolute() else (c['_dir'] / s).resolve()


# ---------------- TTS ----------------
def run_tts(c, tts_dir, skip=False):
    """逐句合成（经 core.tts 可替换接口，含哈希缓存）。产出句序号副本供 build_audio 使用。"""
    tts_dir.mkdir(parents=True, exist_ok=True)
    provider = c.get('tts_provider', 'edge')
    meta = []
    for i, s in enumerate(c['sentences']):
        text = s.get('tts_text') or s['text']
        mp3, wav, m = tts.synthesize_cached(text, tts_dir, provider, c['tts'])
        stem = tts_dir / f's{i + 1:02d}'
        if mp3.resolve() != stem.with_suffix('.mp3').resolve():
            shutil.copyfile(mp3, stem.with_suffix('.mp3'))
            shutil.copyfile(wav, stem.with_suffix('.wav'))
            shutil.copyfile(tts_dir / f"tts_{m['key']}.json", stem.with_suffix('.json'))
        meta.append(m)
    return meta


def char_times(sub_text, words):
    """把 TTS 字边界分摊到字幕里的每个非标点字：返回 [(char, t_start, t_end)]（相对句音频起点）"""
    chars = [ch for ch in sub_text if ch not in PUNCT]
    seq = []
    for w in words:
        wc = [ch for ch in w['text'] if ch not in PUNCT] or [w['text']]
        for k, ch in enumerate(wc):
            a = w['start'] + w['dur'] * k / len(wc)
            seq.append((a, w['start'] + w['dur'] * (k + 1) / len(wc)))
    out = []
    for j, ch in enumerate(chars):
        if j < len(seq):
            out.append((ch, *seq[j]))
        else:  # 边界数与字数不符时，沿用最后时间
            last = seq[-1] if seq else (0, 0.5)
            out.append((ch, last[1], last[1] + 0.2))
    return out


# ---------------- timeline ----------------
def build_timeline(c, meta):
    tm = c['timing']
    t = tm['title_secs'] + tm['intro_secs']
    sents = []
    for i, (s, m) in enumerate(zip(c['sentences'], meta)):
        w = m['words']
        onset = w[0]['start'] if w else 0.0
        offset = (w[-1]['start'] + w[-1]['dur']) if w else m['file_dur']
        speech_start = t
        clip_start = speech_start - onset
        speech_end = speech_start + (offset - onset)
        ct = [(ch, clip_start + a, clip_start + b) for ch, a, b in char_times(s.get('subtitle', s['text']), w)]
        sents.append({'i': i, 'clip_start': clip_start, 'start': speech_start, 'end': speech_end, 'chars': ct,
                      'region': s['region'], 'subtitle': s.get('subtitle', s['text'])})
        gap = s.get('gap_after', tm['gap_secs'])
        t = speech_end + gap
    last_end = sents[-1]['end'] + tm['tail_hold']
    pull = tm.get('pull_back_secs', 2.0)
    end_card_start = last_end + pull
    total = end_card_start + tm['end_secs']
    return {'sents': sents, 'title_end': tm['title_secs'], 'intro_end': tm['title_secs'] + tm['intro_secs'],
            'last_end': last_end, 'end_card_start': end_card_start, 'total': total}


def write_timeline_csv(tl, path, fps):
    rows = [(0.0, 'S00', 'visual', 'title_card', '片头标题卡')]
    rows.append((tl['title_end'], 'S00', 'cut', 'intro_full', '全幅作品亮相'))
    for s in tl['sents']:
        seg = f"S{s['i'] + 1:02d}"
        rows.append((s['start'] - 0.9, seg, 'visual', f'cam_move_{seg}', f"镜头移向区域 {s['region']}"))
        rows.append((s['clip_start'], seg, 'vo', f'vo_{seg}', f"TTS 句音频起点（含前导静音）"))
        rows.append((s['start'], seg, 'subtitle', f'sub_in_{seg}', s['subtitle']))
        rows.append((s['end'], seg, 'subtitle', f'sub_spoken_{seg}', '本句朗读结束'))
    rows.append((tl['last_end'], 'S99', 'visual', 'pull_back', '拉回全幅'))
    rows.append((tl['end_card_start'], 'S99', 'visual', 'end_card', '片尾 作者名 · 书'))
    rows.append((tl['total'], 'S99', 'cut', 'end', f"总时长；帧数 {round(tl['total'] * fps)}"))
    rows.sort(key=lambda r: r[0])
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['t_sec', 'segment', 'kind', 'id', 'note'])
        for r in rows:
            w.writerow([f'{r[0]:.3f}', *r[1:]])


# ---------------- audio ----------------
def build_audio(c, tl, meta, tts_dir, out_wav):
    n = int(tl['total'] * SR)
    voice = np.zeros(n, dtype=np.float32)
    for s in tl['sents']:
        sr, y = wavfile.read(tts_dir / f"s{s['i'] + 1:02d}.wav")
        y = y.astype(np.float32) / 32768.0 if y.dtype == np.int16 else y.astype(np.float32)
        a = int(s['clip_start'] * SR)
        e = min(n, a + len(y))
        voice[a:e] += y[:e - a]
    mp = dict(c['music'])
    music_db = mp.pop('music_db', -20)
    duck_db = mp.pop('duck_db', -6)
    music = synth(tl['total'], sr=SR, **mp)
    out = mix(voice, music, SR, music_db=music_db, duck_db=duck_db)
    tmp = out_wav.with_suffix('.raw.wav')
    wavfile.write(tmp, SR, out)
    wavfile.write(out_wav.parent / 'music_only.wav', SR, music)
    loud = c.get('loudness', {'I': -16, 'TP': -1.5, 'LRA': 11})
    # 两遍 loudnorm
    r = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(tmp), '-af',
                        f"loudnorm=I={loud['I']}:TP={loud['TP']}:LRA={loud['LRA']}:print_format=json", '-f', 'null', '-'],
                       capture_output=True, text=True).stderr
    js = json.loads(r[r.rfind('{'):r.rfind('}') + 1])
    af = (f"loudnorm=I={loud['I']}:TP={loud['TP']}:LRA={loud['LRA']}:measured_I={js['input_i']}:"
          f"measured_TP={js['input_tp']}:measured_LRA={js['input_lra']}:measured_thresh={js['input_thresh']}:"
          f"offset={js['target_offset']}:linear=true")
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(tmp), '-af', af, '-ar', str(SR), str(out_wav)], check=True)
    tmp.unlink()


# ---------------- video ----------------
class Renderer:
    def __init__(self, c, tl):
        self.c, self.tl = c, tl
        L = c['layout']
        self.W, self.H = L['width'], L['height']
        self.bg = np.array([int(L['bg_color'][i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255
        crop = c.get('paper_crop')
        self.ox, self.oy = (crop[0], crop[1]) if crop else (0, 0)
        g = c.get('grade', {})
        geo = c.get('geometry')
        img, self.geo_info = geom.load_work_image(rel(c, c['image']), geo,
                                                  rel(c, geo['cache']) if geo and geo.get('cache') else None)
        gi = vk.grade(img, crop, **g)
        if c.get('paper_cutout', True):  # 纸外墙面换成背景色；纸内像素不动
            m = vk.paper_mask(img, crop)[..., None]
            gi = gi * m + self.bg[None, None, :] * (1 - m)
        self.paper = (gi * 255 + 0.5).astype(np.uint8)
        self.ph, self.pw = self.paper.shape[:2]
        self.win = L['focus_window']  # [x0,y0,x1,y1] 帧内放置当前区域的窗口
        self.font_path = c.get('font')
        self._sub_cache = {}
        self._card_cache = {}
        # 镜头关键状态
        self.full_state = self.fit_state([0, 0, self.pw, self.ph], L.get('full_window', [60, 360, 1020, 1240]))
        self.states = [self.fit_state(self.reg(s['region']), self.win) for s in tl['sents']]

    def reg(self, r):
        return [r[0] - self.ox, r[1] - self.oy, r[2] - self.ox, r[3] - self.oy]

    @staticmethod
    def fit_state(r, win):
        rw, rh = r[2] - r[0], r[3] - r[1]
        ww, wh = win[2] - win[0], win[3] - win[1]
        s = min(ww / rw, wh / rh)
        return np.array([(r[0] + r[2]) / 2, (r[1] + r[3]) / 2, np.log(s), (win[0] + win[2]) / 2, (win[1] + win[3]) / 2])

    def camera(self, t):
        tl = self.tl
        push = self.c['camera'].get('push', 0.04)
        move = self.c['camera'].get('move_secs', 1.1)
        lead = self.c['camera'].get('lead_secs', 0.55)  # 镜头在开口前多久开始移动

        def held(st, a, b, tt):
            p = vk.ease((tt - a) / max(b - a, 1e-6))
            out = st.copy()
            out[2] += np.log(1 + push * p)
            return out

        if t < tl['intro_end'] - 0.0:
            return held(self.full_state, 0, tl['intro_end'], t)
        sents = tl['sents']
        # 从全幅移到第一句
        prev = held(self.full_state, 0, tl['intro_end'], tl['intro_end'])
        for k, s in enumerate(sents):
            m0 = s['start'] - lead
            m1 = m0 + move
            hold_end = sents[k + 1]['start'] - lead if k + 1 < len(sents) else tl['last_end']
            if t < m0:
                return prev
            if t < m1:
                p = vk.ease_io_cubic((t - m0) / move)
                return prev + (self.states[k] - prev) * p
            if t < hold_end:
                return held(self.states[k], m1, hold_end, t)
            prev = held(self.states[k], m1, hold_end, hold_end)
        pb = self.c['timing'].get('pull_back_secs', 2.0)
        p = vk.ease_io_cubic((t - tl['last_end']) / pb)
        return prev + (self.full_state - prev) * p

    def spotlight_rect(self, t):
        """当前高亮区域（帧坐标），与镜头同样插值"""
        st = self.camera(t)
        return st

    def warp(self, st):
        cx, cy, ls, fx, fy = st
        s = np.exp(ls)
        M = np.array([[s, 0, fx - s * cx], [0, s, fy - s * cy]], np.float32)
        fr = cv2.warpAffine(self.paper, M, (self.W, self.H), flags=cv2.INTER_CUBIC,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=tuple(int(v * 255) for v in self.bg))
        return fr.astype(np.float32) / 255, M

    def region_weight(self, t):
        """返回每句区域的高亮权重（用于 spotlight 插值）"""
        return None

    def spotlight(self, fr, t, M):
        amt = self.c['camera'].get('spotlight', 0.22)
        tl = self.tl
        if amt <= 0 or t < tl['intro_end'] - 0.6 or t > tl['last_end'] + 0.3:
            fade = 0.0
        else:
            fade = min(1.0, (t - (tl['intro_end'] - 0.6)) / 0.8, max(0.0, (tl['last_end'] + 0.3 - t) / 0.3))
        if fade <= 0:
            return fr
        # 当前插值区域：取镜头插值的区域（用窗口本身即可，因为区域始终被放进 focus_window）
        x0, y0, x1, y1 = self.win
        pad = 26
        feather = 70.0
        xs = np.arange(self.W, dtype=np.float32)
        ys = np.arange(self.H, dtype=np.float32)
        # 当前区域在帧内的实际宽度（随镜头插值）
        st = self.camera(t)
        s = np.exp(st[2])
        k = self._current_k(t)
        r = self.reg(self.tl['sents'][k]['region'])
        rx0 = s * (r[0] - st[0]) + st[3] - pad
        rx1 = s * (r[2] - st[0]) + st[3] + pad
        mx = np.clip(np.minimum(xs - rx0, rx1 - xs) / feather + 0.5, 0, 1)
        mx = mx * mx * (3 - 2 * mx)
        mask = 1 - amt * fade * (1 - mx)[None, :, None]
        bg = self.bg[None, None, :]
        return bg + (fr - bg) * mask  # 只压暗纸面，背景色不变

    def _current_k(self, t):
        lead = self.c['camera'].get('lead_secs', 0.55)
        move = self.c['camera'].get('move_secs', 1.1)
        k = 0
        for i, s in enumerate(self.tl['sents']):
            if t >= s['start'] - lead + move * 0.5:
                k = i
        return k

    # ---- 文字层 ----
    def text_layer(self, key, draw_fn, size):
        if key not in self._card_cache:
            im = Image.new('RGBA', size, (0, 0, 0, 0))
            draw_fn(ImageDraw.Draw(im), im)
            self._card_cache[key] = np.asarray(im).astype(np.float32) / 255
        return self._card_cache[key]

    @staticmethod
    def comp(fr, layer, x, y, alpha=1.0):
        h, w = layer.shape[:2]
        H, W = fr.shape[:2]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(W, x + w), min(H, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        L = layer[y0 - y:y1 - y, x0 - x:x1 - x]
        a = L[..., 3:4] * alpha
        fr[y0:y1, x0:x1] = fr[y0:y1, x0:x1] * (1 - a) + L[..., :3] * a

    def subtitle(self, fr, t):
        L = self.c['layout']
        S = L['subtitle']
        col_on = np.array([int(S['color'][i:i + 2], 16) for i in (1, 3, 5)]) / 255
        for s in self.tl['sents']:
            nxt = s['i'] + 1
            t_out = (self.tl['sents'][nxt]['start'] - 0.35) if nxt < len(self.tl['sents']) else self.tl['last_end'] + 0.6
            t_in = s['start'] - 0.18
            if not (t_in <= t < t_out + 0.3):
                continue
            a = min(1.0, (t - t_in) / 0.22, max(0.0, (t_out + 0.3 - t) / 0.3))
            key = ('sub', s['i'])
            if key not in self._sub_cache:
                f = vk.font(self.font_path, S['size'])
                txt = s['subtitle']
                tw = int(f.getlength(txt))
                w, h = tw + 40, S['size'] + 60
                base = Image.new('RGBA', (w, h), (0, 0, 0, 0))
                d = ImageDraw.Draw(base)
                d.text((20, 20), txt, font=f, fill=tuple(int(v * 255) for v in col_on) + (255,),
                       stroke_width=S.get('stroke', 2), stroke_fill=(20, 16, 12, 200))
                # 每个字的 x 区间（用于逐字提亮）
                xs, x = [], 20
                for ch in txt:
                    cw = f.getlength(ch)
                    if ch not in PUNCT:
                        xs.append((x, x + cw))
                    x += cw
                self._sub_cache[key] = (np.asarray(base).astype(np.float32) / 255, xs)
            layer, xs = self._sub_cache[key]
            lay = layer.copy()
            # 逐字提亮：未读到的字用 dim 透明度
            dim = S.get('dim_alpha', 0.45)
            colmask = np.full(lay.shape[1], dim, np.float32)
            colmask[:20] = 1
            for (x0, x1), (ch, ta, tb) in zip(xs, s['chars']):
                p = vk.ease((t - ta) / 0.18)
                colmask[int(x0):int(x1) + 1] = dim + (1 - dim) * p
            colmask[int(xs[-1][1]) + 1:] = colmask[int(xs[-1][1])] if xs else 1
            lay[..., 3] *= colmask[None, :]
            cx = S['center_x']
            self.comp(fr, lay, int(cx - lay.shape[1] / 2), int(S['center_y'] - lay.shape[0] / 2), a)

    def title_card(self, fr, t):
        T = self.c['layout']['title']
        tl = self.tl
        a = min(1.0, t / 0.6, max(0.0, (tl['title_end'] + 0.4 - t) / 0.7))
        if a <= 0:
            return fr
        # 压暗背景
        fr *= (1 - self.c['layout']['title'].get('dim', 0.72) * a)

        def draw(d, im):
            f1 = vk.font(self.font_path, T['size'])
            f2 = vk.font(self.font_path, T['sub_size'])
            w = im.size[0]
            t1, t2 = self.c['title'], self.c.get('title_sub', '')
            d.text((w / 2, 110), t1, font=f1, fill=(240, 232, 214, 255), anchor='mm')
            d.line([(w / 2 - 70, 200), (w / 2 + 70, 200)], fill=(176, 58, 46, 255), width=3)
            if t2:
                d.text((w / 2, 260), t2, font=f2, fill=(214, 204, 186, 255), anchor='mm')
        lay = self.text_layer('title', draw, (900, 340))
        y_off = int(14 * (1 - vk.ease(t / 0.9)))
        self.comp(fr, lay, T['center_x'] - 450, T['center_y'] - 170 + y_off, a)
        return fr

    def end_card(self, fr, t):
        T = self.c['layout']['end']
        tl = self.tl
        a = min(1.0, max(0.0, (t - tl['end_card_start']) / 0.8))
        if a <= 0:
            return fr
        fr *= (1 - self.c['layout']['end'].get('dim', 0.45) * a)

        def draw(d, im):
            f1 = vk.font(self.font_path, T['size'])
            w = im.size[0]
            d.text((w / 2, 70), self.c['end_card_text'].format(author=self.c['author']), font=f1,
                   fill=(240, 232, 214, 255), anchor='mm')
            if self.c.get('end_card_note'):
                f2 = vk.font(self.font_path, T['note_size'])
                d.text((w / 2, 150), self.c['end_card_note'], font=f2, fill=(200, 190, 172, 255), anchor='mm')
        lay = self.text_layer('end', draw, (840, 200))
        self.comp(fr, lay, T['center_x'] - 420, T['center_y'] - 100, a)
        return fr

    def frame(self, t):
        st = self.camera(t)
        fr, M = self.warp(st)
        fr = self.spotlight(fr, t, M)
        fr = self.title_card(fr, t)
        self.subtitle(fr, t)
        fr = self.end_card(fr, t)
        # 收尾淡出最后 0.5 秒的 20%，便于循环
        return (np.clip(fr, 0, 1) * 255 + 0.5).astype(np.uint8)


def render_video(c, tl, audio_wav, out_mp4, fps, preview=None):
    R = Renderer(c, tl)
    nfr = round(tl['total'] * fps) if not preview else round(preview * fps)
    tmp = out_mp4.with_suffix('.tmp.mp4')
    cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f"{R.W}x{R.H}", '-r', str(fps),
           '-i', '-', '-i', str(audio_wav), '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-preset', 'medium',
           '-crf', '18', '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-movflags', '+faststart', '-c:a', 'aac',
           '-b:a', '192k', '-ar', str(SR), '-t', f'{nfr / fps:.6f}', str(tmp)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(nfr):
        p.stdin.write(R.frame(i / fps).tobytes())
        if i % 150 == 0:
            print(f'frame {i}/{nfr}', flush=True)
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError('ffmpeg failed')
    if out_mp4.exists():
        bak = out_mp4.with_name(out_mp4.stem + f".bak-{datetime.datetime.now():%Y%m%d-%H%M%S}.mp4")
        out_mp4.rename(bak)
    tmp.rename(out_mp4)
    return R


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    ap.add_argument('--out-dir', help='项目目录（默认 config 所在目录的上一级，若 config 在 源码/ 里）')
    ap.add_argument('--skip-tts', action='store_true', help='已有 TTS 缓存时不联网')
    ap.add_argument('--preview', type=float, help='只渲前 N 秒（快速预览）')
    a = ap.parse_args()
    c = load_cfg(a.config)
    proj = Path(a.out_dir).resolve() if a.out_dir else (c['_dir'].parent if c['_dir'].name == '源码' else c['_dir'])
    fps = c['layout'].get('fps', 30)
    name = c.get('output_name', 'langdu')
    tts_dir = proj / '素材' / 'tts'
    meta = run_tts(c, tts_dir, a.skip_tts)
    tl = build_timeline(c, meta)
    write_timeline_csv(tl, proj / 'timeline.csv', fps)
    (proj / '源码' / 'timeline_resolved.json').write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"total {tl['total']:.2f}s")
    aud = proj / '素材' / 'mix.wav'
    build_audio(c, tl, meta, tts_dir, aud)
    out = proj / '成片' / f'{name}_{c["layout"]["width"]}x{c["layout"]["height"]}.mp4'
    out.parent.mkdir(exist_ok=True)
    render_video(c, tl, aud, out, fps, a.preview)
    rep = vk.ffprobe_report(str(out))
    chk = proj / '检查记录' / f'{datetime.datetime.now():%Y%m%d-%H%M}'
    chk.mkdir(parents=True, exist_ok=True)
    (chk / 'ffprobe.json').write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding='utf-8')
    times = [1.8, (tl['title_end'] + tl['intro_end']) / 2]
    labels = ['片头 %.1fs' % times[0], '全幅 %.1fs' % times[1]]
    for s in tl['sents']:
        tt = min(s['end'] - 0.1, s['start'] + 0.75 * (s['end'] - s['start']) + 0.2)
        times.append(tt)
        labels.append(f"S{s['i'] + 1:02d} {tt:.1f}s {s['subtitle'][:6]}")
    times.append(tl['total'] - 1.0)
    labels.append('片尾 %.1fs' % times[-1])
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
