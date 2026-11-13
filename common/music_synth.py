#!/usr/bin/env python3
"""古风底乐合成（自制，纯代码，无采样）：低音持续音垫 + 古琴感拨弦（五声音阶，带滑音）+ 箫感气声长音 + 合成混响。
所有随机都用固定种子，同参数重复运行结果完全一致。产物版权归 Alaina 所有，可商用（见 README 许可记录）。

用法：python3 music_synth.py --duration 40 --out music.wav [--seed 7 --root 146.83 --bpm 56 --density 0.6 --xiao 1]
也可 import：from music_synth import synth; y = synth(40, sr=48000, **params)  # 返回 (N,2) float32
"""
import argparse, json
import numpy as np
from scipy.signal import fftconvolve, butter, sosfilt
from scipy.io import wavfile

# 五声音阶：宫 商 角 徵 羽（相对根音的半音数）
PENTA = [0, 2, 4, 7, 9]


def _pent_freq(root, degree):
    """degree: 整数音级（可负），映射到五声音阶多八度"""
    octv, idx = divmod(degree, 5)
    return root * 2 ** ((PENTA[idx] + 12 * octv) / 12)


def _pluck(f0, dur, sr, glide_to=None, glide_t=0.35, bright=1.0):
    """古琴感拨弦：加性谐波 + 指数衰减（高次谐波衰减更快）+ 可选按弦滑音 + 起音噪声"""
    n = int(dur * sr)
    t = np.arange(n) / sr
    f = np.full(n, f0, dtype=np.float64)
    if glide_to:
        ramp = np.clip((t - glide_t) / 0.25, 0, 1)
        ramp = ramp * ramp * (3 - 2 * ramp)
        f = f0 * (glide_to / f0) ** ramp
    phase = 2 * np.pi * np.cumsum(f) / sr
    y = np.zeros(n)
    for h in range(1, 9):
        amp = (1.0 / h ** 1.25) * (bright if h > 2 else 1.0)
        dec = 1.1 + 0.9 * h
        # 轻微不和谐度（弦的刚性）
        inh = 1 + 0.0004 * h * h
        y += amp * np.sin(h * inh * phase) * np.exp(-dec * t)
    att = np.clip(t / 0.004, 0, 1)
    noise = np.random.default_rng(int(f0 * 10)).standard_normal(n) * np.exp(-t * 60) * 0.08
    return (y * att + noise) * 0.5


def _xiao(f0, dur, sr, rng):
    """箫感：正弦+弱二次谐波，慢起慢收，轻微颤音，带滤波气声"""
    n = int(dur * sr)
    t = np.arange(n) / sr
    vib = 1 + 0.004 * np.sin(2 * np.pi * 4.8 * t) * np.clip((t - 0.6) / 0.8, 0, 1)
    phase = 2 * np.pi * np.cumsum(f0 * vib) / sr
    tone = np.sin(phase) + 0.12 * np.sin(2 * phase) + 0.04 * np.sin(3 * phase)
    sos = butter(2, [f0 * 0.8, f0 * 3.5], btype='band', fs=sr, output='sos')
    breath = sosfilt(sos, rng.standard_normal(n)) * 0.35
    env = np.clip(t / (dur * 0.35), 0, 1) ** 1.5 * np.clip((dur - t) / (dur * 0.4), 0, 1) ** 1.2
    return (tone + breath) * env * 0.22


def _pad(root, dur, sr):
    n = int(dur * sr)
    t = np.arange(n) / sr
    y = np.zeros(n)
    for f, a in [(root / 2, 0.5), (root / 2 * 1.5, 0.28), (root, 0.18)]:
        for det in (-0.6, 0.6):
            y += a * np.sin(2 * np.pi * (f + det) * t)
    swell = 0.75 + 0.25 * np.sin(2 * np.pi * t / 11.0 - np.pi / 2)
    sos = butter(2, 600, fs=sr, output='sos')
    return sosfilt(sos, y * swell) * 0.10


def _reverb_ir(sr, secs, rng):
    n = int(secs * sr)
    t = np.arange(n) / sr
    ir = np.zeros((n, 2))
    for c in range(2):
        ir[:, c] = rng.standard_normal(n) * np.exp(-t * 6.9 / secs)
    sos = butter(1, 3500, fs=sr, output='sos')
    ir = sosfilt(sos, ir, axis=0)
    ir[:int(0.012 * sr)] *= np.linspace(0, 1, int(0.012 * sr))[:, None]
    return ir / np.sqrt((ir ** 2).sum(0))


def synth(duration, sr=48000, seed=7, root=146.83, bpm=56, density=0.6, xiao=1.0,
          reverb_secs=3.2, wet=0.32, fade_in=2.0, fade_out=3.5, accents=None, accent_gain=0.9):
    rng = np.random.default_rng(seed)
    total = int((duration + reverb_secs) * sr)
    dry = np.zeros((total, 2))

    def add(sig, start, pan):
        s = int(start * sr)
        e = min(total, s + len(sig))
        if e <= s:
            return
        l, r = np.cos(pan * np.pi / 2), np.sin(pan * np.pi / 2)
        dry[s:e, 0] += sig[:e - s] * l
        dry[s:e, 1] += sig[:e - s] * r

    pad = _pad(root, duration + 1, sr)
    add(pad, 0, 0.5)

    beat = 60.0 / bpm
    # 拨弦乐句：每句 4~7 个音，句间留白 1.5~3 拍
    t = 1.2 * beat
    deg = 5  # 从中音区宫音开始
    while t < duration - 3 * beat:
        notes = rng.integers(4, 8)
        for k in range(notes):
            if t >= duration - 2 * beat:
                break
            if rng.random() > density + 0.25:
                t += beat
                continue
            step = rng.choice([-2, -1, -1, 1, 1, 2, 0], p=[.1, .22, .1, .22, .14, .1, .12])
            deg = int(np.clip(deg + step, 2, 10))
            f0 = _pent_freq(root, deg)
            glide = _pent_freq(root, deg + 1) if rng.random() < 0.18 else None
            ndur = beat * rng.choice([1, 1.5, 2, 3])
            vel = 0.55 + 0.35 * rng.random()
            add(_pluck(f0, ndur + 2.5, sr, glide_to=glide) * vel, t, 0.35 + 0.3 * rng.random())
            # 偶尔加泛音感八度双音
            if rng.random() < 0.15:
                add(_pluck(f0 * 2, ndur + 2, sr, bright=0.4) * vel * 0.35, t + 0.02, 0.6)
            t += ndur
        t += beat * rng.choice([1.5, 2, 3])

    # 箫：每 ~9 秒一个长音
    if xiao > 0:
        tx = 3.0 + rng.random() * 2
        while tx < duration - 5:
            d = 4.0 + rng.random() * 2.5
            f0 = _pent_freq(root, int(rng.choice([7, 8, 9, 10]))) 
            add(_xiao(f0, d, sr, rng) * xiao, tx, 0.55)
            tx += d + 4.5 + rng.random() * 3

    # 重音：在画面事件时刻（如换字）加一记低音宫音拨弦，让音画对齐
    for ta in (accents or []):
        if 0 <= ta < duration:
            add(_pluck(_pent_freq(root, 0), 4.0, sr, bright=0.6) * accent_gain, ta, 0.45)
            add(_pluck(_pent_freq(root, 5), 3.0, sr, bright=0.5) * accent_gain * 0.45, ta + 0.012, 0.55)

    ir = _reverb_ir(sr, reverb_secs, rng)
    wetsig = np.stack([fftconvolve(dry[:, c], ir[:, c])[:total] for c in range(2)], 1)
    y = dry * (1 - wet) + wetsig * wet * 1.6
    y = y[:int(duration * sr)]
    n = len(y)
    tt = np.arange(n) / sr
    env = np.clip(tt / fade_in, 0, 1) * np.clip((duration - tt) / fade_out, 0, 1)
    y *= env[:, None]
    y /= (np.abs(y).max() + 1e-9)
    return (y * 0.89).astype(np.float32)  # 峰值约 -1 dBFS，混音时再压低


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--duration', type=float, required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--sr', type=int, default=48000)
    ap.add_argument('--params', default='{}', help='JSON：seed/root/bpm/density/xiao/reverb_secs/wet')
    for k in ['seed', 'root', 'bpm', 'density', 'xiao']:
        ap.add_argument('--' + k, type=float)
    a = ap.parse_args()
    p = json.loads(a.params)
    for k in ['seed', 'root', 'bpm', 'density', 'xiao']:
        v = getattr(a, k)
        # accents 只能通过 --params 传入，如 '{"accents":[4.5,9.1]}'
        if v is not None:
            p[k] = int(v) if k == 'seed' else v
    y = synth(a.duration, sr=a.sr, **p)
    wavfile.write(a.out, a.sr, y)
    print('wrote', a.out, len(y) / a.sr, 's')


if __name__ == '__main__':
    main()
