#!/usr/bin/env python3
"""TTS 可替换接口（M1）：Provider 抽象 + edge-tts 默认实现 + 逐句缓存。

商用前替换实现：注册到 PROVIDERS（如 Azure / 本地 CosyVoice），渲染器不感知差异。
"""
import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

SR = 48000
DEFAULT_VOICE = {"voice": "zh-CN-YunjianNeural", "rate": "-30%", "pitch": "-2Hz", "volume": "+0%"}


class TTSProvider:
    """所有实现返回 (mp3_path, words)；words=[{text,start,dur}] 秒。"""
    name = "base"

    def synthesize(self, text: str, voice: str, rate: str, pitch: str, volume: str, out_mp3: Path):
        raise NotImplementedError


class EdgeTTSProvider(TTSProvider):
    name = "edge"

    def synthesize(self, text, voice, rate, pitch, volume, out_mp3):
        import edge_tts

        async def run():
            comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume,
                                        boundary="WordBoundary")
            words = []
            with open(out_mp3, "wb") as f:
                async for ch in comm.stream():
                    if ch["type"] == "audio":
                        f.write(ch["data"])
                    elif ch["type"] == "WordBoundary":
                        words.append({"text": ch["text"], "start": ch["offset"] / 1e7,
                                      "dur": ch["duration"] / 1e7})
            return words

        for attempt in range(4):
            try:
                return asyncio.run(run())
            except Exception as e:  # noqa: BLE001 - 网络抖动重试
                if attempt == 3:
                    raise
                print(f"TTS 重试 {attempt + 1}/3：{e}")


PROVIDERS = {"edge": EdgeTTSProvider()}  # 注册实例（曾存类导致少绑 self 的回归已修）


def cache_key(text: str, voice: str, rate: str, pitch: str, volume: str, provider: str) -> str:
    h = hashlib.sha256(f"{provider}|{text}|{voice}|{rate}|{pitch}|{volume}".encode("utf-8")).hexdigest()[:16]
    return h


def synthesize_cached(text: str, out_dir: Path, provider_name: str = "edge", params: dict | None = None):
    """带缓存的逐句合成：同 provider+文本+参数 直接复用。返回 (mp3, wav, meta{words,file_dur,key})。"""
    p = {**DEFAULT_VOICE, **(params or {})}
    voice, rate = p["voice"], p["rate"]
    pitch, volume = p.get("pitch", "+0Hz"), p.get("volume", "+0%")
    key = cache_key(text, voice, rate, pitch, volume, provider_name)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"tts_{key}"
    meta_path = stem.with_suffix(".json")
    wav_path = stem.with_suffix(".wav")
    mp3_path = stem.with_suffix(".mp3")
    if meta_path.exists() and wav_path.exists():
        return mp3_path, wav_path, json.loads(meta_path.read_text(encoding="utf-8"))
    provider = PROVIDERS[provider_name]() if isinstance(PROVIDERS[provider_name], type) else PROVIDERS[provider_name]
    words = provider.synthesize(text, voice, rate, pitch, volume, mp3_path)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3_path), "-ac", "1", "-ar", str(SR), str(wav_path)],
                   check=True)
    import wave
    with wave.open(str(wav_path)) as w:
        file_dur = w.getnframes() / w.getframerate()
    meta = {"key": key, "provider": provider_name, "text": text, "params": p, "words": words, "file_dur": file_dur}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return mp3_path, wav_path, meta
