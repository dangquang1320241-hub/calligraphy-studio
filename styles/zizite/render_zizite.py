#!/usr/bin/env python3
"""逐字特写：全篇定场 → Ken Burns 停在若干精彩字上并出点评 → 拉回全篇 + 落款。

用法:
  python render.py --config path/to/config.json --out path/to.mp4
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
COMMON = REPO / "common"
sys.path.insert(0, str(COMMON))

from audio import render_music  # noqa: E402
from cards import compose_end_card  # noqa: E402
from encode import render_frames_to_mp4  # noqa: E402
from media import (  # noqa: E402
    CAPTION_BOT,
    CAPTION_TOP,
    CREAM,
    GOLD,
    STAGE_BOT,
    STAGE_TOP,
    apply_vignette,
    as_norm,
    composite_work,
    crop_and_resize,
    ease_in_out,
    fade,
    font,
    frame_to_rgb_bytes,
    hold_fade,
    lerp_rect,
    load_config,
    load_rgb,
    make_background,
    make_vignette,
    pad_rect_to_aspect,
    paste_centered,
    preprocess_artwork,
    resolve_path,
    rgb_to_image,
    text_image,
)


def _highlights(cfg, W, H):
    out = []
    for h in cfg.get("highlights") or []:
        box = h["box"]
        x0, y0, x1, y1 = box
        x0, x1 = as_norm(x0, W), as_norm(x1, W)
        y0, y1 = as_norm(y0, H), as_norm(y1, H)
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        out.append({**h, "boxn": (x0, y0, x1, y1)})
    return out


def build_schedule(cfg, n_hi):
    vb = cfg.get("video_b") or cfg.get("video") or {}
    dur = float(vb.get("duration", cfg.get("duration", 32.0)))
    open_hold = float(vb.get("open_hold", 2.3))
    travel = float(vb.get("travel", 1.5))
    hold = float(vb.get("hold", 3.1))
    pullback = float(vb.get("pullback", 2.8))
    end_card = float(vb.get("end_card", 3.4))
    # If the sum doesn't match duration, scale the hold slightly.
    body = n_hi * (travel + hold)
    total = open_hold + body + pullback + end_card
    if abs(total - dur) > 0.05 and n_hi > 0:
        # adjust hold to fit
        leftover = dur - (open_hold + n_hi * travel + pullback + end_card)
        hold = max(2.2, leftover / n_hi)
        body = n_hi * (travel + hold)
        total = open_hold + body + pullback + end_card
        dur = total
    keys = []
    t = open_hold
    for i in range(n_hi):
        arrive = t + travel
        leave = arrive + hold
        keys.append({"travel_start": t, "arrive": arrive, "leave": leave})
        t = leave
    pull_start = t
    end_start = pull_start + pullback
    duration = end_start + end_card
    return {
        "duration": duration,
        "open_hold": open_hold,
        "travel": travel,
        "hold": hold,
        "pullback": pullback,
        "end_card": end_card,
        "keys": keys,
        "pull_start": pull_start,
        "end_start": end_start,
        "fps": int(cfg.get("fps", 30)),
        "width": int(cfg.get("width", 1080)),
        "height": int(cfg.get("height", 1920)),
    }


# v3: drop C4 and above one octave (G4→G3, E4→E3, …) for a darker register
PITCH_CYCLE = ["D3", "G3", "A3", "C3", "E3", "D3", "G3"]


def build_plucks(sched):
    plucks = [
        {"time": 0.12, "pitch": "G3", "amp": 0.34, "dur": 3.8},
        {"time": 1.00, "pitch": "D3", "amp": 0.16, "dur": 3.0},
    ]
    for i, k in enumerate(sched["keys"]):
        pitch = PITCH_CYCLE[i % len(PITCH_CYCLE)]
        amp = 0.40 if i in (1, 4) else 0.32
        plucks.append({"time": k["arrive"], "pitch": pitch, "amp": amp, "dur": 3.6})
        if i == 1:
            plucks.append({"time": k["arrive"], "pitch": "G3", "amp": 0.18, "dur": 4.0})
    plucks.append({"time": sched["pull_start"], "pitch": "C3", "amp": 0.22, "dur": 3.2})
    plucks.append({"time": sched["end_start"], "pitch": "G2", "amp": 0.40, "dur": 4.6})
    plucks.append({"time": sched["end_start"] + 2.0, "pitch": "D3", "amp": 0.16, "dur": 3.5})
    return plucks


def write_timeline(path: Path, sched, highlights, plucks):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"time": 0.0, "kind": "section", "video": "B", "name": "full_open", "extra": ""}]
    for i, (k, h) in enumerate(zip(sched["keys"], highlights)):
        rows.append(
            {
                "time": k["travel_start"],
                "kind": "visual",
                "video": "B",
                "name": f"travel_{h.get('char', i)}",
                "extra": "",
            }
        )
        rows.append(
            {
                "time": k["arrive"],
                "kind": "caption",
                "video": "B",
                "name": h.get("char", ""),
                "extra": h.get("note", ""),
            }
        )
    rows.append({"time": sched["pull_start"], "kind": "section", "video": "B", "name": "pullback", "extra": ""})
    rows.append({"time": sched["end_start"], "kind": "section", "video": "B", "name": "end_card", "extra": ""})
    for ev in plucks:
        rows.append({"time": ev["time"], "kind": "pluck", "video": "B", "name": ev["pitch"], "extra": f"amp={ev['amp']}"})
    rows.sort(key=lambda r: (float(r["time"]), r["kind"]))
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["time", "kind", "video", "name", "extra"])
        w.writeheader()
        for r in rows:
            w.writerow(r)


class CloseUp:
    def __init__(self, cfg, work: np.ndarray):
        self.cfg = cfg
        self.work = work
        self.H, self.W = work.shape[:2]
        self.highlights = _highlights(cfg, self.W, self.H)
        if not self.highlights:
            raise ValueError("config.highlights 不能为空")
        self.sched = build_schedule(cfg, len(self.highlights))
        self.fw = self.sched["width"]
        self.fh = self.sched["height"]
        self.bg = rgb_to_image(make_background(work, self.fw, self.fh, blur=61, dark=0.15))
        self.vig = make_vignette(self.fw, self.fh, strength=0.48, power=1.85)
        self.title = cfg.get("title", "")
        self.subtitle = cfg.get("subtitle", "")
        self.author = cfg.get("author", "作者名 · 书")
        self.author_note = cfg.get("author_note", "落款占位，可替换")
        self.seal_char = cfg.get("seal_char", "书")
        self.header = text_image(self.title, font(28, bold=False), GOLD + (255,), tracking=6)

        # stage aspect used when padding focus boxes
        stage_w, stage_h = self.fw, STAGE_BOT - STAGE_TOP
        self.stage_aspect = stage_w / stage_h  # used only as a starting point
        # captions prefer a slightly taller crop than the full stage so the character is large
        self.zoom_aspect = self.fw / (STAGE_BOT - STAGE_TOP)

        self.full_rect = (0.0, 0.0, float(self.W), float(self.H))
        self.focus_rects = []
        for h in self.highlights:
            x0, y0, x1, y1 = h["boxn"]
            px = (x0 * self.W, y0 * self.H, x1 * self.W, y1 * self.H)
            # modest pad, then match a portrait-leaning aspect so the char is large
            # Use a slightly tighter aspect for single characters, wider for "area" shots.
            # Keep the box's own proportions (plus a little paper margin)
            # so a tall column is not inflated sideways into its neighbours.
            bw = max(px[2] - px[0], 1.0)
            bh = max(px[3] - px[1], 1.0)
            aspect = bw / bh
            rect = pad_rect_to_aspect(*px, aspect=aspect, img_w=self.W, img_h=self.H, pad=0.18)
            self.focus_rects.append(rect)

        self.captions = []
        for h in self.highlights:
            ch = h.get("char") or ""
            note = h.get("note") or ""
            label = text_image(f"「{ch}」", font(52, bold=True), CREAM + (255,), tracking=4)
            note_im = text_image(note, font(28, bold=False), GOLD + (255,), tracking=1)
            self.captions.append((label, note_im))

    def _camera_at(self, t):
        s = self.sched
        # open: very slow gentle push on the full work
        if t <= s["open_hold"]:
            p = ease_in_out(t / max(0.01, s["open_hold"])) * 0.15
            z = 1.0 + 0.03 * p
            vw, vh = self.W / z, self.H / z
            x0 = (self.W - vw) / 2
            y0 = (self.H - vh) / 2 * 0.9
            return (x0, y0, x0 + vw, y0 + vh)

        # travel / hold through highlights
        for i, k in enumerate(s["keys"]):
            if t < k["arrive"]:
                # traveling from previous rect to this focus
                prev = self.focus_rects[i - 1] if i > 0 else self._open_end_rect()
                u = ease_in_out((t - k["travel_start"]) / max(0.01, k["arrive"] - k["travel_start"]))
                return lerp_rect(prev, self.focus_rects[i], u)
            if t <= k["leave"] or (i == len(s["keys"]) - 1 and t < s["pull_start"]):
                # hold (tiny breathing zoom)
                local = t - k["arrive"]
                hold_len = max(0.01, k["leave"] - k["arrive"])
                breath = 1.0 + 0.012 * np.sin(np.pi * min(1.0, local / hold_len))
                r = self.focus_rects[i]
                cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
                w, h = (r[2] - r[0]) / breath, (r[3] - r[1]) / breath
                return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

        # pull back to full
        last = self.focus_rects[-1]
        if t < s["end_start"]:
            u = ease_in_out((t - s["pull_start"]) / max(0.01, s["pullback"]))
            return lerp_rect(last, self.full_rect, u)
        return self.full_rect

    def _open_end_rect(self):
        z = 1.03
        vw, vh = self.W / z, self.H / z
        x0 = (self.W - vw) / 2
        y0 = (self.H - vh) / 2 * 0.9
        return (x0, y0, x0 + vw, y0 + vh)

    def _place(self, src_w, src_h):
        stage_w = self.fw
        stage_h = STAGE_BOT - STAGE_TOP
        scale = min(stage_w / max(src_w, 1e-6), stage_h / max(src_h, 1e-6))
        dw, dh = src_w * scale, src_h * scale
        dx = (self.fw - dw) / 2
        dy = STAGE_TOP + (stage_h - dh) / 2
        return dx, dy, dw, dh

    def frame(self, t: float) -> Image.Image:
        s = self.sched
        cam = self._camera_at(t)
        src_w, src_h = cam[2] - cam[0], cam[3] - cam[1]
        dx, dy, dw, dh = self._place(src_w, src_h)
        work_im = crop_and_resize(self.work, cam, dw, dh)

        canvas = self.bg.convert("RGBA")
        header_op = 1.0
        if t >= s["end_start"]:
            header_op = 1.0 - fade(t, s["end_start"], s["end_start"] + 0.6)
        # hide header while captions are up so they don't compete, keep it on open/pull
        cap_busy = any(
            k["arrive"] - 0.1 <= t <= k["leave"] + 0.15 for k in s["keys"]
        )
        if cap_busy:
            header_op *= 0.0
        if header_op > 0.02:
            canvas = paste_centered(canvas, self.header, self.fw // 2, 128, header_op)

        canvas = composite_work(canvas, work_im, dx, dy, shadow=True)

        # captions
        for i, k in enumerate(s["keys"]):
            op = hold_fade(t, k["arrive"] + 0.12, k["leave"] - 0.12, fade_in=0.32, fade_out=0.28)
            if op <= 0.02:
                continue
            label, note = self.captions[i]
            gap = 8
            total_h = label.height + gap + note.height
            y_mid = int((CAPTION_TOP + CAPTION_BOT) / 2)
            y0 = y_mid - total_h // 2
            canvas = paste_centered(canvas, label, self.fw // 2, y0 + label.height // 2, op)
            canvas = paste_centered(canvas, note, self.fw // 2, y0 + label.height + gap + note.height // 2, op)

        end_op = fade(t, s["end_start"], s["end_start"] + 0.8)
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

        rgb = apply_vignette(np.array(canvas.convert("RGB")), self.vig)
        return Image.fromarray(rgb)


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
    world = CloseUp(cfg, work)
    sched = world.sched
    plucks = build_plucks(sched)
    if timeline_path:
        write_timeline(timeline_path, sched, world.highlights, plucks)
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
    p = argparse.ArgumentParser(description="书法作品「逐字特写」模板")
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
