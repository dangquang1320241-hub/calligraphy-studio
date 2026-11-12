#!/usr/bin/env python3
"""笔顺复现：墨迹沿骨架"写"出来——自右向左逐列、每列自上而下逐字。

只揭示照片里真实的墨像素（未揭示处显示从照片估出的空白纸色），最后一帧就是原作品。
笔顺是算法示意（沿骨架从左上端点生长），不是作者真实笔顺，画面左上角标「笔顺为示意」。

用法：
  python render.py --config config.json --out 笔顺复现.mp4 [--check-dir 检查记录/笔顺复现] [--preview 6]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
COMMON = REPO / "common"
sys.path.insert(0, str(COMMON))

import inkseg  # noqa: E402
from ambient import brush_swish, fade_edges, master, place  # noqa: E402
from cards import compose_end_card, compose_title_card  # noqa: E402
from encode import render_frames_to_mp4  # noqa: E402
from media import (BG, CREAM, GOLD, MUTED, make_background, make_paper_texture,  # noqa: E402
                   preprocess_artwork, resolve_path, sample_paper_color, load_config)
from music_synth import synth  # noqa: E402
from render_util import blit, blit_center, parallel_frames, tag_pill, text_rgba, verify  # noqa: E402

W, H = 1920 // 16 * 9, 1920  # 1080x1920


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * x * (x * (x * 6 - 15) + 10)


class Job:
    def __init__(self, cfg):
        self.cfg = cfg
        self.fps = int(cfg.get("fps", 30))
        d = cfg.get("timing", {})
        self.title_s = float(d.get("title_seconds", 2.6))
        self.lead_in = float(d.get("lead_in", 0.7))
        self.write_s = float(d.get("writing_seconds", 22.0))
        self.zoom_s = float(d.get("zoom_out_seconds", 2.2))
        self.end_s = float(d.get("end_card_seconds", 4.5))
        self.col_gap = float(d.get("column_gap", 0.45))
        self.char_gap = float(d.get("char_gap", 0.06))
        self.soft = float(d.get("edge_softness", 0.07))
        self.paper_h = int(cfg.get("paper_height_px", 1180))
        self.stage_top = int(cfg.get("stage_top_px", 150))
        self.prepare()

    # ------------------------------------------------------------ data
    def prepare(self):
        cfg = self.cfg
        cleaned = resolve_path(cfg, cfg.get("cleaned"))
        if cfg.get("geometry"):
            from artwork_prep import prepare_cleaned
            cleaned = prepare_cleaned(cfg)
        if cleaned is None or not cleaned.exists():
            art = resolve_path(cfg, cfg["artwork"])
            cleaned = Path(cfg["_config_dir"]) / "作品_clean.png"
            print("预处理照片 →", cleaned)
            preprocess_artwork(art, cleaned, scale=2.0)
        src = np.asarray(Image.open(cleaned).convert("RGB"))
        sh, sw = src.shape[:2]
        s = self.paper_h / sh
        self.base_w = int(round(sw * s))
        self.photo = cv2.resize(src, (self.base_w, self.paper_h), interpolation=cv2.INTER_AREA)
        texts = cfg.get("columns_text")
        lay_cfg = cfg.get("layout", "auto")
        cache = Path(cfg["_config_dir"]) / "layout.auto.json"
        det = src if max(src.shape[:2]) <= 1800 else cv2.resize(
            src, (int(sw * 1800 / max(sh, sw)), int(sh * 1800 / max(sh, sw))), interpolation=cv2.INTER_AREA)
        self.layout = inkseg.load_or_detect(lay_cfg, det, columns_text=texts,
                                            signature=bool(cfg.get("has_signature", True)),
                                            cache_path=cache, char_cuts=cfg.get("char_cuts"))
        soft, binary = inkseg.ink_masks(self.photo)
        labels = inkseg.char_label_map(binary, self.layout)
        self.chars = []
        for ci, col in enumerate(self.layout["columns"]):
            for j, ch in enumerate(col["chars"]):
                self.chars.append({"col": ci, "j": j, "text": ch.get("text", ""), "box": ch["box"]})
        n = len(self.chars)
        print(f"列 {len(self.layout['columns'])}，字 {n}，计算骨架与揭示顺序…", flush=True)
        rd = int(cfg.get("reveal_halo_px", 10))
        prog, region, char_of, weights = inkseg.reveal_progress_map(binary, labels, n, region_dilate=rd)
        dist = cv2.distanceTransform((1 - binary).astype(np.uint8), cv2.DIST_L2, 3)
        self.ramp = np.clip((dist - rd * 0.4) / (rd * 0.6), 0, 1).astype(np.float32)
        # ------- 时间分配：字时长 ∝ 骨架长度^0.6
        w = np.maximum(weights, 1.0) ** 0.6
        n_cols = len(self.layout["columns"])
        gaps = self.char_gap * (n - n_cols) + self.col_gap * (n_cols - 1)
        avail = max(3.0, self.write_s - gaps)
        dur = w / w.sum() * avail
        dur = np.clip(dur, 0.18, None)
        dur *= avail / dur.sum()
        t = self.title_s + self.lead_in
        self.write_start = t
        starts = np.zeros(n)
        for i, ch in enumerate(self.chars):
            if i > 0:
                t += self.col_gap if ch["col"] != self.chars[i - 1]["col"] else self.char_gap
            starts[i] = t
            t += dur[i]
        self.write_end = t
        self.starts, self.durs = starts, dur
        T = np.full(binary.shape, -1e9, np.float32)
        sel = region & (char_of >= 0)
        T[sel] = starts[char_of[sel]] + prog[sel] * dur[char_of[sel]]
        self.T = T
        self.region = sel
        # ------- 空白纸：从照片里去掉墨区域后归一化模糊估计（只用于"尚未写到"的位置）
        keep = (~cv2.dilate(sel.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)).astype(np.float32)
        ph = self.photo.astype(np.float32)
        blank = np.zeros_like(ph)
        filled = np.zeros(keep.shape, np.float32)
        for sg in (6, 18, 50, 140):  # 由细到粗，粗笔画内部也能补到纸色
            num = cv2.GaussianBlur(ph * keep[..., None], (0, 0), sg)
            den = cv2.GaussianBlur(keep, (0, 0), sg)
            est = num / np.maximum(den, 1e-6)[..., None]
            wgt = np.clip(den / 0.25, 0, 1) * (1 - filled)
            blank += est * wgt[..., None]
            filled += wgt
        blank /= np.maximum(filled, 1e-6)[..., None]
        # 补一点纸纹（来自照片本身的高频，做了平移，不含墨）
        hf = (ph - cv2.GaussianBlur(ph, (0, 0), 6.0)) * keep[..., None]
        gs = np.zeros_like(ph); gw = np.zeros(keep.shape, np.float32)
        for dy, dx in ((61, 97), (-83, 41), (29, -113), (-47, -71)):
            gs += np.roll(hf, (dy, dx), (0, 1)); gw += np.roll(keep, (dy, dx), (0, 1))
        grain = gs / np.maximum(gw, 1.0)[..., None] * np.clip(gw, 0, 1)[..., None]
        self.blank = np.clip(blank + grain, 0, 255).astype(np.float32)
        self.photo_f = ph
        # 每字中心（相机跟随）
        cxs = []
        for ch in self.chars:
            b = ch["box"]
            cxs.append((b[0] + b[2]) / 2 * self.base_w)
        self.char_cx = np.array(cxs)
        self.stroke_px = inkseg.stroke_width(binary)
        # ------- 整体时间轴
        self.zoom_start = self.write_end + 0.5
        self.zoom_end = self.zoom_start + self.zoom_s
        self.end_start = self.zoom_end + 0.2
        self.duration = float(cfg.get("duration") or (self.end_start + self.end_s))
        self.n_frames = int(round(self.duration * self.fps))
        self._cam = self._camera_track()
        # 静态层
        self.bg = make_background(src, W, H, dark=0.16)
        pc = sample_paper_color(src)
        self.title_bg = Image.fromarray(make_paper_texture(W, H, pc))
        from media import INK, font as nfont, text_image
        self.title_im = text_image(cfg["title"], nfont(82, bold=True), INK + (255,), tracking=4)
        self.sub_im = text_image(cfg.get("title_sub", "笔顺复现"), nfont(38), (92, 70, 48, 255), tracking=12)
        self.rule_im = Image.new("RGBA", (240, 3), (120, 92, 60, 230))
        self.tag = tag_pill(cfg.get("tag_text", "笔顺为示意"), 30)
        self.col_caps = []
        for ci, col in enumerate(self.layout["columns"]):
            txt = col.get("text") or ""
            on = [text_rgba(c, 66, CREAM) for c in txt]
            off = [text_rgba(c, 66, (110, 96, 80), shadow=False) for c in txt]
            self.col_caps.append((on, off))

    def _camera_track(self):
        """每帧相机中心 x（base 像素），跟随正在写的字，再按时间高斯平滑。"""
        fps = self.fps
        n = self.n_frames
        half = W / 2
        lo, hi = half, max(half, self.base_w - half)
        xs = np.zeros(n)
        for f in range(n):
            t = f / fps
            i = int(np.clip(np.searchsorted(self.starts, t) - 1, 0, len(self.starts) - 1))
            xs[f] = self.char_cx[i] if t >= self.starts[0] else self.char_cx[0]
        sig = 0.9 * fps
        k = np.exp(-np.arange(-3 * sig, 3 * sig + 1) ** 2 / (2 * sig * sig)); k /= k.sum()
        pad = len(k) // 2
        xs = np.convolve(np.pad(xs, pad, mode="edge"), k, mode="valid")[:n]
        return np.clip(xs, lo, hi)

    # ------------------------------------------------------------ frame
    def paper_at(self, t, x0=0, x1=None):
        x1 = self.base_w if x1 is None else x1
        T = self.T[:, x0:x1]
        A = np.clip((t - T) / self.soft, 0, 1)
        A = np.where(self.region[:, x0:x1], np.maximum(A, self.ramp[:, x0:x1]), 1.0)[..., None]
        out = self.blank[:, x0:x1] * (1 - A) + self.photo_f[:, x0:x1] * A
        # 笔锋处一点柔光（只是叠在上面的光，不改墨像素本身；写完即消失）
        lead = self.region[:, x0:x1] & (T <= t) & (T > t - 0.10)
        if lead.any() and t < self.write_end:
            ys, xs = np.nonzero(lead)
            cy, cx = ys.mean(), xs.mean()
            r = max(12.0, self.stroke_px * 2.4)
            hh, ww = T.shape
            yy, xx = np.ogrid[0:hh, 0:ww]
            g = np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * r * r)).astype(np.float32)
            out = out + g[..., None] * np.array([38, 30, 18], np.float32) * 0.55
        return np.clip(out, 0, 255).astype(np.uint8)

    def frame(self, fi):
        t = fi / self.fps
        img = Image.fromarray(self.bg).convert("RGBA")
        # ---- 片头：标题直接写在"还没落笔的那张纸"上，随后淡出，镜头不切
        im = self._scene(t, img)
        if t < self.title_s + 0.6:
            op = 1.0 - ease((t - self.title_s) / 0.6) if t > self.title_s else 1.0
            cy = self.stage_top + self.paper_h * 0.45
            blit_center(im, self.title_im, W / 2, cy, op)
            blit_center(im, self.rule_im, W / 2, cy + 70, op * 0.9)
            blit_center(im, self.sub_im, W / 2, cy + 125, op)
        return im.convert("RGB").tobytes()

    def _scene(self, t, img):
        fi = min(int(round(t * self.fps)), self.n_frames - 1)
        z = ease((t - self.zoom_start) / max(self.zoom_s, 1e-3))
        fit_w = int(self.cfg.get("end_paper_width_px", 1000))
        if z <= 0:
            cx = self._cam[fi]
            x0 = int(round(cx - W / 2))
            x0 = max(0, min(x0, max(0, self.base_w - W)))
            x1 = min(self.base_w, x0 + W)
            crop = self.paper_at(t, x0, x1)
            px = (W - crop.shape[1]) // 2 if self.base_w < W else 0
            img.paste(Image.fromarray(crop), (px, self.stage_top))
        else:
            full = self.paper_at(t)
            s_end = fit_w / self.base_w
            s = 1.0 + (s_end - 1.0) * z
            cx_start = self._cam[min(int(self.zoom_start * self.fps), self.n_frames - 1)]
            cx = cx_start + (self.base_w / 2 - cx_start) * z
            end_top = int(self.cfg.get("end_paper_top_px", 330))
            top = self.stage_top + (end_top - self.stage_top) * z
            sw_, sh_ = max(1, int(self.base_w * s)), max(1, int(self.paper_h * s))
            sm = cv2.resize(full, (sw_, sh_), interpolation=cv2.INTER_AREA)
            ox = W / 2 - cx * s
            pim = Image.fromarray(sm).convert("RGBA")
            # 软阴影
            sh = Image.new("RGBA", (sw_ + 60, sh_ + 60), (0, 0, 0, 0))
            from PIL import ImageDraw, ImageFilter
            ImageDraw.Draw(sh).rectangle((30, 40, sw_ + 30, sh_ + 40), fill=(0, 0, 0, int(110 * z)))
            sh = sh.filter(ImageFilter.GaussianBlur(16))
            img.alpha_composite(sh, (int(ox) - 30 if int(ox) - 30 >= 0 else 0, max(0, int(top) - 30)))
            img.paste(pim, (int(round(ox)), int(round(top))))
        # ---- 当前列字幕（写到哪个字亮到哪个字）
        if t < self.zoom_start + 0.4:
            i = int(np.clip(np.searchsorted(self.starts, t) - 1, 0, len(self.starts) - 1))
            col = self.chars[i]["col"]
            on, off = self.col_caps[col]
            if on:
                op = 1.0 - ease((t - self.zoom_start) / 0.4) if t > self.zoom_start else 1.0
                op *= ease((t - (self.write_start - 0.4)) / 0.4)
                gap = 10
                tw = sum(x.width for x in on) + gap * (len(on) - 1)
                x = (W - tw) / 2
                y = int(self.cfg.get("caption_y_px", 1430))
                for j, (a, b) in enumerate(zip(on, off)):
                    k = [c for c in range(len(self.chars)) if self.chars[c]["col"] == col and self.chars[c]["j"] == j]
                    done = bool(k) and t >= self.starts[k[0]]
                    blit(img, a if done else b, x, y - a.height / 2, op)
                    x += a.width + gap
        # ---- 角标
        if t > self.title_s:
            blit(img, self.tag, 40, 72, 0.95 * ease((t - self.title_s) / 0.5))
        # ---- 片尾
        if t >= self.end_start:
            op = ease((t - self.end_start) / 0.8)
            img = compose_end_card(img, self.cfg["title"], self.cfg.get("subtitle", ""),
                                   self.cfg.get("author", "作者名 · 书"),
                                   self.cfg.get("author_note", "落款占位，可替换"),
                                   self.cfg.get("seal_char", "书"), op)
        return img

    # ------------------------------------------------------------ audio
    def audio(self, out_wav):
        sr = 48000
        acc = sorted({round(self.starts[i], 3) for i in range(len(self.chars))
                      if i == 0 or self.chars[i]["col"] != self.chars[i - 1]["col"]})
        acc.append(round(self.end_start, 3))
        m = self.cfg.get("music", {})
        music = synth(self.duration, sr=sr, seed=int(m.get("seed", 21)), root=float(m.get("root", 146.83)),
                      bpm=float(m.get("bpm", 54)), density=float(m.get("density", 0.45)),
                      xiao=float(m.get("xiao", 0.7)), accents=acc, accent_gain=0.6)
        bus = np.zeros((len(music), 2), np.float32)
        for i in range(len(self.chars)):
            place(bus, brush_swish(float(self.durs[i]) * 0.9, sr, seed=100 + i), float(self.starts[i]), sr)
        layers = [(music, 0.0), (fade_edges(bus, sr, 0.2, 1.0), float(m.get("brush_db", -9.0)))]
        info = master(layers, out_wav, sr, target_lufs=float(self.cfg.get("loudness_lufs", -16)))
        return acc, info

    def write_timeline(self, path, accents):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["kind", "t_start", "t_end", "label"])
            w.writerow(["title", 0, self.title_s, self.cfg["title"]])
            for i, ch in enumerate(self.chars):
                w.writerow(["char", f"{self.starts[i]:.3f}", f"{self.starts[i] + self.durs[i]:.3f}",
                            f"列{ch['col'] + 1}-{ch['j'] + 1} {ch['text']}"])
            for a in accents:
                w.writerow(["music_accent", f"{a:.3f}", "", "宫音重音"])
            w.writerow(["zoom_out", f"{self.zoom_start:.3f}", f"{self.zoom_end:.3f}", "拉远看全篇"])
            w.writerow(["end_card", f"{self.end_start:.3f}", f"{self.duration:.3f}", "落款片尾"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeline", default="")
    ap.add_argument("--check-dir", default="")
    ap.add_argument("--preview", type=int, default=0, help="只渲 N 张静帧到 check-dir，不出视频")
    ap.add_argument("--workers", type=int, default=0)
    a = ap.parse_args()
    cfg = load_config(Path(a.config))
    job = Job(cfg)
    print(f"时长 {job.duration:.2f}s，书写 {job.write_start:.2f}–{job.write_end:.2f}s，帧数 {job.n_frames}")
    out = Path(a.out)
    if a.preview:
        d = Path(a.check_dir or out.parent) / "preview"
        d.mkdir(parents=True, exist_ok=True)
        for t in np.linspace(0.5, job.duration - 0.2, a.preview):
            Image.frombytes("RGB", (W, H), job.frame(int(t * job.fps))).save(d / f"t{t:05.2f}.jpg", quality=88)
        print("预览 →", d)
        return
    wav = out.with_suffix(".music.wav")
    acc, _ = job.audio(wav)
    if a.timeline:
        job.write_timeline(a.timeline, acc)
    frames = parallel_frames(job.frame, job.n_frames, workers=a.workers or None)
    render_frames_to_mp4(frames, job.n_frames, job.fps, W, H, wav, out, duration=job.n_frames / job.fps)
    print("成片 →", out)
    if a.check_dir:
        ts = [1.0, job.write_start + 1.0, job.write_start + job.write_s * 0.25, job.write_start + job.write_s * 0.5,
              job.write_start + job.write_s * 0.8, job.write_end - 0.2, job.zoom_end, job.duration - 0.6]
        labels = [f"{x:.1f}s" for x in ts]
        rep, sheet = verify(out, a.check_dir, ts, labels)
        print(json.dumps({k: rep[k] for k in ("video", "format", "decode_ok", "loudness")}, ensure_ascii=False))
        print("联系表 →", sheet)


if __name__ == "__main__":
    main()
