#!/usr/bin/env python3
"""环境声合成（新增模块，纯代码、固定种子、无采样）：雨声、翻纸声、毛笔擦纸声，以及多层混音+响度归一。

  from ambient import rain, paper_rustle, brush_swish, place, master
所有函数返回 (N,2) float32，采样率默认 48000。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

SR = 48000


def _stereo(x, pan=0.5):
    l, r = np.cos(pan * np.pi / 2), np.sin(pan * np.pi / 2)
    return np.stack([x * l, x * r], 1).astype(np.float32)


def _pink(n, rng):
    # Voss-ish：白噪声 1/f 频谱整形
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(len(X)); f[0] = 1
    X /= np.sqrt(f)
    y = np.fft.irfft(X, n)
    return y / (np.abs(y).max() + 1e-9)


def rain(duration, sr=SR, seed=11, intensity=0.6, drops_per_s=26):
    """柔和的屋檐细雨：低通粉噪声底 + 随机小雨滴（短促带通脉冲），左右独立。"""
    rng = np.random.default_rng(seed)
    n = int(duration * sr)
    out = np.zeros((n, 2), np.float32)
    for c in range(2):
        bed = _pink(n, rng)
        bed = sosfilt(butter(2, [250, 5000], btype="band", fs=sr, output="sos"), bed)
        swell = 0.85 + 0.15 * np.sin(2 * np.pi * np.arange(n) / sr / (13 + 4 * c) + c)
        out[:, c] += (bed * swell * 0.35 * intensity).astype(np.float32)
    nd = int(duration * drops_per_s)
    for _ in range(nd):
        t0 = rng.uniform(0, duration - 0.05)
        L = int(sr * rng.uniform(0.006, 0.02))
        tt = np.arange(L) / sr
        f = rng.uniform(1800, 5200)
        g = rng.uniform(0.04, 0.16) * intensity
        blip = np.sin(2 * np.pi * f * tt * (1 - tt * 8)) * np.exp(-tt * rng.uniform(250, 500)) * g
        s = int(t0 * sr)
        pan = rng.uniform(0.15, 0.85)
        out[s:s + L] += _stereo(blip, pan)[: n - s]
    return out


def paper_rustle(dur=0.9, sr=SR, seed=3, gain=0.5):
    """翻纸/抚纸：带通噪声，若干个快速起伏的小包络。"""
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    x = rng.standard_normal(n)
    x = sosfilt(butter(2, [900, 7000], btype="band", fs=sr, output="sos"), x)
    t = np.arange(n) / sr
    env = np.zeros(n)
    for _ in range(rng.integers(5, 9)):
        c = rng.uniform(0.05, dur * 0.85); w = rng.uniform(0.02, 0.09)
        env += np.exp(-((t - c) / w) ** 2) * rng.uniform(0.4, 1.0)
    env *= np.clip(t / 0.03, 0, 1) * np.clip((dur - t) / 0.12, 0, 1)
    y = x * env
    return _stereo(y / (np.abs(y).max() + 1e-9) * gain, rng.uniform(0.35, 0.65))


def brush_swish(dur=0.5, sr=SR, seed=5, gain=0.18):
    """毛笔擦过宣纸的轻微沙声：低通噪声、慢起快收。"""
    rng = np.random.default_rng(seed)
    n = int(max(0.05, dur) * sr)
    x = sosfilt(butter(2, [300, 2600], btype="band", fs=sr, output="sos"), rng.standard_normal(n))
    t = np.arange(n) / sr
    env = np.clip(t / (dur * 0.25 + 1e-3), 0, 1) ** 0.7 * np.clip((dur - t) / (dur * 0.35 + 1e-3), 0, 1)
    env *= 0.7 + 0.3 * np.sin(2 * np.pi * rng.uniform(5, 9) * t) ** 2
    y = x * env
    return _stereo(y / (np.abs(y).max() + 1e-9) * gain, rng.uniform(0.4, 0.6))


def place(bus, sig, t0, sr=SR, gain=1.0):
    s = int(t0 * sr)
    if s >= len(bus):
        return bus
    e = min(len(bus), s + len(sig))
    bus[s:e] += sig[: e - s] * gain
    return bus


def fade_edges(x, sr=SR, fin=1.5, fout=2.5):
    n = len(x); t = np.arange(n) / sr
    env = np.clip(t / max(fin, 1e-3), 0, 1) * np.clip((n / sr - t) / max(fout, 1e-3), 0, 1)
    return (x * env[:, None]).astype(np.float32)


def master(layers, out_wav, sr=SR, target_lufs=-16.0):
    """layers: [(signal(N,2), gain_db), ...] → 求和、软限幅、写 wav，再用 audio.loudnorm 两遍归一。"""
    n = max(len(s) for s, _ in layers)
    bus = np.zeros((n, 2), np.float64)
    for s, db in layers:
        bus[: len(s)] += s * 10 ** (db / 20)
    peak = np.abs(bus).max() + 1e-9
    bus = bus / peak * 0.89
    out_wav = Path(out_wav)
    raw = out_wav.with_suffix(".premaster.wav")
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(raw), sr, bus.astype(np.float32))
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from audio import loudnorm
    return loudnorm(raw, out_wav, target_lufs=target_lufs)
