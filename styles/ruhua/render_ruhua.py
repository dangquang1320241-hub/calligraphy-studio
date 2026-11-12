#!/usr/bin/env python3
"""作品入画：把真实作品照片装裱后合成进 3–4 个场景（茶室横披 / 书房镜框 / 门匾 / 展厅），
场景之间缓慢运镜 + 叠化，每景一句短字幕，左上角标「场景为示意」。

作品像素只做缩放、透视、整体光照/色温匹配（乘法），不重画笔画；"门匾"模式把墨迹（照片里提取的
墨色遮罩）按原形转印到木纹上，属于示意效果。

用法：
  python render.py --config config.json --out 作品入画.mp4 [--check-dir 检查记录/作品入画] [--preview]
  --preview 只把每个场景的合成静帧存到 check-dir/scenes/，方便调四个角点。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent  # calligraphy-studio 根目录
COMMON = REPO / "common"
sys.path.insert(0, str(COMMON))
sys.path.insert(0, str(REPO))

import inkseg  # noqa: E402
from ambient import master  # noqa: E402
from cards import compose_end_card  # noqa: E402
from encode import render_frames_to_mp4  # noqa: E402
from media import INK, font as nfont, load_config, preprocess_artwork, resolve_path, text_image  # noqa: E402
from music_synth import synth  # noqa: E402
from render_util import blit, blit_center, parallel_frames, tag_pill, text_rgba, verify  # noqa: E402

W, H = 1080, 1920
SUPER = 2  # 场景按 2 倍输出分辨率合成，给推镜留余量


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * x * (x * (x * 6 - 15) + 10)


# ------------------------------------------------------------------ mounting
def _silk(w, h, color, seed=3, pattern=True):
    """绫：底色 + 细经纬纹 + 极淡的暗花（程序生成）。"""
    rng = np.random.default_rng(seed)
    base = np.ones((h, w, 3), np.float32) * np.array(color, np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    weave = (np.sin(xx * 1.9) * 0.5 + np.sin(yy * 2.1) * 0.5) * 2.2
    n = cv2.resize(rng.standard_normal((max(2, h // 6), max(2, w // 6))).astype(np.float32), (w, h)) * 3.0
    out = base + (weave + n)[..., None]
    if pattern:  # 暗花：稀疏的小菱形云纹
        cell = max(24, min(w, h) // 10)
        m = ((np.abs(((xx % cell) - cell / 2)) + np.abs(((yy % cell) - cell / 2))) < cell * 0.18).astype(np.float32)
        m = cv2.GaussianBlur(m, (0, 0), 1.2)
        out += m[..., None] * 7.0
    return np.clip(out, 0, 255).astype(np.uint8)


def _wood(w, h, color=(92, 60, 38), seed=5, grain_dir="h"):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a = yy if grain_dir == "h" else xx
    b = xx if grain_dir == "h" else yy
    warp = cv2.resize(rng.standard_normal((6, 6)).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC) * 18
    rings = np.sin((a + warp) * 0.35 + np.sin(b * 0.004) * 3) * 0.5 + 0.5
    fine = cv2.resize(rng.standard_normal((max(2, h // 2), max(2, w // 40))).astype(np.float32), (w, h)) * 0.5
    v = rings * 14 + fine * 8
    out = np.array(color, np.float32) + v[..., None] * np.array([1.0, 0.8, 0.6])
    return np.clip(out, 0, 255).astype(np.uint8)


def mount_artwork(art: np.ndarray, style: str, long_side: int = 2400):
    """返回 (RGBA 装裱好的平面图, 作品在其中的区域 (x0,y0,x1,y1))。art: RGB。"""
    h, w = art.shape[:2]
    s = long_side / max(w, h)
    aw, ah = int(w * s), int(h * s)
    a = cv2.resize(art, (aw, ah), interpolation=cv2.INTER_AREA)
    u = int(min(aw, ah) * 0.06)  # 基本单位
    if style == "scroll_h":  # 横披：绫裱 + 两端轴杆
        lx, ty = int(u * 2.0), int(u * 1.5)
        inner = max(3, u // 6)
        rod = int(u * 0.9)
        W_, H_ = aw + 2 * lx + 2 * rod, ah + 2 * ty
        out = np.zeros((H_, W_, 4), np.uint8)
        silk = _silk(W_ - 2 * rod, H_, (206, 196, 172), seed=11)
        out[:, rod:W_ - rod, :3] = silk
        out[:, rod:W_ - rod, 3] = 255
        # 隔水细边（米金）
        x0, y0 = rod + lx, ty
        cv2.rectangle(out, (x0 - inner * 2, y0 - inner * 2), (x0 + aw + inner * 2 - 1, y0 + ah + inner * 2 - 1),
                      (178, 152, 110, 255), -1)
        out[y0:y0 + ah, x0:x0 + aw, :3] = a
        # 轴杆
        wood = _wood(rod, H_ + 2 * int(u * 0.6), (58, 36, 24), seed=7, grain_dir="v")
        full = np.zeros((H_ + 2 * int(u * 0.6), W_, 4), np.uint8)
        off = int(u * 0.6)
        full[off:off + H_] = out
        for xs in (0, W_ - rod):
            full[:, xs:xs + rod, :3] = wood
            full[:, xs:xs + rod, 3] = 255
            # 圆柱高光
            g = np.sin(np.linspace(0, np.pi, rod))[None, :, None] * 30
            full[:, xs:xs + rod, :3] = np.clip(full[:, xs:xs + rod, :3].astype(np.float32) + g - 10, 0, 255)
        return full, (x0, y0 + off, x0 + aw, y0 + off + ah)
    if style == "frame":  # 书房镜框：深木框 + 绫边
        fr, mt = int(u * 0.55), int(u * 1.4)
        W_, H_ = aw + 2 * (fr + mt), ah + 2 * (fr + mt)
        out = np.zeros((H_, W_, 4), np.uint8)
        out[..., :3] = _wood(W_, H_, (70, 46, 30), seed=9)
        out[..., 3] = 255
        out[fr:H_ - fr, fr:W_ - fr, :3] = _silk(W_ - 2 * fr, H_ - 2 * fr, (214, 206, 186), seed=4)
        # 框内斜面阴影
        cv2.rectangle(out, (fr, fr), (W_ - fr - 1, H_ - fr - 1), (40, 28, 20, 255), max(2, u // 10))
        x0, y0 = fr + mt, fr + mt
        cv2.rectangle(out, (x0 - 3, y0 - 3), (x0 + aw + 2, y0 + ah + 2), (170, 146, 106, 255), -1)
        out[y0:y0 + ah, x0:x0 + aw, :3] = a
        # 玻璃反光：一道极淡斜光
        yy, xx = np.mgrid[0:H_, 0:W_].astype(np.float32)
        band = np.exp(-(((xx - yy * 1.3) - W_ * 0.35) / (W_ * 0.08)) ** 2) * 10
        out[..., :3] = np.clip(out[..., :3].astype(np.float32) + band[..., None], 0, 255).astype(np.uint8)
        return out, (x0, y0, x0 + aw, y0 + ah)
    if style == "gallery":  # 展厅：细黑框 + 白卡纸
        fr, mt = int(u * 0.28), int(u * 2.2)
        W_, H_ = aw + 2 * (fr + mt), ah + 2 * (fr + mt)
        out = np.zeros((H_, W_, 4), np.uint8)
        out[..., :3] = (30, 28, 27)
        out[..., 3] = 255
        out[fr:H_ - fr, fr:W_ - fr, :3] = (238, 234, 226)
        x0, y0 = fr + mt, fr + mt
        cv2.rectangle(out, (x0 - 4, y0 - 4), (x0 + aw + 3, y0 + ah + 3), (205, 200, 190, 255), 2)
        out[y0:y0 + ah, x0:x0 + aw, :3] = a
        return out, (x0, y0, x0 + aw, y0 + ah)
    if style == "plaque":  # 匾：只把墨迹按原形转印，纸不要
        soft, _ = inkseg.ink_masks(a)
        out = np.zeros((ah, aw, 4), np.uint8)
        out[..., :3] = (24, 18, 14)
        out[..., 3] = (np.clip(soft * 1.15, 0, 1) * 235).astype(np.uint8)
        return out, (0, 0, aw, ah)
    raise ValueError(f"未知装裱方式 {style}")


def fit_quad(quad, aspect, align="center"):
    """在四边形 quad(tl,tr,br,bl) 内按宽高比放一个子四边形（双线性插值，保留透视）。"""
    q = np.asarray(quad, np.float32)
    top = np.linalg.norm(q[1] - q[0]); left = np.linalg.norm(q[3] - q[0])
    qa = top / max(left, 1e-6)
    if aspect >= qa:
        u0, u1 = 0.0, 1.0
        hh = qa / aspect
        v0 = (1 - hh) / 2 if align == "center" else 0.0
        v1 = v0 + hh
    else:
        ww = aspect / qa
        u0 = (1 - ww) / 2; u1 = u0 + ww
        v0, v1 = 0.0, 1.0

    def P(u, v):
        return (1 - v) * ((1 - u) * q[0] + u * q[1]) + v * ((1 - u) * q[3] + u * q[2])

    return np.array([P(u0, v0), P(u1, v0), P(u1, v1), P(u0, v1)], np.float32)


def composite_scene(bg_path, piece_rgba, quad_norm, sc, art_box=None):
    """返回 SUPER 倍分辨率的合成图 (RGB uint8)。"""
    bg = Image.open(bg_path).convert("RGB")
    BW, BH = W * SUPER, H * SUPER
    # 背景按 cover 方式缩放到 9:16
    r = max(BW / bg.width, BH / bg.height)
    bgs = bg.resize((int(bg.width * r + 0.5), int(bg.height * r + 0.5)), Image.Resampling.LANCZOS)
    ox, oy = (bgs.width - BW) // 2, (bgs.height - BH) // 2
    bgs = bgs.crop((ox, oy, ox + BW, oy + BH))
    base = np.asarray(bgs).astype(np.float32)
    quad = np.array([[x * BW, y * BH] for x, y in quad_norm], np.float32)
    ph, pw = piece_rgba.shape[:2]
    dq = fit_quad(quad, pw / ph, sc.get("align", "center"))
    src = np.array([[0, 0], [pw - 1, 0], [pw - 1, ph - 1], [0, ph - 1]], np.float32)
    M = cv2.getPerspectiveTransform(src, dq)
    warped = cv2.warpPerspective(piece_rgba, M, (BW, BH), flags=cv2.INTER_AREA if pw > BW else cv2.INTER_LINEAR,
                                 borderValue=(0, 0, 0, 0)).astype(np.float32)
    alpha = warped[..., 3:4] / 255.0
    rgb = warped[..., :3]
    # ---- 光照匹配：用背景在该区域的低频亮度做乘法（只改明暗/色温，不改形状）
    lowbg = cv2.GaussianBlur(base, (0, 0), BW / 18)
    m = alpha[..., 0] > 0.5
    lum = lowbg.mean(2)
    ref = lum[m].mean() if m.any() else 200.0
    light = (lum / max(ref, 1.0)) ** float(sc.get("light_follow", 0.8))
    gain = float(sc.get("brightness", 1.0))
    tint = (lowbg[m].mean(0) / max(lowbg[m].mean(), 1.0)) if m.any() else np.ones(3)
    tint = tint ** float(sc.get("tint_strength", 0.35))
    rgb = rgb * light[..., None] * gain * tint[None, None, :]
    # ---- 阴影
    shd = sc.get("shadow", {})
    if sc.get("style") != "plaque":
        a_sh = alpha[..., 0]
        dx, dy = float(shd.get("dx", 0.006)) * BW, float(shd.get("dy", 0.012)) * BH
        T = np.float32([[1, 0, dx], [0, 1, dy]])
        a_sh = cv2.warpAffine(a_sh, T, (BW, BH))
        a_sh = cv2.GaussianBlur(a_sh, (0, 0), float(shd.get("blur", 0.012)) * BW)
        base = base * (1 - a_sh[..., None] * float(shd.get("opacity", 0.45)))
        # 接触阴影（紧贴边缘的一圈更暗）
        c = cv2.GaussianBlur(alpha[..., 0], (0, 0), 0.0025 * BW)
        base = base * (1 - c[..., None] * 0.25)
        out = base * (1 - alpha) + rgb * alpha
    else:
        # 墨迹转印：乘性混合，让木纹透出来；再加一点点浮雕感
        ink = alpha[..., 0]
        emb = cv2.GaussianBlur(ink, (0, 0), 1.5)
        gy = np.gradient(emb, axis=0)
        out = base * (1 - ink[..., None] * 0.92) + rgb * ink[..., None] * 0.92
        out = out + (-gy * 120)[..., None] * float(sc.get("emboss", 0.6))
    return np.clip(out, 0, 255).astype(np.uint8), dq


class Job:
    def __init__(self, cfg):
        self.cfg = cfg
        self.fps = int(cfg.get("fps", 30))
        tm = cfg.get("timing", {})
        self.title_s = float(tm.get("title_seconds", 3.0))
        self.scene_s = float(tm.get("scene_seconds", 6.5))
        self.xfade = float(tm.get("crossfade", 1.1))
        self.end_s = float(tm.get("end_card_seconds", 4.5))
        cleaned = resolve_path(cfg, cfg.get("cleaned"))
        if cfg.get("geometry"):
            from artwork_prep import prepare_cleaned
            cleaned = prepare_cleaned(cfg)
        if cleaned is None or not cleaned.exists():
            cleaned = Path(cfg["_config_dir"]) / "作品_clean.png"
            preprocess_artwork(resolve_path(cfg, cfg["artwork"]), cleaned, scale=2.0)
        self.art = np.asarray(Image.open(cleaned).convert("RGB"))
        self.scenes = []
        for k, sc in enumerate(cfg["scenes"]):
            piece, _ = mount_artwork(self.art, sc.get("style", "frame"), int(sc.get("piece_px", 2400)))
            comp, dq = composite_scene(resolve_path(cfg, sc["background"]), piece, sc["corners"], sc)
            self.scenes.append({**sc, "img": comp, "quad": dq})
            print(f"场景 {k + 1} {sc.get('caption', '')} 合成完成", flush=True)
        n = len(self.scenes)
        self.scene_t0 = [self.title_s + i * self.scene_s for i in range(n)]
        self.end_start = self.title_s + n * self.scene_s
        self.duration = self.end_start + self.end_s
        self.n_frames = int(round(self.duration * self.fps))
        self.tag = tag_pill(cfg.get("tag_text", "场景为示意"), 30)
        self.caps = []
        for sc in self.scenes:
            a = text_rgba(sc.get("caption", ""), 58, (246, 238, 222), tracking=8)
            b = text_rgba(sc.get("caption_sub", ""), 36, (226, 212, 186), tracking=4) if sc.get("caption_sub") else None
            self.caps.append((a, b))
        # 片头：作品平铺在深色上
        self.title_bg = self._title_bg()
        self.title_im = text_image(cfg["title"], nfont(80, bold=True), (243, 232, 212, 255), tracking=4)
        self.sub_im = text_image(cfg.get("title_sub", "作品入画"), nfont(38), (196, 165, 116, 255), tracking=12)
        self.grad = self._caption_gradient()

    def _title_bg(self):
        bg = np.zeros((H, W, 3), np.uint8); bg[:] = (22, 18, 16)
        h, w = self.art.shape[:2]
        tw = 1000; th = int(h * tw / w)
        a = cv2.resize(self.art, (tw, th), interpolation=cv2.INTER_AREA)
        y0 = 330
        im = Image.fromarray(bg).convert("RGBA")
        sh = Image.new("RGBA", (tw + 80, th + 80), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rectangle((40, 52, tw + 40, th + 52), fill=(0, 0, 0, 150))
        im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18)), ((W - tw) // 2 - 40, y0 - 40))
        im.paste(Image.fromarray(a), ((W - tw) // 2, y0))
        self.title_art_rect = ((W - tw) // 2, y0, tw, th)
        return im

    def _caption_gradient(self):
        g = np.zeros((H, W, 4), np.uint8)
        y = np.arange(H, dtype=np.float32)
        a = np.clip((y - 1250) / 200, 0, 1) * np.clip((1700 - y) / 120, 0, 1) * 0.55
        g[..., 3] = (a[:, None] * 255).astype(np.uint8)
        g[..., :3] = (12, 9, 7)
        return Image.fromarray(g, "RGBA")

    def camera(self, k, u):
        """场景 k 的取景框（SUPER 坐标），u = 场景内进度 0..1（含叠化余量）。"""
        sc = self.scenes[k]
        cam = sc.get("camera", {})
        BW, BH = W * SUPER, H * SUPER
        z0, z1 = float(cam.get("zoom_from", 1.0)), float(cam.get("zoom_to", 1.12))
        fx0, fy0 = cam.get("focus_from", [0.5, 0.5])
        fx1, fy1 = cam.get("focus_to", None) or self._quad_center(k)
        e = ease(u) * 0.85 + u * 0.15
        z = z0 + (z1 - z0) * e
        fx = fx0 + (fx1 - fx0) * e; fy = fy0 + (fy1 - fy0) * e
        cw, ch = BW / z, BH / z
        x0 = np.clip(fx * BW - cw / 2, 0, BW - cw); y0 = np.clip(fy * BH - ch / 2, 0, BH - ch)
        return x0, y0, cw, ch

    def _quad_center(self, k):
        q = self.scenes[k]["quad"]
        c = q.mean(0)
        return [float(c[0] / (W * SUPER)), float(c[1] / (H * SUPER))]

    def view(self, k, u):
        x0, y0, cw, ch = self.camera(k, u)
        M = np.float32([[W / cw, 0, -x0 * W / cw], [0, H / ch, -y0 * H / ch]])
        return cv2.warpAffine(self.scenes[k]["img"], M, (W, H), flags=cv2.INTER_AREA)

    def _wipe(self, a_img, b_img, p):
        """柔边横扫转场（自右向左，和书写方向一致），避免两幅作品叠成双影。"""
        feather = 360.0
        x = np.arange(W, dtype=np.float32)
        front = (1 - p) * (W + 2 * feather) - feather  # 从右往左推进
        m = np.clip((x - front) / feather, 0, 1)
        m = (m * m * (3 - 2 * m))[None, :, None]
        return (a_img.astype(np.float32) * (1 - m) + b_img.astype(np.float32) * m).astype(np.uint8)

    def frame(self, fi):
        t = fi / self.fps
        n = len(self.scenes)
        span = self.scene_s + self.xfade
        if t < self.title_s:
            im = self.title_bg.copy()
            op = ease(t / 0.4) if t < 0.4 else 1.0
            blit_center(im, self.title_im, W / 2, 1150, max(op, 0.85))
            blit_center(im, self.sub_im, W / 2, 1240, op)
            # 最后 xfade 秒叠到第一景
            if t > self.title_s - self.xfade:
                a = ease((t - (self.title_s - self.xfade)) / self.xfade)
                im = Image.fromarray(self._wipe(np.asarray(im.convert("RGB")), self.view(0, (t - self.title_s + self.xfade) / (span + self.xfade)), a)).convert("RGBA")
            return im.convert("RGB").tobytes()
        # 当前场景
        k = min(n - 1, int((t - self.title_s) // self.scene_s))
        lt = t - self.scene_t0[k]
        u = (lt + self.xfade) / (span + self.xfade)
        cur = self.view(k, u)
        if k + 1 < n and lt > self.scene_s - self.xfade:
            a = ease((lt - (self.scene_s - self.xfade)) / self.xfade)
            nxt = self.view(k + 1, (lt - self.scene_s + self.xfade) / (span + self.xfade))
            cur = self._wipe(cur, nxt, a)
        im = Image.fromarray(cur).convert("RGBA")
        # 片尾：最后一景压暗，叠落款
        if t >= self.end_start:
            e = ease((t - self.end_start) / 0.9)
            dark = Image.new("RGBA", (W, H), (14, 11, 9, int(185 * e)))
            im.alpha_composite(dark)
            im = compose_end_card(im, self.cfg["title"], self.cfg.get("subtitle", ""),
                                  self.cfg.get("author", "作者名 · 书"), self.cfg.get("author_note", "落款占位，可替换"),
                                  self.cfg.get("seal_char", "书"), e)
        else:
            # 字幕：进场快（0.35s），停得久，换景前淡出
            cop = ease((lt - 0.5) / 0.35) * (1 - ease((lt - (self.scene_s - self.xfade - 0.4)) / 0.4))
            if cop > 0:
                im.alpha_composite(Image.blend(Image.new("RGBA", (W, H), (0, 0, 0, 0)), self.grad, cop))
                a, b = self.caps[k]
                cy = int(self.cfg.get("caption_y_px", 1420))
                blit_center(im, a, W / 2, cy, cop)
                if b is not None:
                    blit_center(im, b, W / 2, cy + 72, cop * 0.95)
        if t >= self.title_s - 0.3 and t < self.end_start + 0.5:
            blit(im, self.tag, 40, 72, 0.95)
        return im.convert("RGB").tobytes()

    def audio(self, out_wav):
        m = self.cfg.get("music", {})
        acc = [round(x, 3) for x in self.scene_t0] + [round(self.end_start, 3)]
        music = synth(self.duration, sr=48000, seed=int(m.get("seed", 33)), root=float(m.get("root", 130.81)),
                      bpm=float(m.get("bpm", 52)), density=float(m.get("density", 0.5)),
                      xiao=float(m.get("xiao", 0.8)), accents=acc, accent_gain=0.55)
        master([(music, 0.0)], out_wav, 48000, target_lufs=float(self.cfg.get("loudness_lufs", -16)))
        return acc

    def write_timeline(self, path, acc):
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["kind", "t_start", "t_end", "label"])
            w.writerow(["title", 0, self.title_s, self.cfg["title"]])
            for k, sc in enumerate(self.scenes):
                w.writerow(["scene", f"{self.scene_t0[k]:.3f}", f"{self.scene_t0[k] + self.scene_s:.3f}", sc.get("caption", "")])
            for a in acc:
                w.writerow(["music_accent", f"{a:.3f}", "", "换景重音"])
            w.writerow(["end_card", f"{self.end_start:.3f}", f"{self.duration:.3f}", "落款片尾"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeline", default="")
    ap.add_argument("--check-dir", default="")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--workers", type=int, default=0)
    a = ap.parse_args()
    cfg = load_config(Path(a.config))
    job = Job(cfg)
    print(f"时长 {job.duration:.2f}s，{len(job.scenes)} 景，帧数 {job.n_frames}")
    out = Path(a.out)
    if a.preview:
        d = Path(a.check_dir or out.parent) / "scenes"; d.mkdir(parents=True, exist_ok=True)
        for k in range(len(job.scenes)):
            t = job.scene_t0[k] + job.scene_s * 0.5
            Image.frombytes("RGB", (W, H), job.frame(int(t * job.fps))).save(d / f"scene{k + 1}.jpg", quality=88)
            Image.fromarray(job.scenes[k]["img"]).resize((W, H)).save(d / f"scene{k + 1}_full.jpg", quality=88)
        Image.frombytes("RGB", (W, H), job.frame(int(1.0 * job.fps))).save(d / "title.jpg", quality=88)
        Image.frombytes("RGB", (W, H), job.frame(job.n_frames - 10)).save(d / "end.jpg", quality=88)
        print("预览 →", d)
        return
    wav = out.with_suffix(".music.wav")
    acc = job.audio(wav)
    if a.timeline:
        job.write_timeline(a.timeline, acc)
    render_frames_to_mp4(parallel_frames(job.frame, job.n_frames, workers=a.workers or None),
                         job.n_frames, job.fps, W, H, wav, out, duration=job.n_frames / job.fps)
    print("成片 →", out)
    if a.check_dir:
        ts = [1.0]
        for k in range(len(job.scenes)):
            ts.append(job.scene_t0[k] + job.scene_s * 0.45)
        ts += [job.scene_t0[1] - job.xfade * 0.5, job.end_start + 0.3, job.duration - 0.5]
        ts = sorted(ts)
        rep, sheet = verify(out, a.check_dir, ts, [f"{x:.1f}s" for x in ts])
        print(json.dumps({k: rep[k] for k in ("video", "format", "decode_ok", "loudness")}, ensure_ascii=False))
        print("联系表 →", sheet)


if __name__ == "__main__":
    main()
