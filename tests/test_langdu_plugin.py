"""styles/langdu/plugin.py build_config 分支测试。"""
import json
import sys

import pytest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from styles.langdu import plugin as langdu  # noqa: E402


def _run(built_profile, tmp_path, params=None):
    prof = json.loads((built_profile / "档案.json").read_text(encoding="utf-8"))
    prof["_dir"] = built_profile
    project = Path(tmp_path) / "项目" / "测试项目"
    report = langdu.build_config(prof, project, params or {})
    cfg = json.loads(Path(report["config"]).read_text(encoding="utf-8"))
    return report, cfg


def test_build_config_default(built_profile, tmp_path):
    report, cfg = _run(built_profile, tmp_path)
    # 12 字 → 默认逐短语：甲乙丙，/ 丁戊己。/ 庚辛壬；/ 癸子丑 = 4 句
    assert cfg["output_name"] == "测试作品_朗读版"
    assert [s["text"] for s in cfg["sentences"]] == ["甲乙丙，", "丁戊己。", "庚辛壬；", "癸子丑"]
    assert [s["subtitle"] for s in cfg["sentences"]] == ["甲乙丙", "丁戊己", "庚辛壬", "癸子丑"]
    # 句↔列映射：依次跨列 1 / 2 / 3 / 4，region 中心单调左移
    xs = [sum(s["region"][0::3]) / 2 for s in cfg["sentences"]]
    assert xs == sorted(xs, reverse=True)
    # 末句 gap_after
    assert cfg["sentences"][-1]["gap_after"] == 0.8
    assert cfg["tts_provider"] == "edge"


def test_build_config_custom(built_profile, tmp_path):
    custom = [
        {"text": "甲乙丙丁戊己。", "subtitle": "前六字"},
        {"text": "庚辛壬癸子丑。", "subtitle": "后六字"},
    ]
    _, cfg = _run(built_profile, tmp_path, {"sentences": custom})
    assert [s["subtitle"] for s in cfg["sentences"]] == ["前六字", "后六字"]
    assert len(cfg["sentences"]) == 2
    # region 覆盖全部 4 列的并集跨度
    assert cfg["sentences"][0]["region"][0] > cfg["sentences"][1]["region"][2]  # 两句分居左右，不重叠


def test_pron_substitution(built_profile, tmp_path):
    # 档案读音表 → tts_text 自动同音替换，字幕不受影响
    prof_path = built_profile / "档案.json"
    prof = json.loads(prof_path.read_text(encoding="utf-8"))
    prof["pronunciation"] = {"甲": {"pinyin": "jiǎ", "tts_char": "假"}, "丑": {"pinyin": "chǒu", "tts_char": "瞅"}}
    prof_path.write_text(json.dumps(prof, ensure_ascii=False), encoding="utf-8")
    _, cfg = _run(built_profile, tmp_path)
    assert cfg["sentences"][0]["tts_text"] == "假乙丙，"
    assert cfg["sentences"][0]["subtitle"] == "甲乙丙"
    assert cfg["sentences"][-1]["tts_text"] == "癸子瞅"


def test_text_layout_mismatch(built_profile, tmp_path):
    # 回归：释文与档案字序列不一致必须报错（列框/释文改动后未重建）
    prof = json.loads((built_profile / "档案.json").read_text(encoding="utf-8"))
    prof["_dir"] = built_profile
    prof["transcription"]["text"] = "甲乙丙丁"  # 只有 4 字 ≠ 字序列 12 字
    with pytest.raises(ValueError, match="不一致"):
        langdu.build_config(prof, Path(tmp_path) / "p", {})


def test_last_gap(built_profile, tmp_path):
    _, cfg = _run(built_profile, tmp_path, {"sentences": [
        {"text": "甲乙丙，", "gap_after": 2.0},
        {"text": "丁戊己。"},
    ]})
    assert cfg["sentences"][0]["gap_after"] == 2.0
    assert cfg["sentences"][-1]["gap_after"] == 0.8
