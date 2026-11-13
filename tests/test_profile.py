"""core/profile.py 分支测试。"""
import json

import pytest

from core.profile import _distribute, build_profile, layout_px_pixels, load_profile, split_clauses, strip_punct

TEXT12 = "甲乙丙，丁戊己。庚辛壬；癸子丑"


def test_strip_punct():
    # 回归：\n 曾被当字计数（后赤壁赋 41≠40 事故）
    assert len(strip_punct("甲乙。\n丙丁")) == 4
    assert strip_punct("，。？！；：、…·—") == ""


def test_split_clauses():
    assert split_clauses("甲乙，丙丁。戊") == ["甲乙，", "丙丁。", "戊"]
    assert split_clauses("，。甲") == ["，。甲"]      # 前导标点保留在短语内（无害）


def test_distribute_ok():
    assert _distribute("甲乙丙丁", [2, 2]) == ["甲乙", "丙丁"]


def test_distribute_mismatch():
    # 回归：三槐堂铭 33≠40 快速失败；错误信息应含各列检出字数
    with pytest.raises(ValueError, match="33"):
        _distribute("一二三四五六七八", [4, 4, 4, 4, 4, 4, 4, 5])


def test_build_auto_estimate(profile_factory):
    root = profile_factory(text=TEXT12)
    prof = json.loads((root / "作品档案" / "测试作品" / "档案.json").read_text(encoding="utf-8"))
    assert prof["layout"]["n_columns"] == 4
    assert prof["transcription"]["per_column"] == ["甲乙丙", "丁戊己", "庚辛壬", "癸子丑"]
    assert all(len(c["chars"]) == 3 for c in prof["layout"]["columns"])


def test_build_with_counts(synthetic_source, tmp_path):
    build_profile(synthetic_source, "甲乙丙丁", "两列案例", tmp_path, col_counts=[2, 2])
    prof = json.loads((tmp_path / "作品档案" / "两列案例" / "档案.json").read_text(encoding="utf-8"))
    assert prof["layout"]["n_columns"] == 2
    assert [len(c["chars"]) for c in prof["layout"]["columns"]] == [2, 2]


def test_build_per_column(built_profile):
    # per_column 权威切字：字框数 = 各列字数
    prof = json.loads((built_profile / "档案.json").read_text(encoding="utf-8"))
    assert [len(c["chars"]) for c in prof["layout"]["columns"]] == [3, 3, 3, 3]
    assert prof["transcription"]["per_column"] == ["甲乙丙", "丁戊己", "庚辛壬", "癸子丑"]


def test_build_no_text(profile_factory):
    # 回归：无释文上传曾因 layout=None 崩溃；现在纯自动出列框
    root = profile_factory()  # 无释文
    prof = json.loads((root / "作品档案" / "测试作品" / "档案.json").read_text(encoding="utf-8"))
    assert prof["layout"]["n_columns"] == 4
    assert prof["transcription"]["confidence"] == "undetected"


def test_build_no_text_counts(profile_factory):
    # 回归：② 无释文重建曾报「释文字数 0」；现在占位切字
    root = profile_factory(col_counts=[3, 3, 3, 3])
    prof = json.loads((root / "作品档案" / "测试作品" / "档案.json").read_text(encoding="utf-8"))
    assert [len(c["chars"]) for c in prof["layout"]["columns"]] == [3, 3, 3, 3]
    assert all(ch["text"] == "□" for c in prof["layout"]["columns"] for ch in c["chars"])


def test_confirm_mismatch(synthetic_source, tmp_path):
    # 回归：per_column(12) 与全文(15) 不一致被快速失败拦截
    with pytest.raises(ValueError, match="不一致"):
        build_profile(synthetic_source, "甲乙丙丁戊己庚辛壬癸子丑寅卯辰", "mismatch案例",
                      tmp_path, per_column=["甲乙丙", "丁戊己", "庚辛壬", "癸子丑"])


def test_auto_estimate_signature(synthetic_source, tmp_path):
    # signature 语义：末列的落款墨块天然在估字数里，文本含落款字——不再重复 +1
    # （回归：测试套件抓到「估算与检测口径不一致」的真 bug）
    from PIL import Image, ImageDraw
    im = Image.open(synthetic_source)
    ImageDraw.Draw(im).rectangle([285, 620, 355, 660], fill=(15, 15, 15))  # 第4列下方补一个落款墨块
    src = tmp_path / "带落款作品.jpg"
    im.save(src, quality=92)
    build_profile(src, "甲乙丙丁戊己庚辛壬癸子丑款", "落款案例", tmp_path, signature=True)
    prof = json.loads((tmp_path / "作品档案" / "落款案例" / "档案.json").read_text(encoding="utf-8"))
    assert [len(c["chars"]) for c in prof["layout"]["columns"]] == [3, 3, 3, 4]
    assert prof["layout"]["columns"][-1]["chars"][-1]["text"] == "款"


def test_per_column_signature_no_extra(profile_factory):
    # 逐列模式：signature 不给末列追加字（落款含在文本里）
    root = profile_factory(slug="签名案例", text="甲乙丙丁戊己庚辛壬癸子丑", signature=True)
    prof = json.loads((root / "作品档案" / "签名案例" / "档案.json").read_text(encoding="utf-8"))
    assert [len(c["chars"]) for c in prof["layout"]["columns"]] == [3, 3, 3, 3]


def test_layout_px_pixels(built_profile):
    prof = json.loads((built_profile / "档案.json").read_text(encoding="utf-8"))
    W, H = prof["geometry"]["size"]
    px = layout_px_pixels(prof)
    assert px["crop"] == prof["geometry"]["paper_rect"]
    assert all(0 <= b <= max(W, H) for col in px["columns"] for b in col["box"])


def test_load_profile_errors(built_profile):
    root = built_profile.parent.parent
    assert load_profile("测试作品", root)["slug"] == "测试作品"
    with pytest.raises(FileNotFoundError):
        load_profile("不存在", root)
    with pytest.raises(ValueError, match="sha256"):
        load_profile("测试作品", root, expect_sha="deadbeef")

