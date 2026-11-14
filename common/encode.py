#!/usr/bin/env python3
"""ffmpeg 帧编码。probe/联系表/取帧统一在 videokit（曾三处重复实现，已收敛）。"""
from __future__ import annotations

import subprocess
from pathlib import Path


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
