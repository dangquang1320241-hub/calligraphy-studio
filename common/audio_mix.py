#!/usr/bin/env python3
"""共用混音：底乐压低 + 人声处闪避（侧链式包络，纯 numpy，可复现）。
mix(voice, music, sr, music_db=-20, duck_db=-6, attack=0.25, release=0.6) -> (N,2) float32
voice 可为 None（无朗读版本时只压低底乐）。
"""
import numpy as np


def _smooth_env(x, sr, attack, release):
    # 逐块（10ms）计算 RMS，再做不对称平滑
    hop = int(sr * 0.01)
    n = len(x)
    blocks = int(np.ceil(n / hop))
    pad = np.zeros(blocks * hop)
    pad[:n] = x
    rms = np.sqrt((pad.reshape(blocks, hop) ** 2).mean(1))
    gate = (rms > 10 ** (-45 / 20)).astype(float)
    a_up = np.exp(-0.01 / attack)
    a_dn = np.exp(-0.01 / release)
    env = np.zeros(blocks)
    # 先向前看（提前开始闪避，避免人声开头被音乐盖住）
    look = int(attack / 0.01)
    gate = np.maximum.reduce([np.roll(gate, -k) for k in range(look + 1)])
    e = 0.0
    for i, g in enumerate(gate):
        a = a_up if g > e else a_dn
        e = a * e + (1 - a) * g
        env[i] = e
    return np.repeat(env, hop)[:n]


def mix(voice, music, sr, music_db=-20.0, duck_db=-6.0, attack=0.25, release=0.6, voice_db=0.0):
    n = len(music) if voice is None else max(len(music), len(voice))
    out = np.zeros((n, 2), dtype=np.float64)
    m = np.zeros((n, 2))
    m[:len(music)] = music if music.ndim == 2 else music[:, None]
    g = np.full(n, 10 ** (music_db / 20))
    if voice is not None:
        v = voice if voice.ndim == 1 else voice.mean(1)
        vv = np.zeros(n)
        vv[:len(v)] = v
        env = _smooth_env(vv, sr, attack, release)
        g *= 10 ** (duck_db * env / 20)
        out += (vv * 10 ** (voice_db / 20))[:, None]
    out += m * g[:, None]
    return out.astype(np.float32)
