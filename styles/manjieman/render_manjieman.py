#!/usr/bin/env python3
"""慢揭幕：片头 → 自右向左逐行墨色揭开 → 缓推近 + 全文 → 落款片尾。

用法:
  python render.py --config path/to/config.json --out path/to.mp4
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
COMMON = REPO / "common"
sys.path.insert(0, str(COMMON))

from audio import render_music  # noqa: E402
from cards import compose_end_card, compose_title_card  # noqa: E402
from encode import (  # noqa: E402
    contact_sheet,
    decode_test,
    extract_frames,
    ffprobe_check,
    render_frames_to_mp4,
)
from media import (  # noqa: E402
    CAPTION_BOT,
    CAPTION_TOP,
    CREAM,
    GOLD,
    MUTED,
    SAFE_BOTTOM,
    STAGE_BOT,
    STAGE_TOP,
    as_norm,
    apply_vignette,
    blurred_work,
    composite_work,
    crop_and_resize,
    ease_in_out,
    fade,
    font,
    frame_to_rgb_bytes,
    hold_fade,
    ink_wipe_mask,
    lerp,
    load_config,
    load_rgb,
    make_background,
    make_paper_texture,
    make_vignette,
    mix_reveal,
    pad_rect_to_aspect,
    paste_centered,
    preprocess_artwork,
    resolve_path,
    rgb_to_image,
    sample_paper_color,
    text_image,
)


def _sorted_columns(cfg, work_w):
    cols = []
    for c in cfg["columns"]:
        x0 = as_norm(c["x0"], work_w)
        x1 = as_norm(c["x1"], work_w)
        if x0 > x1:
            x0, x1 = x1, x0
        cols.append({**c, "x0n": x0, "x1n": x1})
    # reveal right to left: larger x first
    cols.sort(key=lambda c: -(c["x0n"] + c["x1n"]) / 2.0)
    return cols


def _passage_lines(cfg):
    if cfg.get("passage_lines"):
        return list(cfg["passage_lines"])
    text = cfg.get("passage") or ""
    return [text] if text else []


def build_schedule(cfg, n_cols):
    va = cfg.get("video_a") or cfg.get("video") or {}
    dur = float(va.get("duration", cfg.get("duration", 30.0)))
    title_s = float(va.get("title_seconds", 2.0))
    intro = float(va.get("intro_hold", 0.8))
    reveal_end = float(va.get("reveal_end", 18.0))
    push_end = float(va.get("push_end", 24.5))
    wipe = float(va.get("wipe_duration", 0.75))
    reveal_start = title_s + intro
    span = max(0.5, reveal_end - reveal_start)
    step = span / n_cols
    starts = [reveal_start + i * step for i in range(n_cols)]
    return {
        "duration": dur,
        "title_s": title_s,
        "intro": intro,
        "reveal_start": reveal_start,
        "reveal_end": reveal_end,
        "push_end": push_end,
        "wipe": min(wipe, step * 0.85),
        "step": step,
        "starts": starts,
        "fps": int(cfg.get("fps", 30)),
        "width": int(cfg.get("width", 1080)),
        "height": int(cfg.get("height", 1920)),
    }


def build_plucks(sched, cols):
    # sparse pentatonic accents aligned to visual events
    # v3: drop C4 and above one octave so the register sits in a darker guqin range
    seq = ["G3", "D3", "G3", "A3", "C3", "D3", "E3", "D3", "C3"]
    plucks = [
        {"time": 0.15, "pitch": "G3", "amp": 0.36, "dur": 3.8},
        {"time": 1.10, "pitch": "D3", "amp": 0.22, "dur": 3.2},
    ]
    climaxes = {3}  # 4th column (0-based) often 有声
    for i, st in enumerate(sched["starts"]):
        pitch = seq[(i + 2) % len(seq)]
        amp = 0.48 if i in climaxes else 0.32
        plucks.append({"time": st, "pitch": pitch, "amp": amp, "dur": 3.5})
        if i in climaxes:
            plucks.append({"time": st, "pitch": "G3", "amp": 0.22, "dur": 4.0})
    plucks.append({"time": sched["reveal_end"], "pitch": "G3", "amp": 0.36, "dur": 4.0})
    plucks.append({"time": sched["reveal_end"] + 2.6, "pitch": "E3", "amp": 0.18, "dur": 3.4})
    plucks.append({"time": sched["push_end"], "pitch": "G2", "amp": 0.40, "dur": 4.5})
    plucks.append({"time": sched["push_end"] + 2.2, "pitch": "D3", "amp": 0.18, "dur": 3.6})
    if sched["duration"] - sched["push_end"] > 4.0:
        plucks.append({"time": sched["duration"] - 2.4, "pitch": "G3", "amp": 0.14, "dur": 3.0})
    return plucks


def write_timeline(path: Path, sched, cols, plucks):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    rows.append({"time": 0.0, "kind": "section", "video": "A", "name": "title", "extra": ""})
    rows.append({"time": sched["title_s"], "kind": "section", "video": "A", "name": "intro_blur", "extra": ""})
    for i, (st, col) in enumerate(zip(sched["starts"], cols)):
        rows.append(
            {
                "time": st,
                "kind": "visual",
                "video": "A",
                "name": f"reveal_col{col.get('id', i+1)}",
                "extra": col.get("text", ""),
            }
        )
    rows.append({"time": sched["reveal_end"], "kind": "section", "video": "A", "name": "push_in", "extra": ""})
    rows.append({"time": sched["push_end"], "kind": "section", "video": "A", "name": "end_card", "extra": ""})
    for ev in plucks:
        rows.append(
            {
                "time": ev["time"],
                "kind": "pluck",
                "video": "A",
                "name": ev["pitch"],
                "extra": f"amp={ev['amp']}",
            }
        )
    rows.sort(key=lambda r: (float(r["time"]), r["kind"]))
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["time", "kind", "video", "name", "extra"])
        w.writeheader()
        for r in rows:
            w.writerow(r)


class SlowReveal:
    def __init__(self, cfg, work: np.ndarray):
        self.cfg = cfg
        self.work = work
        self.H, self.W = work.shape[:2]
        self.cols = _sorted_columns(cfg, self.W)
        self.sched = build_schedule(cfg, len(self.cols))
        self.fw = self.sched["width"]
        self.fh = self.sched["height"]
        self.blur = blurred_work(work, radius=31)
        paper_c = sample_paper_color(work)
        self.paper = rgb_to_image(make_paper_texture(self.fw, self.fh, color=paper_c, seed=20261110))
        self.bg = rgb_to_image(make_background(work, self.fw, self.fh, blur=61, dark=0.15))
        self.vig = make_vignette(self.fw, self.fh, strength=0.48, power=1.85)
        # faint work for title bg
        faint = Image.fromarray(self.blur).resize((self.fw, int(self.fw * self.H / self.W)), Image.Resampling.LANCZOS)
        paper = self.paper.convert("RGBA")
        # keep paper warm; a very faint ghost of the work only
        fy = (self.fh - faint.height) // 2
        faint_rgba = faint.convert("RGBA")
        faint_rgba.putalpha(28)
        paper.paste(faint_rgba, (0, fy), faint_rgba)
        # slight warm wash, not grey
        wash = Image.new("RGBA", (self.fw, self.fh), (212, 188, 150, 40))
        self.title_bg = Image.alpha_composite(paper, wash)

        self.col_layers = []
        for c in self.cols:
            im = text_image(c.get("text", ""), font(42, bold=False), CREAM + (255,), tracking=8)
            self.col_layers.append(im)

        lines = _passage_lines(cfg)
        self.passage_layers = [
            text_image(line, font(30, bold=False), CREAM + (255,), tracking=2) for line in lines
        ]
        self.title = cfg.get("title", "")
        self.subtitle = cfg.get("subtitle", "")
        self.author = cfg.get("author", "作者名 · 书")
        self.author_note = cfg.get("author_note", "落款占位，可替换")
        self.seal_char = cfg.get("seal_char", "书")
        # running header
        self.header = text_image(self.title, font(28, bold=False), GOLD + (255,), tracking=6)

    def _col_progress(self, t):
        s = self.sched
        out = []
        for st in s["starts"]:
            p = 0.0
            if t >= st + s["wipe"]:
                p = 1.0
            elif t > st:
                p = ease_in_out((t - st) / s["wipe"])
            out.append(p)
        return out

    def _camera_rect(self, t):
        """Source rect in work pixels. After reveal, slow push-in."""
        s = self.sched
        if t <= s["reveal_end"]:
            z = 1.0
            cx, cy = self.W * 0.5, self.H * 0.5
        else:
            p = ease_in_out((t - s["reveal_end"]) / max(0.01, s["push_end"] - s["reveal_end"]))
            p = min(1.0, p)
            # keep push during end card
            z = 1.0 + 0.06 * p
            cx = self.W * 0.50
            cy = self.H * 0.50
        vw, vh = self.W / z, self.H / z
        x0 = cx - vw / 2
        y0 = cy - vh / 2
        x0 = max(0, min(x0, self.W - vw))
        y0 = max(0, min(y0, self.H - vh))
        return (x0, y0, x0 + vw, y0 + vh)

    def _place(self, src_w, src_h):
        stage_w = self.fw
        stage_h = STAGE_BOT - STAGE_TOP
        scale = min(stage_w / src_w, stage_h / src_h)
        dw, dh = src_w * scale, src_h * scale
        # fit-width preferred when showing nearly the full work
        dx = (self.fw - dw) / 2
        dy = STAGE_TOP + (stage_h - dh) / 2
        return dx, dy, dw, dh

    def frame(self, t: float) -> Image.Image:
        s = self.sched
        # --- title card ---
        if t < s["title_s"] + 0.85:
            title_op = 1.0
            if t >= s["title_s"]:
                title_op = 1.0 - fade(t, s["title_s"], s["title_s"] + 0.85)
            card = compose_title_card(self.title_bg, self.title, self.subtitle, opacity=title_op, t_local=t)
            if t < s["title_s"]:
                arr = apply_vignette(np.array(card.convert("RGB")), self.vig)
                return Image.fromarray(arr)
            # fall through and blend with work
            title_im = card
        else:
            title_im = None
            title_op = 0.0

        # --- work reveal (composite at display size so the veil is obvious) ---
        progresses = self._col_progress(t)
        cam = self._camera_rect(t)
        src_w, src_h = cam[2] - cam[0], cam[3] - cam[1]
        dx, dy, dw, dh = self._place(src_w, src_h)
        sharp = crop_and_resize(self.work, cam, dw, dh)
        blur = sharp.filter(ImageFilter.GaussianBlur(radius=16))
        blur_arr = np.array(blur).astype(np.float32)
        # veil: darker, slightly less contrast
        mean = blur_arr.mean()
        blur_arr = (blur_arr - mean) * 0.72 + mean * 0.78
        blur_arr = np.clip(blur_arr, 0, 255).astype(np.uint8)
        # map column x-ranges through the camera into dest pixels
        cam_x0, cam_x1 = cam[0], cam[2]
        col_dest = []
        for c in self.cols:
            x0 = (c["x0n"] * self.W - cam_x0) / max(cam_x1 - cam_x0, 1e-6) * dw
            x1 = (c["x1n"] * self.W - cam_x0) / max(cam_x1 - cam_x0, 1e-6) * dw
            col_dest.append((x0, x1))
        mask = ink_wipe_mask(int(round(dh)), int(round(dw)), col_dest, progresses, feather_ratio=0.16)
        revealed = mix_reveal(np.array(sharp), blur_arr, mask)
        work_im = Image.fromarray(revealed)

        canvas = self.bg.convert("RGBA")
        # running header above the work, after title
        header_op = fade(t, s["title_s"] + 0.3, s["title_s"] + 0.9)
        if t >= s["push_end"]:
            header_op *= 1.0 - fade(t, s["push_end"], s["push_end"] + 0.7)
        if header_op > 0.01:
            canvas = paste_centered(canvas, self.header, self.fw // 2, 128, header_op)

        canvas = composite_work(canvas, work_im, dx, dy, shadow=True)

        # column subtitle: show the current revealing/just-revealed column
        cap_op_total = 0
        active_idx = None
        for i, st in enumerate(s["starts"]):
            nxt = s["starts"][i + 1] if i + 1 < len(s["starts"]) else s["reveal_end"]
            # visible from start to just before next, fade
            op = hold_fade(t, st + 0.05, nxt - 0.15, fade_in=0.30, fade_out=0.28)
            if op > cap_op_total:
                cap_op_total = op
                active_idx = i
        if active_idx is not None and cap_op_total > 0.02 and t < s["reveal_end"] + 0.4:
            layer = self.col_layers[active_idx]
            cy = int((CAPTION_TOP + CAPTION_BOT) / 2) - 20
            canvas = paste_centered(canvas, layer, self.fw // 2, cy, cap_op_total)

        # full passage after reveal
        pass_op = fade(t, s["reveal_end"] + 0.25, s["reveal_end"] + 1.1)
        if t >= s["push_end"]:
            pass_op *= 1.0 - fade(t, s["push_end"], s["push_end"] + 0.6)
        if pass_op > 0.02 and self.passage_layers:
            total_h = sum(im.height for im in self.passage_layers) + 10 * (len(self.passage_layers) - 1)
            y0 = int((CAPTION_TOP + CAPTION_BOT) / 2 - total_h / 2)
            # keep inside safe area
            y0 = min(y0, 1540 - total_h)
            y = y0
            for im in self.passage_layers:
                canvas = paste_centered(canvas, im, self.fw // 2, y + im.height // 2, pass_op)
                y += im.height + 10

        # end card typography (work stays as cover)
        end_op = fade(t, s["push_end"], s["push_end"] + 0.8)
        if end_op > 0.02:
            canvas = compose_end_card(
                canvas,
                self.title,
                self.subtitle,
                self.author,
                self.author_note,
                self.seal_char,
                opacity=end_op,
            )

        rgb = np.array(canvas.convert("RGB"))
        rgb = apply_vignette(rgb, self.vig)
        out = Image.fromarray(rgb)

        if title_im is not None and title_op > 0.01:
            out = Image.blend(out, title_im.convert("RGB"), title_op)
        return out


def prepare_work(cfg):
    if cfg.get("geometry"):  # 新增（v2）：先拉正再裁纸，见 common/artwork_prep.py；无 geometry 时行为不变
        from artwork_prep import prepare_cleaned
        return load_rgb(prepare_cleaned(cfg))
    cleaned = resolve_path(cfg, cfg.get("cleaned"))
    artwork = resolve_path(cfg, cfg.get("artwork"))
    pre = cfg.get("preprocess") or {}
    run = pre.get("run", cleaned is None or (cleaned is not None and not cleaned.exists()))
    if run:
        if artwork is None or not artwork.exists():
            raise FileNotFoundError("config 里需要有效的 artwork 路径")
        if cleaned is None:
            cleaned = (cfg.get("_config_dir") or Path.cwd()) / "作品_clean.png"
        scale = float(pre.get("scale", 2.0))
        print(f"preprocess {artwork} -> {cleaned} scale={scale}")
        preprocess_artwork(artwork, cleaned, scale=scale)
    elif cleaned is None or not cleaned.exists():
        raise FileNotFoundError("找不到 cleaned 图像，且 preprocess.run 未开启")
    return load_rgb(cleaned)


def render(cfg, out_path: Path, timeline_path: Path | None = None, music_dir: Path | None = None):
    work = prepare_work(cfg)
    world = SlowReveal(cfg, work)
    sched = world.sched
    plucks = build_plucks(sched, world.cols)
    if timeline_path:
        write_timeline(timeline_path, sched, world.cols, plucks)
    music_dir = Path(music_dir or out_path.parent / "audio")
    music_dir.mkdir(parents=True, exist_ok=True)
    wav = music_dir / (out_path.stem + ".wav")
    print("synthesizing music…")
    info = render_music(
        plucks,
        sched["duration"],
        wav,
        target_lufs=float(cfg.get("loudness_lufs", -16)),
        tone=str(cfg.get("audio_tone", "warm")),
    )
    print("loudnorm", {k: info.get(k) for k in ("input_i", "input_tp", "output_i", "output_tp") if k in info})

    fps = sched["fps"]
    n = int(round(sched["duration"] * fps))

    def frames():
        for i in range(n):
            t = i / fps
            im = world.frame(t)
            yield frame_to_rgb_bytes(im)

    print(f"rendering {n} frames → {out_path}")
    render_frames_to_mp4(frames(), n, fps, sched["width"], sched["height"], wav, out_path, duration=sched["duration"])
    return world, sched, out_path


def main(argv=None):
    p = argparse.ArgumentParser(description="书法作品「慢揭幕」模板")
    p.add_argument("--config", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--timeline", default="")
    p.add_argument("--music-dir", default="")
    args = p.parse_args(argv)
    cfg = load_config(args.config)
    render(
        cfg,
        Path(args.out),
        timeline_path=Path(args.timeline) if args.timeline else None,
        music_dir=Path(args.music_dir) if args.music_dir else None,
    )
    print("done", args.out)


if __name__ == "__main__":
    main()
