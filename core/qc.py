#!/usr/bin/env python3
"""统一 QC：音画对位核验（渲染器自带的 ffprobe+联系表之外，附加的自动核验）。"""
import csv
import json
from pathlib import Path

import numpy as np
from scipy.io import wavfile

SR = 48000


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
    rows = list(csv.DictReader(open(project_dir / "timeline.csv", encoding="utf-8")))
    vo = [(float(r["t_sec"]), r["id"]) for r in rows if r["kind"] == "vo"]
    ends = {r["id"].replace("sub_spoken", "vo"): float(r["t_sec"])
            for r in rows if r["kind"] == "subtitle" and r["id"].startswith("sub_spoken")}
    report = []
    for t, seg in vo:
        end = ends.get(seg, t + 3)
        a, b = int(t / 0.05), int(end / 0.05)
        inner = float(env[a:b].max()) if b > a else 0.0
        pre = float(env[max(0, a - 5):a].mean()) if a > 0 else 0.0
        post = float(env[b:b + 5].mean())
        ok = inner > 0.02 and inner > 3 * max(pre, post, 1e-4)
        report.append({"seg": seg, "window": [t, end], "峰值RMS": round(inner, 3),
                       "窗外均RMS": round(max(pre, post), 4), "对位": "✓" if ok else "✗"})
    out = project_dir / "检查记录" / "音频对位核验.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report
