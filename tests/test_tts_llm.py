"""core/tts.py 与 core/llm.py 分支测试（不联网）。"""
import json

import pytest

from core import tts as tts_mod
from core.llm import annotate_pronunciation, import_transcription_draft
from core.profile import strip_punct


def test_provider_instance():
    # 回归：PROVIDERS 曾存类不实例化，调用时少绑 self
    from core.tts import PROVIDERS
    p = PROVIDERS["edge"]
    assert hasattr(p, "synthesize") and not isinstance(p, type)


def test_cache_key_stable():
    from core.tts import cache_key
    a = cache_key("甲乙", "v", "-30%", "-2Hz", "+0%", "edge")
    b = cache_key("甲乙", "v", "-30%", "-2Hz", "+0%", "edge")
    c = cache_key("丙丁", "v", "-30%", "-2Hz", "+0%", "edge")
    assert a == b and a != c


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.calls = []

    def synthesize(self, text, voice, rate, pitch, volume, out_mp3):
        import wave
        self.calls.append(text)
        with wave.open(str(out_mp3), "w") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
            w.writeframes(b"\x00\x00" * 4800)  # 0.1s 静音
        return [{"text": text, "start": 0.0, "dur": 0.1}]


def test_cache_hit_and_miss(tmp_path, monkeypatch):
    from core.tts import synthesize_cached
    fake = FakeProvider()
    monkeypatch.setitem(tts_mod.PROVIDERS, "fake", fake)
    mp3, wav, meta = synthesize_cached("甲乙", tmp_path, "fake", {})
    assert fake.calls == ["甲乙"] and meta["file_dur"] > 0
    # 第二次：缓存命中，Provider 不再被调
    mp3b, wavb, meta2 = synthesize_cached("甲乙", tmp_path, "fake", {})
    assert fake.calls == ["甲乙"] and meta2["file_dur"] == meta["file_dur"]
    # 不同文本：重新合成
    synthesize_cached("丙丁", tmp_path, "fake", {})
    assert fake.calls == ["甲乙", "丙丁"]


def test_annotate_pronunciation():
    flags = annotate_pronunciation("甲朝乙种几丙")
    assert {"朝", "种", "几"} <= set(flags)          # 真多音字必须被标记
    assert "甲" not in flags                        # 常见字不误标（pypinyin 冷僻异读可容忍）


def test_import_draft(profile_factory):
    import json
    root = profile_factory(slug="草稿案例")
    prof_dir = root / "作品档案" / "草稿案例"
    reply = '前言…… {"来源": "测试篇", "columns": ["甲乙丙", "丁戊己"], "置信度低的字": ["丙"]} 后记'
    d = import_transcription_draft(prof_dir, reply)
    assert d["source_text"] == "测试篇"
    assert d["confidence"] == "llm-draft"           # 红线：LLM 输出必须是草稿
    assert (prof_dir / "释文草稿.txt").exists()
    assert json.loads((prof_dir / "档案.json").read_text(encoding="utf-8"))["transcription"]["draft"]["confidence"] == "llm-draft"
    with pytest.raises(ValueError, match="JSON"):
        import_transcription_draft(prof_dir, "完全没有 JSON 的回复")


def test_export_pkg(built_profile):
    from core.llm import export_transcription_package
    pkg = export_transcription_package(built_profile)
    crops = list(pkg.glob("列*.jpg"))
    assert len(crops) == 4 and (pkg / "prompt.txt").exists()
