#!/usr/bin/env python3
"""「诗意叠化」：复用朗读版（TTS/时间线/闪避混音/镜头逐句停列/逐字字幕）。
作品纸面始终在最上层、完全清晰；纸面以外的背景是一幅**代码生成**的低对比暗调水墨场景（远山、月、水波、孤舟、断崖），
每句换一幅，随镜头移动时叠化切换，并有轻微视差漂移。不使用 AI 图片；作品照片只裁切/调色/几何校正。
用法：python3 render_shiyi.py --config config.json [--out-dir 项目] [--skip-tts] [--preview 秒]
"""
import sys, json, argparse, datetime
from pathlib import Path
import numpy as np
import cv2
HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # calligraphy-studio 根目录
sys.path.insert(0, str(REPO / 'common'))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'styles' / 'langdu'))
import render_langdu as L  # noqa
import videokit as vk  # noqa
import geometry as geom  # noqa


def hexrgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255


def noise1d(n, seed, octaves=5, base=3):
    rng = np.random.default_rng(seed)
    out = np.zeros(n)
    for o in range(octaves):
        k = base * 2 ** o
        pts = rng.standard_normal(k + 2)
        out += np.interp(np.linspace(0, k, n), np.arange(k + 2), pts) / (1.9 ** o)
    return out / 1.6


def ink_scene(W, H, spec, seed, S):
    """暗调水墨：底色 S.bg，山/雾用略亮的墨灰（低对比），月用淡米色。返回 float32 (H,W,3)。"""
    bg = hexrgb(S.get('bg', '#1b1815'))
    mist = hexrgb(S.get('mist', '#4b453b'))
    moon_c = hexrgb(S.get('moon_color', '#cbbfa6'))
    img = np.ones((H, W, 3), np.float32) * bg
    yy = np.arange(H, dtype=np.float32)[:, None]
    xx = np.arange(W, dtype=np.float32)[None, :]
    # 天空上方一点微光
    glow = np.clip(1 - yy / (H * 0.6), 0, 1) * S.get('sky_glow', 0.10)
    img = img * (1 - glow[..., None]) + mist * glow[..., None]
    # 月
    if spec.get('moon', False):
        cx, cy = W * spec.get('moon_x', 0.70), H * spec.get('moon_y', 0.12)
        r = W * spec.get('moon_r', 0.05)
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        halo = np.clip(1 - (d - r) / (r * 3), 0, 1) ** 2 * 0.18
        disc = np.clip((r - d) / 2.0, 0, 1) * S.get('moon_alpha', 0.55)
        a = np.maximum(halo, disc)[..., None]
        img = img * (1 - a) + moon_c * a
    # 远山多层：越远越淡越高
    n = spec.get('mountains', 3)
    for k in range(n):
        base = H * (spec.get('mount_base', 0.55) + 0.10 * k)
        amp = H * spec.get('mount_amp', 0.10) * (1.15 - 0.2 * k)
        prof = base - amp * (0.6 + 0.6 * noise1d(W, seed * 7 + k, base=2 + k))
        depth = (0.35 + 0.25 * k) * spec.get('mount_strength', 1.0)
        edge = np.clip((yy - prof[None, :]) / (6 + 4 * k), 0, 1)
        fade = np.clip(1 - (yy - prof[None, :]) / (H * 0.18), 0.0, 1)  # 山脚化入雾（留白）
        a = (edge * (0.35 + 0.65 * fade) * depth)[..., None]
        col = mist * (0.75 + 0.1 * k)
        img = img * (1 - a) + col * a
    # 断崖（左侧或右侧）
    if spec.get('cliff'):
        side = spec.get('cliff_side', 'left')
        edge = W * spec.get('cliff_w', 0.22) + W * 0.04 * noise1d(H, seed + 99, base=6)
        dist = (edge[:, None] - xx) if side == 'left' else (xx - (W - edge[:, None]))
        a = np.clip(dist / 5, 0, 1)[..., None] * 0.75
        img = img * (1 - a) + mist * 0.85 * a
    # 水波
    if spec.get('water', False):
        wy = H * spec.get('water_y', 0.80)
        rng = np.random.default_rng(seed + 5)
        for i in range(int(spec.get('ripples', 18))):
            y = int(wy + (i ** 1.25) * H * 0.006)
            if y >= H - 2:
                break
            x0 = int(rng.random() * W * 0.7)
            ln = int(W * (0.08 + 0.22 * rng.random()))
            img[y:y + 2, x0:x0 + ln] = img[y:y + 2, x0:x0 + ln] * 0.72 + mist * 0.28
    # 孤舟
    if spec.get('boat'):
        bx, by = int(W * spec.get('boat_x', 0.30)), int(H * spec.get('water_y', 0.80)) - 4
        hull = np.clip(1 - np.abs(np.arange(-40, 41)) / 41, 0, 1)
        for j, hgt in enumerate(hull):
            h = int(2 + 6 * hgt)
            img[by - h:by, bx + j - 40] = mist * 0.9
        img[by - 34:by - 6, bx - 2:bx] = mist * 0.9
    return np.clip(img, 0, 1).astype(np.float32)


class ShiyiRenderer(L.Renderer):
    def __init__(self, c, tl):
        super().__init__(c, tl)
        geo = c.get('geometry')
        img, _ = geom.load_work_image(L.rel(c, c['image']), geo, L.rel(c, geo['cache']) if geo and geo.get('cache') else None)
        self.pmask = (vk.paper_mask(img, c.get('paper_crop')) * 255).astype(np.uint8)
        S = c['scene']
        self.drift = S.get('drift_px', 60)
        self.scenes = [ink_scene(self.W + 2 * self.drift, self.H, s.get('scene', {}), S.get('seed', 3) + i, S)
                       for i, s in enumerate(c['sentences'])]
        self.scene_intro = ink_scene(self.W + 2 * self.drift, self.H, S.get('intro_scene', {'mountains': 3, 'moon': True}), S.get('seed', 3) + 50, S)
        # 字幕带压暗（保证字幕可读）
        Sub = c['layout']['subtitle']
        yy = np.arange(self.H, dtype=np.float32)
        band = np.exp(-((yy - Sub['center_y']) / S.get('subtitle_band', 110)) ** 2)
        self.sub_dim = (1 - S.get('subtitle_dim', 0.6) * band)[:, None, None].astype(np.float32)

    def scene_at(self, t):
        """当前背景：按镜头移动节奏叠化切换（与 camera 同一时间点）"""
        lead = self.c['camera'].get('lead_secs', 0.6)
        move = self.c['camera'].get('move_secs', 1.15)
        cur, prev, p = self.scene_intro, None, 1.0
        for k, s in enumerate(self.tl['sents']):
            m0 = s['start'] - lead
            if t >= m0:
                prev, cur = cur, self.scenes[k]
                p = vk.ease((t - m0) / move)
        if t >= self.tl['last_end']:
            prev, cur = cur, self.scene_intro
            p = vk.ease((t - self.tl['last_end']) / 2.0)
        off = int(self.drift + self.drift * np.sin(t / max(self.tl['total'], 1) * np.pi - np.pi / 2))
        sc = cur[:, off:off + self.W]
        if prev is not None and p < 1:
            sc = prev[:, off:off + self.W] * (1 - p) + sc * p
        bg = self.bg[None, None, :]
        return bg + (sc - bg) * self.sub_dim

    def frame(self, t):
        st = self.camera(t)
        fr, M = self.warp(st)
        fr = self.spotlight(fr, t, M)
        a = cv2.warpAffine(self.pmask, M, (self.W, self.H), flags=cv2.INTER_LINEAR, borderValue=0).astype(np.float32)[..., None] / 255
        fr = fr * a + self.scene_at(t) * (1 - a)
        fr = self.title_card(fr, t)
        self.subtitle(fr, t)
        fr = self.end_card(fr, t)
        return (np.clip(fr, 0, 1) * 255 + 0.5).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True); ap.add_argument('--out-dir')
    ap.add_argument('--skip-tts', action='store_true'); ap.add_argument('--preview', type=float)
    a = ap.parse_args()
    c = L.load_cfg(a.config)
    proj = Path(a.out_dir).resolve() if a.out_dir else (c['_dir'].parent if c['_dir'].name == '源码' else c['_dir'])
    fps = c['layout'].get('fps', 30)
    meta = L.run_tts(c, proj / '素材' / 'tts', a.skip_tts)
    tl = L.build_timeline(c, meta)
    L.write_timeline_csv(tl, proj / 'timeline.csv', fps)
    (proj / '源码' / 'timeline_resolved.json').write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding='utf-8')
    aud = proj / '素材' / 'mix.wav'
    L.build_audio(c, tl, meta, proj / '素材' / 'tts', aud)
    out = proj / '成片' / f"{c.get('output_name', 'shiyi')}_{c['layout']['width']}x{c['layout']['height']}.mp4"
    out.parent.mkdir(exist_ok=True)
    L.Renderer = ShiyiRenderer  # render_video 内部实例化 Renderer（只在本进程内替换）
    L.render_video(c, tl, aud, out, fps, a.preview)
    rep = vk.ffprobe_report(str(out))
    chk = proj / '检查记录' / f'{datetime.datetime.now():%Y%m%d-%H%M}'
    chk.mkdir(parents=True, exist_ok=True)
    (chk / 'ffprobe.json').write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding='utf-8')
    times = [1.8, (tl['title_end'] + tl['intro_end']) / 2]
    labels = [f'片头 {times[0]:.1f}s', f'全幅 {times[1]:.1f}s']
    for s in tl['sents']:
        tt = min(s['end'] - 0.1, s['start'] + 0.75 * (s['end'] - s['start']) + 0.2)
        times.append(tt); labels.append(f"S{s['i'] + 1:02d} {tt:.1f}s {s['subtitle'][:6]}")
    times.append(tl['total'] - 1.0); labels.append(f'片尾 {times[-1]:.1f}s')
    lim = a.preview or tl['total']
    keep = [(x, l) for x, l in zip(times, labels) if x < lim] or [(lim / 2, '预览')]
    fr = vk.grab_frames(str(out), [x for x, _ in keep], chk / 'frames')
    vk.contact_sheet(fr, [l for _, l in keep], chk / '联系表.jpg')
    print(json.dumps({'mp4': str(out), 'sheet': str(chk / '联系表.jpg'), 'report': rep}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
