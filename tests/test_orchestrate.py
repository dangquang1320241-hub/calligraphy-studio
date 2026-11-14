"""orchestrate 编排分段测试：失败路径与幂等索引（不真实渲染）。"""
import csv
import shutil
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest


def _args(tmp_path, style="朗读版", **kw):
    base = dict(style=style, source=str(tmp_path / "src.jpg"), text=None, slug="测速作品",
                data_root=str(tmp_path / "data"), title=None, author=None, title_sub=None,
                params=None, preview=None, skip_tts=True, rebuild_profile=False, col_counts=None)
    base.update(kw)
    return Namespace(**base)


def test_unknown_style_lists_available(tmp_path):
    from core.orchestrate import cmd_make
    with pytest.raises(SystemExit, match="未知风格"):
        cmd_make(_args(tmp_path, style="不存在的风格"))


def test_needs_text_guard(tmp_path):
    # NEEDS_TEXT 风格 + 无档案 + 无释文 → 拦截（无释文语义修复后仍保留需释文风格的防线）
    from core.orchestrate import cmd_make
    with pytest.raises(SystemExit, match="需要释文"):
        cmd_make(_args(tmp_path, style="朗读版"))


def test_render_failure_leaves_clean_state(tmp_path, monkeypatch, synthetic_source):
    # 回归（F2）：渲染中途失败 → 不写 QC 产物、不记账发布索引
    from core.orchestrate import cmd_make
    from core.profile import build_profile
    src = tmp_path / "src.jpg"
    shutil.copy(synthetic_source, src)
    build_profile(src, "甲乙丙，丁戊己。庚辛壬；癸子丑", "测速作品", tmp_path / "data")
    a = _args(tmp_path, source=str(src), text=str(tmp_path / "释文.txt"))
    (tmp_path / "释文.txt").write_text("甲乙丙，丁戊己。庚辛壬；癸子丑", encoding="utf-8")

    def boom(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr("core.orchestrate.subprocess.run", boom)
    with pytest.raises(subprocess.CalledProcessError):
        cmd_make(a)
    data = tmp_path / "data"
    assert not (data / "发布索引.csv").exists()
    assert not (data / "项目" / "测速作品_朗读版" / "检查记录" / "编排ffprobe.json").exists()


def test_stage_index_idempotent(tmp_path):
    # 同作品+同风格+同 sha 重跑 → 跳过；内容变化（重渲）→ 新行
    from core.orchestrate import stage_index
    proj = tmp_path / "proj"
    (proj / "成片").mkdir(parents=True)
    mp4 = proj / "成片" / "out.mp4"
    mp4.write_bytes(b"v1")
    a = _args(tmp_path)
    stage_index(a, proj)
    stage_index(a, proj)
    rows = list(csv.reader(open(tmp_path / "data" / "发布索引.csv", encoding="utf-8")))
    assert len(rows) == 2  # header + 1 行
    mp4.write_bytes(b"v2")
    stage_index(a, proj)
    rows = list(csv.reader(open(tmp_path / "data" / "发布索引.csv", encoding="utf-8")))
    assert len(rows) == 3


def test_cmd_sample_moves_product(tmp_path, monkeypatch, synthetic_source):
    # 样片产出落 样片库/<风格>.mp4；与 项目/ 完全隔离，不动原档案（缩略图失败仅跳过）
    from core.orchestrate import cmd_sample
    from core.profile import build_profile
    src = tmp_path / "src.jpg"
    shutil.copy(synthetic_source, src)
    build_profile(src, "甲乙丙，丁戊己。庚辛壬；癸子丑", "测速作品", tmp_path / "data")

    def fake_render(cmd, **kw):
        cfg = Path(cmd[cmd.index("--config") + 1])
        proj = cfg.parent.parent  # config 在 源码/ 里 → 项目目录是上一级
        (proj / "成片").mkdir(parents=True, exist_ok=True)
        (proj / "成片" / "out.mp4").write_bytes(b"fake mp4")
        return 0

    monkeypatch.setattr("core.orchestrate.subprocess.run", fake_render)
    a = Namespace(style="朗读版", slug="测速作品", seconds=10.0, data_root=str(tmp_path / "data"))
    cmd_sample(a)
    assert (tmp_path / "data" / "样片库" / "朗读版.mp4").read_bytes() == b"fake mp4"
    assert not (tmp_path / "data" / "项目").exists()
    assert (tmp_path / "data" / "作品档案" / "测速作品" / "档案.json").exists()


def test_cmd_sample_needs_text_guard(tmp_path, synthetic_source):
    # 需释文风格 + 样片源档案无释文 → 拦截并提示换源
    from core.orchestrate import cmd_sample
    from core.profile import build_profile
    src = tmp_path / "src.jpg"
    shutil.copy(synthetic_source, src)
    build_profile(src, None, "无释文作品", tmp_path / "data")
    a = Namespace(style="朗读版", slug="无释文作品", seconds=10.0, data_root=str(tmp_path / "data"))
    with pytest.raises(SystemExit, match="缺释文"):
        cmd_sample(a)
