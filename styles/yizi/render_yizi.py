#!/usr/bin/env python3
"""「一字一讲」：单字特写（作品裁切）+ 字形演变图片序列（甲骨/金文/篆/隶/楷/行草等，需提供真实图片并记录许可）
+ 讲解字幕与 TTS（复用朗读版的 edge-tts/字边界/闪避混音）。
用法：python3 render_yizi.py --config config.json [--out-dir 项目] [--skip-tts] [--preview 秒]
每段讲解 lines[i] 可指定 show: "work"（作品特写）或 "evo:<序号>"（第几张演变图），画面随朗读切换。
"""
import sys, json, argparse, datetime, subprocess
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # calligraphy-studio 根目录
sys.path.insert(0, str(REPO / 'common')); sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / 'styles' / 'langdu'))
import render_langdu as L  # noqa
import videokit as vk  # noqa
import geometry as geom  # noqa


def hexrgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255


def fit_img(im, box):
    s = min(box / im.width, box / im.height)
    return im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)


def rgba(a):
    """舞台图是 RGB，而朗读版 Renderer.comp 需要 RGBA（带 alpha 通道）。"""
    return np.dstack([a, np.ones(a.shape[:2], np.float32)])


class Yizi:
    def __init__(self, c, tl):
        self.c, self.tl = c, tl
        Lc = c['layout']; self.W, self.H = Lc['width'], Lc['height']; self.bg = hexrgb(Lc['bg_color'])
        geo = c.get('geometry')
        img, _ = geom.load_work_image(L.rel(c, c['image']), geo, L.rel(c, geo['cache']) if geo and geo.get('cache') else None)
        S = Lc['stage']
        w = vk.grade(img, c['char_box'], **c.get('grade', {}))
        self.work = rgba(np.asarray(fit_img(Image.fromarray((w * 255).astype(np.uint8)), S['size'])).astype(np.float32) / 255)
        self.evo = []
        for e in c.get('evolution', []):
            im = Image.open(L.rel(c, e['image'])).convert('RGB')
            if e.get('crop'):
                im = im.crop(tuple(e['crop']))
            self.evo.append((rgba(np.asarray(fit_img(im, S['size'])).astype(np.float32) / 255), e['label']))
        self.cache = {}

    def show_for(self, t):
        cur = 'work'
        for s, ln in zip(self.tl['sents'], self.c['sentences']):
            if t >= s['start'] - 0.4:
                cur = ln.get('show', 'work')
        if t >= self.tl['last_end']:
            cur = 'work'
        return cur

    def stage_img(self, key):
        if key == 'work':
            return self.work, self.c.get('work_label', '本作')
        i = int(key.split(':')[1]); return self.evo[i]

    def label(self, text, size):
        k = (text, size)
        if k not in self.cache:
            im = Image.new('RGBA', (900, int(size * 1.8)), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((450, size * 0.9), text, font=vk.font(self.c.get('font'), size), fill=(236, 226, 206, 255), anchor='mm')
            self.cache[k] = np.asarray(im).astype(np.float32) / 255
        return self.cache[k]

    def frame(self, t):
        Lc = self.c['layout']; S = Lc['stage']
        fr = np.ones((self.H, self.W, 3), np.float32) * self.bg
        # 当前与上一个画面交叉淡化 0.5 秒
        key = self.show_for(t); prev = self.show_for(t - 0.5)
        for k, a in ([(prev, 1.0)] if prev == key else [(prev, 1 - vk.ease(self._since_switch(t))), (key, vk.ease(self._since_switch(t)))]):
            im, lab = self.stage_img(k)
            h, w = im.shape[:2]
            x = int(S['center_x'] - w / 2); y = int(S['center_y'] - h / 2)
            L.Renderer.comp(fr, im, x, y, a)
            ll = self.label(lab, Lc['label_size'])
            L.Renderer.comp(fr, ll, S['center_x'] - 450, y + h + 10, a)
        # 片头大字 / 片尾署名
        tt = self.tl
        ta = min(1, t / 0.6, max(0, (tt['intro_end'] - t) / 0.5))
        if ta > 0:
            L.Renderer.comp(fr, self.label(self.c['title'], Lc['title_size']), Lc['title_x'] - 450, Lc['title_y'], ta)
        ea = min(1, max(0, (t - tt['end_card_start']) / 0.8))
        if ea > 0:
            L.Renderer.comp(fr, self.label(self.c['end_card_text'].format(author=self.c['author']), Lc['title_size'] - 16),
                            Lc['title_x'] - 450, Lc['end_y'], ea)
        return fr

    def _since_switch(self, t):
        cur = self.show_for(t); k = 0.0
        while k < 0.5 and self.show_for(t - k) == cur:
            k += 1 / 30
        return min(1.0, k / 0.5)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--config', required=True); ap.add_argument('--out-dir')
    ap.add_argument('--skip-tts', action='store_true'); ap.add_argument('--preview', type=float)
    a = ap.parse_args(); c = L.load_cfg(a.config)
    proj = Path(a.out_dir).resolve() if a.out_dir else (c['_dir'].parent if c['_dir'].name == '源码' else c['_dir'])
    fps = c['layout'].get('fps', 30)
    for s in c['sentences']:
        s.setdefault('region', c['char_box'])  # build_timeline 需要
    meta = L.run_tts(c, proj / '素材' / 'tts', a.skip_tts)
    tl = L.build_timeline(c, meta); L.write_timeline_csv(tl, proj / 'timeline.csv', fps)
    aud = proj / '素材' / 'mix.wav'; L.build_audio(c, tl, meta, proj / '素材' / 'tts', aud)
    Y = Yizi(c, tl)
    sub = L.Renderer.__new__(L.Renderer)  # 只借用字幕绘制
    sub.c, sub.tl, sub._sub_cache, sub.font_path = c, tl, {}, c.get('font')
    out = proj / '成片' / f"{c.get('output_name', 'yizi')}_{Y.W}x{Y.H}.mp4"; out.parent.mkdir(exist_ok=True)
    nfr = round((a.preview or tl['total']) * fps); tmp = out.with_suffix('.tmp.mp4')
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{Y.W}x{Y.H}', '-r', str(fps), '-i', '-',
                          '-i', str(aud), '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p',
                          '-c:a', 'aac', '-b:a', '192k', '-t', f'{nfr / fps:.6f}', str(tmp)], stdin=subprocess.PIPE)
    for i in range(nfr):
        fr = Y.frame(i / fps); sub.subtitle(fr, i / fps)
        p.stdin.write((np.clip(fr, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())
    p.stdin.close(); p.wait()
    if out.exists():
        out.rename(out.with_name(out.stem + f'.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}.mp4'))
    tmp.rename(out)
    print(json.dumps(vk.ffprobe_report(str(out)), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
