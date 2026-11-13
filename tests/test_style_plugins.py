"""其余 9 个风格插件的 build_config 测试：保证每个风格都能从同一档案生成合法 config。"""
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

CASES = {
    # 风格: (额外 params, 必须出现的 config 键)
    "诗意叠化": ({}, ["scene"]),
    "一字一讲": ({"sentences": [{"text": "这是「甲」字。", "show": "work"}]}, ["char_box", "evolution", "sentences"]),
    "章法解析": ({}, ["inkseg", "layers", "layout_px"]),
    "节奏卡点": ({}, ["rhythm", "shots", "layout_px"]),
    "临帖对比": ({"reference": {"image": "x.jpg", "label": "参照"},
                "chars": [{"char": "甲", "ref_box": [0, 0, 10, 10], "comment": "示意"}]},
               ["work", "reference", "chars"]),
    "慢揭幕": ({}, ["passage", "passage_lines", "columns", "highlights"]),
    "逐字特写": ({}, ["highlights", "columns"]),
    "笔顺复现": ({}, ["columns_text", "has_signature"]),
    "书写路径": ({}, ["strokes", "focus"]),
    "墨色呼吸": ({}, ["focus_points", "n_shots", "cleaned"]),
    "作品入画": ({}, ["scenes", "cleaned", "tag_text"]),
}


def _load(built_profile):
    prof = json.loads((built_profile / "档案.json").read_text(encoding="utf-8"))
    prof["_dir"] = built_profile
    return prof


@pytest.mark.parametrize("style", list(CASES), ids=list(CASES))
def test_style_build_config(built_profile, tmp_path, style):
    params, required_keys = CASES[style]
    from core.orchestrate import discover_styles
    styles = discover_styles()
    mod = styles[style]
    prof = _load(built_profile)
    report = mod.build_config(prof, Path(tmp_path) / f"项目_{style}", dict(params))
    cfg = json.loads(Path(report["config"]).read_text(encoding="utf-8"))
    for key in required_keys:
        assert key in cfg, f"{style} config 缺 {key}"
    assert cfg["output_name"]
    if style == "笔顺复现":
        assert cfg["columns_text"] == ["甲乙丙", "丁戊己", "庚辛壬", "癸子丑"]
    if style == "临帖对比":
        assert cfg["chars"][0]["work_box"] and cfg["chars"][0]["ref_box"]
    if style == "慢揭幕":
        assert cfg["passage_lines"] == ["甲乙丙，丁戊己。", "庚辛壬；", "癸子丑"]  # 揭开字幕按句级换行


def test_styles_registry_complete():
    from core.orchestrate import discover_styles
    assert len(discover_styles()) == 12
