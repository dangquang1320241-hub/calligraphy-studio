#!/usr/bin/env python3
"""统一 QC：音画对位核验（渲染器自带的 ffprobe+联系表之外，附加的自动核验）。"""
import csv
import json
from pathlib import Path

import numpy as np
from scipy.io import wavfile

SR = 48000


def sheet_for(mp4: Path, out_dir: Path, n: int = 6, title: str = "") -> Path:
    """通用关键帧联系表（渲染器没自带时兜底）。"""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
    from videokit import contact_sheet, grab_frames  # noqa: E402
    import json as _json
    dur = float(ffprobe_check(mp4)["duration"] or 0)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    hi = max(0.0, dur - 0.2)  # 取帧时刻钳在末尾前，防 -ss 越界拿不到帧
    times = [min(round(dur * (i + 0.5) / n, 1), hi) for i in range(n)]
    frames = grab_frames(str(mp4), times, out_dir / "frames")
    labels = [f"{t:.1f}s" for t in times]
    return contact_sheet(frames, labels, out_dir / "编排联系表.jpg")


def ffprobe_check(mp4: Path) -> dict:
    """通用 ffprobe 校验（解码无错 + 时长 + 规格摘要），供无自带报告的渲染器兜底。"""
    import subprocess
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                        "-show_entries", "stream=width,height,r_frame_rate,nb_read_frames:format=duration",
                        "-of", "json", str(mp4)], capture_output=True, text=True)
    d = json.loads(p.stdout or "{}")
    streams = d.get("streams") or [{}]
    s = streams[0]
    err = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp4), "-f", "null", "-"],
                         capture_output=True, text=True).stderr.strip()
    return {"file": str(mp4), "width": s.get("width"), "height": s.get("height"),
            "fps": s.get("r_frame_rate"), "frames": s.get("nb_read_frames"),
            "duration": d.get("format", {}).get("duration"), "decode_ok": not err, "decode_errors": err[:400]}


def audio_align(project_dir: Path) -> list:
    """mix.wav 能量包络 vs timeline.csv 各句窗口：窗口内应有人声、窗口外仅底乐。"""
    project_dir = Path(project_dir)
    sr, y = wavfile.read(project_dir / "素材" / "mix.wav")
    assert sr == SR, f"采样率 {sr}≠{SR}"
    y = y.astype(np.float32) / 32768.0 if y.dtype == np.int16 else y.astype(np.float32)
    if y.ndim > 1:
        y = y.mean(1)
    win = int(0.05 * SR)
    n = len(y) // win
    env = np.sqrt((y[:n * win].reshape(n, win) ** 2).mean(1))
    with open(project_dir / "timeline.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    vo = [(float(r["t_sec"]), r["id"]) for r in rows if r["kind"] == "vo"]
    ends = {r["id"].replace("sub_spoken", "vo"): float(r["t_sec"])
            for r in rows if r["kind"] == "subtitle" and r["id"].startswith("sub_spoken")}
    report = []
    for t, seg in vo:
        end = ends.get(seg, t + 3)
        a, b = int(t / 0.05), int(end / 0.05)
        inner = float(env[a:b].max()) if b > a and a < len(env) else 0.0
        pre = float(env[max(0, a - 5):a].mean()) if 0 < a <= len(env) else 0.0  # 前窗同理不越界
        post = float(env[b:b + 5].mean()) if b < len(env) else 0.0  # 窗越界（音频末尾）不产生 NaN
        ok = inner > 0.02 and inner > 3 * max(pre, post, 1e-4)
        report.append({"seg": seg, "window": [t, end], "峰值RMS": round(inner, 3),
                       "窗外均RMS": round(max(pre, post), 4), "对位": "✓" if ok else "✗"})
    out = project_dir / "检查记录" / "音频对位核验.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report
