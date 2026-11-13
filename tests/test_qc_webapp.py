"""core/qc.py 与 webapp/app.py 分支测试（TestClient + 临时数据根）。"""
import math
import subprocess

import pytest
from fastapi.testclient import TestClient

from webapp.app import DATA, app

client = TestClient(app)


# ─────────────────────────────── qc.audio_align（合成音频）
def _make_mix(path, bursts):
    import numpy as np
    from scipy.io import wavfile
    sr, dur = 48000, 8.0
    y = np.zeros(int(sr * dur), np.float32)
    for t0, t1 in bursts:
        n = int(sr * (t1 - t0))
        y[int(t0 * sr):int(t0 * sr) + n] = 0.3 * np.sin(2 * math.pi * 220 * np.arange(n) / sr)
    wavfile.write(path, sr, (y * 32767).astype(np.int16))


def _make_timeline(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("t_sec,segment,kind,id,note\n")
        for t, seg, kind, ident in rows:
            f.write(f"{t},{seg},{kind},{ident},x\n")


def test_audio_align(tmp_path):
    from core.qc import audio_align
    (tmp_path / "素材").mkdir(parents=True)
    _make_mix(tmp_path / "素材" / "mix.wav", [(1.0, 2.0), (4.0, 5.0), (6.2, 7.0)])
    _make_timeline(tmp_path / "timeline.csv", [
        (1.0, "S01", "vo", "vo_S01"), (2.0, "S01", "subtitle", "sub_spoken_S01"),
        (4.0, "S02", "vo", "vo_S02"), (5.0, "S02", "subtitle", "sub_spoken_S02"),
        (6.0, "S03", "vo", "vo_S03"), (7.0, "S03", "subtitle", "sub_spoken_S03"),
        (7.5, "S04", "vo", "vo_S04"), (8.0, "S04", "subtitle", "sub_spoken_S04"),
    ])
    rep = audio_align(tmp_path)
    by = {r["seg"]: r for r in rep}
    assert by["vo_S01"]["对位"] == "✓"      # 人声在窗内
    assert by["vo_S02"]["对位"] == "✓"
    assert by["vo_S03"]["对位"] == "✓"      # 人声跨过窗口边界，窗内仍有能量
    assert by["vo_S04"]["对位"] == "✗"      # 窗口 7.5-8.0 只有底乐静音 → 存疑
    assert (tmp_path / "检查记录" / "音频对位核验.json").exists()


def test_ffprobe_check_and_sheet(tmp_path):
    from core.qc import ffprobe_check, sheet_for
    mp4 = tmp_path / "t.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=black:s=64x64:d=1", "-t", "1", str(mp4)], check=True)
    rep = ffprobe_check(mp4)
    assert rep["decode_ok"] and rep["width"] == 64 and float(rep["duration"]) >= 1.0
    sheet = sheet_for(mp4, tmp_path / "chk", n=2)
    assert sheet.exists()


# ─────────────────────────────── webapp 四屏接口
def _seed(source):
    from core.profile import build_profile
    build_profile(source, "甲乙丙，丁戊己。庚辛壬；癸子丑", "测试作品", DATA)


def test_web_library_styles(synthetic_source):
    _seed(synthetic_source)
    lib = client.get("/api/library").json()
    assert any(i["slug"] == "测试作品" for i in lib)  # 新建档案默认 human-provided
    styles = client.get("/api/styles").json()
    assert len(styles) == 12 and "朗读版" in styles


def test_web_profile_pron(synthetic_source):
    _seed(synthetic_source)
    r = client.get("/api/profiles/测试作品")
    assert r.status_code == 200
    d = r.json()
    assert d["n_columns"] == 4 and d["counts"] == [3, 3, 3, 3] and d["preview"]["n_crops"] == 4
    assert client.get("/data/作品档案/测试作品/preview_overlay.jpg").status_code == 200
    pron = client.get("/api/profiles/测试作品/pron").json()
    assert isinstance(pron, list)


def test_web_rebuild_no_text(synthetic_source):
    # 回归：② 无释文重建曾崩
    _seed(synthetic_source)
    (DATA / "作品档案" / "测试作品" / "释文.txt").unlink(missing_ok=True)
    r = client.post("/api/profiles/测试作品/rebuild", json={"col_counts": [3, 3, 3, 3]})
    assert r.status_code == 200
    jid = r.json()["job"]
    for _ in range(60):
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] != "running":
            break
        import time; time.sleep(0.5)
    assert j["status"] == "done"


def test_web_confirm(synthetic_source):
    # 回归：③ 逐列权威确认 + 读音写入 + 状态翻转
    _seed(synthetic_source)
    r = client.post("/api/profiles/测试作品/confirm", json={
        "per_column": ["甲乙丙", "丁戊己", "庚辛壬", "癸子丑"],
        "text": "甲乙丙，丁戊己。庚辛壬；癸子丑。",
        "pronunciation": {"甲": {"tts_char": "假"}},
    })
    assert r.status_code == 200
    jid = r.json()["job"]
    for _ in range(60):
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] != "running":
            break
        import time; time.sleep(0.5)
    assert j["status"] == "done"
    for _ in range(10):  # 确认写入是任务完成后的后台动作，轮询等它落盘
        d = client.get("/api/profiles/测试作品").json()
        if d["confidence"] == "human-confirmed":
            break
        import time; time.sleep(0.5)
    assert d["confidence"] == "human-confirmed"
    assert d["pron_confirmed"].get("甲", {}).get("tts_char") == "假"


def test_web_busy(synthetic_source):
    # 同作品任务互斥 → 409
    from webapp import jobs as jobs_mod
    _seed(synthetic_source)
    jobs_mod.start("重建档案 测试作品", ["sleep", "3"])
    r = client.post("/api/profiles/测试作品/rebuild", json={})
    assert r.status_code == 409
    import time; time.sleep(3.5)  # 等 sleep 任务结束，不污染其他用例
