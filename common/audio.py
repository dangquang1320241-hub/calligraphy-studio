#!/usr/bin/env python3
"""Synthesize a calm guqin-like pentatonic ambience (Karplus-Strong + pad + reverb).

Original music, no third-party samples. Deterministic given the same seed and timeline.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.signal import butter, fftconvolve, lfilter, sosfilt

SEED = 20261110
FS = 44100

# G-centered Chinese pentatonic (宫 商 角 徵 羽 relative to G)
PITCH = {
    "G2": 98.00,
    "A2": 110.00,
    "C3": 130.81,
    "D3": 146.83,
    "E3": 164.81,
    "G3": 196.00,
    "A3": 220.00,
    "C4": 261.63,
    "D4": 293.66,
    "E4": 329.63,
    "G4": 392.00,
    "A4": 440.00,
}


def pitch_hz(p) -> float:
    if isinstance(p, (int, float)):
        return float(p)
    return float(PITCH[str(p)])


def _frac_delay_karplus(freq, dur, fs, brightness=0.985, decay=0.996, seed=SEED, amp=0.5,
                        attack_s=0.004, excitation="bright"):
    """Karplus-Strong with fractional delay interpolation."""
    n = int(round(dur * fs))
    if n <= 0:
        return np.zeros(1, dtype=np.float64)
    delay = fs / max(freq, 30.0)
    delay_i = max(2, int(np.floor(delay)))
    frac = delay - delay_i
    rng = np.random.RandomState(int(seed + freq * 10) & 0x7FFFFFFF)
    buf = rng.randn(delay_i + 1).astype(np.float64)
    # slightly high-passed burst, windowed
    buf -= buf.mean()
    if excitation == "bright":
        # legacy: simple 1-zero highpass (very clicky / zingy attack)
        buf = np.concatenate([[0.0], np.diff(buf)])
    else:
        # dark: gentle DC block + lowpass so the string starts with body, not a tick
        buf = lfilter([1.0, -0.95], [1.0], buf)
        buf = sosfilt(butter(2, 1400, btype="low", fs=fs, output="sos"), buf)
    buf *= np.hanning(len(buf))
    buf *= amp
    out = np.zeros(n, dtype=np.float64)
    idx = 0
    prev = 0.0
    length = delay_i
    for i in range(n):
        # linear interpolation on the delay line read
        i0 = idx
        i1 = (idx + 1) % length
        s = buf[i0] * (1.0 - frac) + buf[i1] * frac
        # lowpass (average with previous) * decay
        cur = decay * (brightness * 0.5 * (s + prev) + (1.0 - brightness) * s)
        prev = s
        buf[idx] = cur
        out[i] = cur
        idx = (idx + 1) % length
    # short fade-in to avoid click (attack_s default 4 ms = legacy)
    fade = min(int(max(attack_s, 0.0) * fs), n)
    if fade > 0:
        out[:fade] *= np.linspace(0, 1, fade)
    # natural tail already decays; ensure end is quiet
    fade_out = min(int(0.08 * fs), n)
    if fade_out > 0:
        out[-fade_out:] *= np.linspace(1, 0, fade_out)
    return out


def karplus_pluck(freq, dur, fs=FS, amp=0.45, seed=SEED, brightness=None,
                 octave_amp=0.12, attack_s=0.004, excitation="bright", lowpass_hz=None):
    # lower notes: longer, darker; higher: brighter, shorter body
    if brightness is None:
        brightness = float(np.clip(0.975 + (freq - 196) / 4000, 0.96, 0.992))
    decay = float(np.clip(0.994 + (120 - min(freq, 200)) / 8000, 0.988, 0.999))
    ks_kw = dict(attack_s=attack_s, excitation=excitation)
    # two slightly detuned strings for body
    a = _frac_delay_karplus(freq, dur, fs, brightness, decay, seed, amp, **ks_kw)
    b = _frac_delay_karplus(freq * 1.0018, dur, fs, brightness, decay, seed + 17, amp * 0.55, **ks_kw)
    # a hint of octave harmonic, very quiet
    c = _frac_delay_karplus(freq * 2.003, dur * 0.7, fs, min(0.99, brightness + 0.01), decay * 0.997, seed + 31, amp * float(octave_amp), **ks_kw)
    out = a + b
    if len(c) < len(out):
        tmp = np.zeros_like(out)
        tmp[: len(c)] = c
        c = tmp
    out = out + c
    # mild low-shelf / body resonance around 180-350 Hz via a peaking biquad-ish one-pole
    # y[n] = x + a*(x[n]-x[n-1]) already did HP; now add a dark body
    body = lfilter([1.0], [1.0, -0.965], out) * (1 - 0.965)
    mixed = 0.78 * out + 0.22 * body
    if lowpass_hz:
        mixed = sosfilt(butter(2, float(lowpass_hz), btype="low", fs=fs, output="sos"), mixed)
    # peak normalize this pluck only
    peak = np.max(np.abs(mixed)) + 1e-9
    mixed = mixed / peak * amp
    return mixed.astype(np.float64)


def make_pad(duration, fs=FS, root=98.0, amp=0.045, seed=SEED):
    n = int(round(duration * fs))
    t = np.arange(n) / fs
    rng = np.random.RandomState(seed)
    # slow beating partials on a G drone + fifth + upper fifth
    freqs = [root, root * 1.5, root * 2, root * 3, root * 2 * 1.498, root * 4 / 3]
    amps = [1.0, 0.45, 0.28, 0.12, 0.10, 0.08]
    sig = np.zeros(n, dtype=np.float64)
    for f, a, k in zip(freqs, amps, range(len(freqs))):
        det = 1.0 + rng.uniform(-0.0012, 0.0012)
        phase = rng.uniform(0, 2 * np.pi)
        # very slow amplitude drift
        lfo = 0.75 + 0.25 * np.sin(2 * np.pi * (0.03 + 0.01 * k) * t + phase)
        sig += a * lfo * np.sin(2 * np.pi * f * det * t + phase)
    # soft attack over 2s, release handled later
    att = min(n, int(2.0 * fs))
    env = np.ones(n)
    env[:att] = np.linspace(0, 1, att) ** 1.4
    sig *= env
    peak = np.max(np.abs(sig)) + 1e-9
    return (sig / peak * amp).astype(np.float64)


def make_reverb_ir(fs=FS, rt60=2.1, seed=SEED, damp_hz=None):
    rng = np.random.RandomState(seed + 99)
    length = int(rt60 * 3 * fs)
    t = np.arange(length) / fs
    # noise IR with exponential decay and high-frequency damping
    ir = rng.randn(length).astype(np.float64)
    tau = rt60 / 6.91
    ir *= np.exp(-t / tau)
    # early reflections (a few sparse taps)
    early = np.zeros(length, dtype=np.float64)
    for delay_ms, gain in ((18, 0.35), (27, 0.22), (41, 0.18), (59, 0.12), (88, 0.08), (120, 0.05)):
        d = int(delay_ms * 0.001 * fs)
        if d < length:
            early[d] += gain
    early = lfilter([0.3, 0.7], [1.0], early)
    ir = 0.35 * early + 0.65 * ir
    # damp highs (legacy mild one-pole; optional extra lowpass when damp_hz is set)
    ir = lfilter([0.35, 0.65], [1.0], ir)
    if damp_hz:
        ir = sosfilt(butter(2, float(damp_hz), btype="low", fs=fs, output="sos"), ir)
    ir /= np.sqrt(np.sum(ir ** 2) + 1e-12)
    return ir


def apply_reverb(x, fs=FS, rt60=2.1, mix=0.38, seed=SEED, damp_hz=None):
    ir = make_reverb_ir(fs, rt60, seed, damp_hz=damp_hz)
    wet = fftconvolve(x, ir, mode="full")[: len(x)]
    return (1.0 - mix) * x + mix * wet


def high_shelf(x, fs, freq=3500.0, gain_db=0.0):
    """RBJ high-shelf biquad. gain_db=0 is a no-op. Works on mono or (N, C) arrays."""
    if abs(float(gain_db)) < 1e-9:
        return x
    A = 10 ** (float(gain_db) / 40.0)
    w0 = 2 * np.pi * float(freq) / fs
    cosw, sinw = np.cos(w0), np.sin(w0)
    alpha = sinw / 2.0 * np.sqrt(2.0)  # S=1
    b0 = A * ((A + 1) + (A - 1) * cosw + 2 * np.sqrt(A) * alpha)
    b1 = -2 * A * ((A - 1) + (A + 1) * cosw)
    b2 = A * ((A + 1) + (A - 1) * cosw - 2 * np.sqrt(A) * alpha)
    a0 = (A + 1) - (A - 1) * cosw + 2 * np.sqrt(A) * alpha
    a1 = 2 * ((A - 1) - (A + 1) * cosw)
    a2 = (A + 1) - (A - 1) * cosw - 2 * np.sqrt(A) * alpha
    b = np.array([b0, b1, b2], dtype=np.float64) / a0
    a = np.array([1.0, a1 / a0, a2 / a0], dtype=np.float64)
    if x.ndim == 1:
        return lfilter(b, a, x)
    return np.stack([lfilter(b, a, x[:, c]) for c in range(x.shape[1])], axis=1)


def tone_settings(tone: str = "legacy") -> dict:
    """Named mix/synth presets. Unknown or 'legacy' returns {} (original behaviour)."""
    if tone in ("warm", "dark", "soft"):
        return {
            "pluck_opts": {
                "excitation": "dark",
                "attack_s": 0.016,
                "octave_amp": 0.04,
                "lowpass_hz": 2800.0,
            },
            "reverb_damp_hz": 2500.0,
            "high_shelf_hz": 3500.0,
            "high_shelf_db": -5.0,
            # louder low drone so the now-fuller pluck body does not dwarf the pad
            "pad_amp": 0.16,
        }
    return {}


def synth_ambience(duration, plucks, fs=FS, seed=SEED, fade_in=0.9, fade_out=1.6,
                   pluck_opts=None, reverb_damp_hz=None, high_shelf_hz=3500.0, high_shelf_db=0.0,
                   pad_amp=0.042):
    """plucks: iterable of dicts with keys time, pitch, amp, dur (optional)."""
    n = int(round(duration * fs))
    acc = np.zeros(n, dtype=np.float64)
    pad = make_pad(duration, fs, root=PITCH["G2"], amp=float(pad_amp), seed=seed)
    acc += pad
    for i, ev in enumerate(plucks):
        t0 = float(ev["time"])
        if t0 >= duration:
            continue
        freq = pitch_hz(ev["pitch"])
        amp = float(ev.get("amp", 0.38))
        dur = float(ev.get("dur", 3.6))
        start = int(round(t0 * fs))
        opts = dict(pluck_opts or {})
        wave = karplus_pluck(freq, dur, fs=fs, amp=amp, seed=seed + i * 13, **opts)
        end = min(n, start + len(wave))
        acc[start:end] += wave[: end - start]

    acc = apply_reverb(acc, fs=fs, rt60=2.15, mix=0.36, seed=seed, damp_hz=reverb_damp_hz)
    # fades
    fi = min(n, int(fade_in * fs))
    fo = min(n, int(fade_out * fs))
    if fi > 0:
        acc[:fi] *= np.linspace(0, 1, fi)
    if fo > 0:
        acc[-fo:] *= np.linspace(1, 0, fo)
    if high_shelf_db:
        acc = high_shelf(acc, fs, freq=high_shelf_hz, gain_db=high_shelf_db)
    # safety peak
    peak = np.max(np.abs(acc)) + 1e-12
    if peak > 0.85:
        acc = acc / peak * 0.85
    return acc.astype(np.float32), fs


def write_wav(path: Path, audio: np.ndarray, fs: int = FS):
    import wave
    import struct

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    x = np.clip(audio, -1.0, 1.0)
    if x.ndim == 1:
        stereo = np.stack([x, x], axis=1)
    else:
        stereo = x
    pcm = (stereo * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(pcm.tobytes())


def loudnorm(in_wav: Path, out_wav: Path, target_lufs: float = -16.0, tp: float = -1.5):
    """Two-pass EBU R128 loudnorm via ffmpeg."""
    in_wav, out_wav = Path(in_wav), Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    # Gentle compression first so a few loud plucks do not cap integrated loudness.
    prep = in_wav.with_suffix(".comp.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(in_wav),
            "-af",
            "acompressor=threshold=-28dB:ratio=3:attack=20:release=200:makeup=8,alimiter=limit=0.89",
            str(prep),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    in_wav = prep
    meas = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(in_wav),
            "-af",
            f"loudnorm=I={target_lufs}:TP={tp}:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    # json is at the end of stderr
    err = meas.stderr
    start = err.rfind("{")
    end = err.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError("loudnorm did not print measurement JSON")
    info = json.loads(err[start : end + 1])
    filt = (
        f"loudnorm=I={target_lufs}:TP={tp}:LRA=11"
        f":measured_I={info['input_i']}"
        f":measured_TP={info['input_tp']}"
        f":measured_LRA={info['input_lra']}"
        f":measured_thresh={info['input_thresh']}"
        f":offset={info['target_offset']}"
        f":linear=true:print_format=summary"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(in_wav), "-af", filt, "-ar", "44100", str(out_wav)],
        check=True,
        capture_output=True,
        text=True,
    )
    return info


def render_music(plucks, duration, out_wav: Path, target_lufs: float = -16.0, seed: int = SEED,
                tone: str = "legacy", **synth_kw):
    """tone='legacy' keeps the original bright mix. tone='warm' darkens plucks, damps
    the reverb hiss, and applies a gentle high shelf (see tone_settings). Extra kwargs
    are passed to synth_ambience and override the preset."""
    settings = tone_settings(tone)
    settings.update(synth_kw)
    audio, fs = synth_ambience(duration, plucks, fs=FS, seed=seed, **settings)
    raw = Path(out_wav).with_suffix(".raw.wav")
    write_wav(raw, audio, fs)
    info = loudnorm(raw, out_wav, target_lufs=target_lufs)
    return info
