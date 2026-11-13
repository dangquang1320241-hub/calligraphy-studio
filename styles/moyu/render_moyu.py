#!/usr/bin/env python3
"""墨色呼吸：微距式慢镜头扫过作品局部（纸纹、积墨、飞白），柔和的"呼吸"光，雨声 + 偶尔翻纸 + 稀疏古琴。
几乎无字：只有开场一句 + 落款片尾。

清晰度原则：放大倍数按"原照片像素"计（native），默认 2.2–2.8 倍，只做 Lanczos 放大 + 轻度锐化，
不做 AI 超分、不"补"细节。照片分辨率越高，可以把 max_magnification 调得越大。

用法：
  python render.py --config config.json --out 墨色呼吸.mp4 [--check-dir 检查记录/墨色呼吸] [--preview]
  python render.py --config config.json --detect-only   # 只自动找焦点，写 focus.auto.json + 叠框图
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent  # calligraphy-studio 根目录
COMMON = REPO / "common"
sys.path.insert(0, str(COMMON))
sys.path.insert(0, str(REPO))

import inkseg  # noqa: E402
from ambient import fade_edges, master, paper_rustle, place, rain  # noqa: E402
from cards import compose_end_card  # noqa: E402
from encode import render_frames_to_mp4  # noqa: E402
from media import load_config, preprocess_artwork, resolve_path  # noqa: E402
from music_synth import synth  # noqa: E402
from render_util import blit_center, parallel_frames, text_rgba, verify  # noqa: E402

W, H = 1080, 1920


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def sharpen(img, amount=0.55, sigma=1.4):
    f = img.astype(np.float32)
    return np.clip(f + (f - cv2.GaussianBlur(f, (0, 0), sigma)) * amount, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------ auto focus
def detect_focus(rgb, n=5, win_h_frac=0.6, aspect=W / H):
    """在作品上自动挑 n 个"值得凑近看"的窗口：把作品从右到左分成 n 条，每条里找一个最好的点，
    飞白（墨/纸交错的中间调）与积墨（大块浓墨）交替。返回 [{center:[x,y], kind, score}]（归一化）。"""
    h, w = rgb.shape[:2]
    s = 900 / max(h, w)
    small = cv2.resize(rgb, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    soft, binary = inkseg.ink_masks(small)
    sh, sw = soft.shape
    wh = max(9, int(sh * win_h_frac * 0.55)); ww = max(9, int(wh * aspect))
    k = (ww | 1, wh | 1)
    near = cv2.dilate(binary, np.ones((7, 7), np.uint8)).astype(np.float32)
    mid = (soft * (1 - soft) * 4) * near
    hf = np.abs(soft - cv2.GaussianBlur(soft, (0, 0), 2)) * near
    dry = cv2.blur(mid + hf * 2, k)
    heavy = cv2.blur((soft > 0.85).astype(np.float32), k)
    xs = np.where(binary.any(0))[0]
    xlo, xhi = (xs.min(), xs.max()) if xs.size else (0, sw - 1)
    edges = np.linspace(xhi, xlo, n + 1)
    out = []
    for i in range(n):
        m = (dry if i % 2 == 0 else heavy).copy()
        a0, a1 = int(edges[i + 1]), int(edges[i]) + 1
        mask = np.zeros_like(m, bool); mask[:, a0:a1] = True
        m[~mask] = -1
        # 和已选镜头别重叠太多（横向至少差 0.75 个取景宽，或纵向差半个取景高）
        yy, xx = np.mgrid[0:sh, 0:sw]
        for o in out:
            ox, oy = o["center"][0] * sw, o["center"][1] * sh
            m[(np.abs(xx - ox) < ww * 1.35) & (np.abs(yy - oy) < wh * 0.9)] = -1
        if m.max() < 0:
            m = (dry if i % 2 == 0 else heavy).copy(); m[~mask] = -1
        y, x = np.unravel_index(int(np.argmax(m)), m.shape)
        out.append({"center": [round(x / sw, 4), round(y / sh, 4)], "kind": "飞白" if i % 2 == 0 else "积墨",
                    "score": round(float(m[y, x]), 4)})
    return out


def draw_focus_overlay(rgb, shots, native_px_per_img_px, out_path):
    im = Image.fromarray(rgb).convert("RGB")
    d = ImageDraw.Draw(im)
    for i, sh in enumerate(shots):
        for key, col in (("rect_from", (220, 40, 40)), ("rect_to", (40, 90, 220))):
            x0, y0, x1, y1 = sh[key]
            d.rectangle([x0, y0, x1, y1], outline=col, width=4)
        d.text((sh["rect_from"][0] + 8, sh["rect_from"][1] + 6), f"{i + 1} {sh.get('kind', '')}", fill=(200, 0, 0),
               font=__import__("render_util").wk(max(28, rgb.shape[1] // 50)))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    im.resize((im.width // 2, im.height // 2)).save(out_path, quality=88)


class Job:
    def __init__(self, cfg):
        self.cfg = cfg
        self.fps = int(cfg.get("fps", 30))
        tm = cfg.get("timing", {})
        self.shot_s = float(tm.get("shot_seconds", 5.8))
        self.xfade = float(tm.get("crossfade", 1.4))
        self.end_s = float(tm.get("end_card_seconds", 5.0))
        self.open_text_s = float(tm.get("opening_text_seconds", 3.8))
        self.breath = float(tm.get("breath_period", 6.4))
        cleaned = resolve_path(cfg, cfg.get("cleaned"))
        if cfg.get("geometry"):
            from artwork_prep import prepare_cleaned
            cleaned = prepare_cleaned(cfg)
        if cleaned is None or not cleaned.exists():
            cleaned = Path(cfg["_config_dir"]) / "作品_clean.png"
            preprocess_artwork(resolve_path(cfg, cfg["artwork"]), cleaned, scale=2.0)
        self.img = np.asarray(Image.open(cleaned).convert("RGB"))
        ih, iw = self.img.shape[:2]
        # 原照片里纸的像素宽（native）。cleaned 是 scale 倍放大的。
        self.native_scale = float(cfg.get("cleaned_scale", 2.0))
        self.mag_max = float(cfg.get("max_magnification", 2.8))
        self.mag_min = float(cfg.get("min_magnification", 2.1))
        fp = cfg.get("focus_points", "auto")
        if fp == "auto" or not fp:
            crop_h = H / ((self.mag_min + self.mag_max) / 2) * self.native_scale
            fp = detect_focus(self.img, n=int(cfg.get("n_shots", 5)), win_h_frac=crop_h / ih)
            Path(cfg["_config_dir"], "focus.auto.json").write_text(json.dumps(fp, ensure_ascii=False, indent=1), "utf-8")
        self.shots = []
        for i, f in enumerate(fp):
            mag0 = float(f.get("mag_from", self.mag_min)); mag1 = float(f.get("mag_to", (self.mag_min + self.mag_max) / 2))
            mag0, mag1 = [min(self.mag_max, max(1.0, m)) for m in (mag0, mag1)]
            cx, cy = f["center"]
            drift = f.get("drift", [0.0, 0.06 if i % 2 == 0 else -0.06])
            self.shots.append({**f, "r0": self._rect(cx - drift[0] / 2, cy - drift[1] / 2, mag0),
                               "r1": self._rect(cx + drift[0] / 2, cy + drift[1] / 2, mag1)})
            self.shots[-1]["rect_from"] = self.shots[-1]["r0"]; self.shots[-1]["rect_to"] = self.shots[-1]["r1"]
        n = len(self.shots)
        self.t0 = [i * self.shot_s for i in range(n)]
        self.end_start = n * self.shot_s
        self.duration = self.end_start + self.end_s
        self.n_frames = int(round(self.duration * self.fps))
        # 片尾：整幅作品
        tw = 1000; th = int(ih * tw / iw)
        self.full_small = cv2.resize(self.img, (tw, th), interpolation=cv2.INTER_AREA)
        self.open_im = text_rgba(cfg.get("opening_line", "墨落纸上，自有呼吸"), 60, (250, 244, 232), tracking=10)
        self.open_sub = text_rgba(cfg["title"], 34, (236, 224, 200), tracking=6)
        vy, vx = np.ogrid[0:H, 0:W]
        r = np.sqrt(((vx - W / 2) / (W * 0.75)) ** 2 + ((vy - H / 2) / (H * 0.62)) ** 2)
        self.vig = (1 - 0.38 * np.clip(r, 0, 1.2) ** 2).astype(np.float32)
        self.vy, self.vx = vy, vx

    def _rect(self, cx, cy, mag):
        ih, iw = self.img.shape[:2]
        cw = W / mag * self.native_scale; ch = H / mag * self.native_scale
        cw, ch = min(cw, iw), min(ch, ih)
        x0 = float(np.clip(cx * iw - cw / 2, 0, iw - cw)); y0 = float(np.clip(cy * ih - ch / 2, 0, ih - ch))
        return [x0, y0, x0 + cw, y0 + ch]

    def shot_view(self, k, u):
        r0, r1 = self.shots[k]["r0"], self.shots[k]["r1"]
        e = u  # 匀速漂移最安静；起止靠叠化柔化
        x0, y0, x1, y1 = [a + (b - a) * e for a, b in zip(r0, r1)]
        sx = W / (x1 - x0); sy = H / (y1 - y0)
        M = np.float32([[sx, 0, -x0 * sx], [0, sy, -y0 * sy]])
        v = cv2.warpAffine(self.img, M, (W, H), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
        return sharpen(v, float(self.cfg.get("sharpen", 0.55)))

    def light(self, t):
        """呼吸光：整体亮度 ±3.5% 的慢正弦 + 一团缓慢移动的暖光。"""
        b = 1.0 + 0.035 * np.sin(2 * np.pi * t / self.breath - np.pi / 2)
        lx = W * (0.5 + 0.22 * np.sin(2 * np.pi * t / 23.0))
        ly = H * (0.42 + 0.12 * np.sin(2 * np.pi * t / 17.0 + 1.0))
        g = np.exp(-(((self.vx - lx) / (W * 0.55)) ** 2 + ((self.vy - ly) / (H * 0.35)) ** 2)).astype(np.float32)
        return self.vig * b, g * (0.06 + 0.03 * np.sin(2 * np.pi * t / self.breath))

    def frame(self, fi):
        t = fi / self.fps
        n = len(self.shots)
        span = self.shot_s + self.xfade
        if t < self.end_start + self.xfade:
            k = min(n - 1, int(t // self.shot_s))
            lt = t - self.t0[k]
            cur = self.shot_view(k, min(1.0, (lt + (self.xfade if k else 0)) / (span + self.xfade))).astype(np.float32)
            if lt > self.shot_s - self.xfade:
                a = ease((lt - (self.shot_s - self.xfade)) / self.xfade)
                if k + 1 < n:
                    nxt = self.shot_view(k + 1, (lt - self.shot_s + self.xfade) / (span + self.xfade)).astype(np.float32)
                else:
                    nxt = self.end_base(t).astype(np.float32)
                # "呼吸式"转场：先缓缓暗下去（呼），在最暗处换镜，再亮起来（吸），不叠双影
                dip = float(self.cfg.get("dip_depth", 0.88))
                cur = (cur if a < 0.5 else nxt) * (1 - dip * np.sin(np.pi * a))
            if t < 0.8:
                cur = cur * ease(t / 0.8)
        else:
            cur = self.end_base(t).astype(np.float32)
        mul, glow = self.light(t)
        cur = cur * mul[..., None] + glow[..., None] * np.array([255, 236, 200], np.float32)
        im = Image.fromarray(np.clip(cur, 0, 255).astype(np.uint8)).convert("RGBA")
        # 开场一句
        if t < self.open_text_s + 0.8:
            op = ease((t - 0.5) / 0.6) * (1 - ease((t - self.open_text_s) / 0.8))
            if op > 0:
                im.alpha_composite(self._band(op), (0, 520))
                blit_center(im, self.open_im, W / 2, 680, op)
                blit_center(im, self.open_sub, W / 2, 770, op * 0.9)
        if t >= self.end_start:
            e = ease((t - self.end_start - 0.6) / 1.0)
            im = compose_end_card(im, self.cfg["title"], self.cfg.get("subtitle", ""), self.cfg.get("author", "作者名 · 书"),
                                  self.cfg.get("author_note", "落款占位，可替换"), self.cfg.get("seal_char", "书"), e)
        return im.convert("RGB").tobytes()

    def _band(self, op):
        """开场字下的柔边暗带（上下羽化，避免硬边）。"""
        y = np.linspace(-1, 1, 380, dtype=np.float32)
        a = np.exp(-(y / 0.55) ** 4) * 150 * op
        arr = np.zeros((380, W, 4), np.uint8); arr[..., :3] = (10, 8, 6)
        arr[..., 3] = a[:, None].astype(np.uint8)
        return Image.fromarray(arr, "RGBA")

    def end_base(self, t):
        bg = np.zeros((H, W, 3), np.float32); bg[:] = (20, 16, 14)
        th, tw = self.full_small.shape[:2]
        y0 = int(self.cfg.get("end_paper_top_px", 330)); x0 = (W - tw) // 2
        bg[y0:y0 + th, x0:x0 + tw] = self.full_small
        return bg.astype(np.uint8)

    def audio(self, out_wav):
        sr = 48000
        a = self.cfg.get("audio", {})
        d = self.duration
        music = synth(d, sr=sr, seed=int(a.get("seed", 47)), root=float(a.get("root", 146.83)),
                      bpm=float(a.get("bpm", 44)), density=float(a.get("density", 0.22)),
                      xiao=float(a.get("xiao", 0.35)), accents=[self.end_start + 0.6], accent_gain=0.45,
                      fade_in=3.0, fade_out=4.0)
        rn = fade_edges(rain(d + 0.5, sr, seed=int(a.get("seed", 47)), intensity=float(a.get("rain_intensity", 0.6)))[: len(music)],
                        sr, 2.5, 3.5)
        rus = np.zeros_like(music)
        for i, t0 in enumerate(self.t0[1:] + [self.end_start]):
            place(rus, paper_rustle(1.0, sr, seed=200 + i, gain=0.6), max(0.0, t0 - 0.9), sr)
        layers = [(music, 0.0), (rn, float(a.get("rain_db", -7.0))), (rus, float(a.get("rustle_db", -13.0)))]
        master(layers, out_wav, sr, target_lufs=float(self.cfg.get("loudness_lufs", -17)))
        return [round(x - 0.9, 3) for x in self.t0[1:] + [self.end_start]]

    def write_timeline(self, path, rustles):
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["kind", "t_start", "t_end", "label"])
            w.writerow(["text", 0.5, self.open_text_s + 0.8, self.cfg.get("opening_line", "")])
            for k, s in enumerate(self.shots):
                w.writerow(["shot", f"{self.t0[k]:.3f}", f"{self.t0[k] + self.shot_s + self.xfade:.3f}",
                            f"{s.get('kind', '')} @ {s['center']}"])
            for r in rustles:
                w.writerow(["sfx", f"{r:.3f}", "", "翻纸声"])
            w.writerow(["end_card", f"{self.end_start:.3f}", f"{self.duration:.3f}", "整幅 + 落款"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", default="墨色呼吸.mp4")
    ap.add_argument("--timeline", default="")
    ap.add_argument("--check-dir", default="")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--detect-only", action="store_true")
    ap.add_argument("--workers", type=int, default=0)
    a = ap.parse_args()
    cfg = load_config(Path(a.config))
    job = Job(cfg)
    cd = Path(a.check_dir or Path(a.out).parent)
    draw_focus_overlay(job.img, job.shots, job.native_scale, cd / "focus_overlay.jpg")
    print(f"时长 {job.duration:.2f}s，{len(job.shots)} 个镜头，帧数 {job.n_frames}；焦点叠框 → {cd / 'focus_overlay.jpg'}")
    if a.detect_only:
        print(json.dumps([{k: s[k] for k in ('center', 'kind') if k in s} for s in job.shots], ensure_ascii=False))
        return
    out = Path(a.out)
    if a.preview:
        d = cd / "preview"; d.mkdir(parents=True, exist_ok=True)
        for k in range(len(job.shots)):
            t = job.t0[k] + job.shot_s * 0.45
            Image.frombytes("RGB", (W, H), job.frame(int(t * job.fps))).save(d / f"shot{k + 1}.jpg", quality=90)
        Image.frombytes("RGB", (W, H), job.frame(int(2.0 * job.fps))).save(d / "open.jpg", quality=90)
        Image.frombytes("RGB", (W, H), job.frame(job.n_frames - 5)).save(d / "end.jpg", quality=90)
        print("预览 →", d)
        return
    wav = out.with_suffix(".music.wav")
    rus = job.audio(wav)
    if a.timeline:
        job.write_timeline(a.timeline, rus)
    render_frames_to_mp4(parallel_frames(job.frame, job.n_frames, workers=a.workers or None),
                         job.n_frames, job.fps, W, H, wav, out, duration=job.n_frames / job.fps)
    print("成片 →", out)
    if a.check_dir:
        ts = [2.0] + [job.t0[k] + job.shot_s * 0.45 for k in range(len(job.shots))] + \
             [job.t0[1] - job.xfade * 0.5, job.duration - 0.5]
        ts = sorted(ts)
        rep, sheet = verify(out, a.check_dir, ts, [f"{x:.1f}s" for x in ts])
        print(json.dumps({k: rep[k] for k in ("video", "format", "decode_ok", "loudness")}, ensure_ascii=False))
        print("联系表 →", sheet)


if __name__ == "__main__":
    main()
