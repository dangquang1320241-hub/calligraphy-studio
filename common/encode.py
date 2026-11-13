#!/usr/bin/env python3
"""ffmpeg encode, probe, decode test, and contact-sheet helpers."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def render_frames_to_mp4(frame_iter, n_frames, fps, w, h, audio_path, out_path, duration=None, crf=17):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp.mp4")
    if tmp.exists():
        tmp.unlink()
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{w}x{h}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-i",
        str(audio_path),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-t",
        str(duration if duration is not None else n_frames / fps),
        str(tmp),
    ]
    # stderr 进临时文件而非 PIPE：ffmpeg -stats 会持续写 stderr，写帧期间无人排空管道
    # 会在 64KB 写满后与 stdin 写入互锁（长渲染死锁）；stdout 弃之（无信息量）。
    import tempfile
    with tempfile.TemporaryFile() as err_f:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err_f)
        assert proc.stdin is not None
        write_err = None
        try:
            for i, frame in enumerate(frame_iter):
                if isinstance(frame, (bytes, bytearray)):
                    proc.stdin.write(frame)
                else:
                    proc.stdin.write(frame.tobytes())
                if (i + 1) % 30 == 0 or i == 0:
                    print(f"  frame {i+1}/{n_frames}", flush=True)
        except BrokenPipeError as e:
            write_err = e
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.wait()
        err_f.seek(0)
        stderr = err_f.read()
        if proc.returncode != 0:
            err = stderr[-4000:].decode("utf-8", "replace") if stderr else str(write_err)
            tmp.unlink(missing_ok=True)  # 失败不残留半成品
            raise RuntimeError(f"ffmpeg failed (code {proc.returncode}):\n{err}")
        tmp.replace(out_path)
        return out_path


def ffprobe_check(path: Path) -> dict:
    path = Path(path)
    v = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,nb_read_frames,codec_name",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    a = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size",
            "-show_entries",
            "stream=codec_type,codec_name",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    import json

    vj = json.loads(v.stdout)
    aj = json.loads(a.stdout)
    return {"video": vj, "format": aj}


def decode_test(path: Path) -> bool:
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or r.stderr.strip():
        print("DECODE issues:", r.stderr)
        return False
    print("DECODE_OK")
    return True


def extract_frames(path: Path, times, out_dir: Path, prefix="frame"):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, t in enumerate(times):
        dest = out_dir / f"{prefix}_{i:02d}_{t:.2f}s.png"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{t:.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(dest),
            ],
            check=True,
            capture_output=True,
        )
        if not dest.exists():
            # ffmpeg -ss 越过视频末尾时以 0 退出但不出文件——不校验会把不存在路径传给下游
            raise RuntimeError(f"取帧失败 t={t:.2f}s（越界或解码失败）：{path.name}")
        paths.append(dest)
    return paths


def contact_sheet(frame_paths, out_path: Path, cols=4, thumb_w=270, labels=None, font_path=None):
    from PIL import Image, ImageDraw, ImageFont

    frames = [Image.open(p).convert("RGB") for p in frame_paths]
    if not frames:
        raise ValueError("no frames")
    aspect = frames[0].height / frames[0].width
    tw = thumb_w
    th = int(tw * aspect)
    n = len(frames)
    cols = min(cols, n)
    rows = (n + cols - 1) // cols
    pad = 8
    label_h = 28
    W = cols * tw + (cols + 1) * pad
    H = rows * (th + label_h) + (rows + 1) * pad
    sheet = Image.new("RGB", (W, H), (28, 24, 22))
    draw = ImageDraw.Draw(sheet)
    try:
        fnt = ImageFont.truetype(
            font_path or "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
            16,
            index=2,
        )
    except Exception:
        fnt = ImageFont.load_default()
    for i, im in enumerate(frames):
        r, c = divmod(i, cols)
        x = pad + c * (tw + pad)
        y = pad + r * (th + label_h + pad)
        thumb = im.resize((tw, th), Image.Resampling.LANCZOS)
        sheet.paste(thumb, (x, y))
        label = labels[i] if labels else Path(frame_paths[i]).stem
        draw.text((x + 2, y + th + 4), str(label), fill=(230, 220, 200), font=fnt)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=92)
    return out_path
