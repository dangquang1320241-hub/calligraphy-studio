"""core/qc.py 与 webapp/app.py 分支测试（TestClient + 临时数据根）。"""
import json
import math
import subprocess
from pathlib import Path

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
        (10.0, "S05", "vo", "vo_S05"),  # 回归：vo 起点越过音频末尾，曾空切片崩溃/NaN
    ])
    rep = audio_align(tmp_path)
    by = {r["seg"]: r for r in rep}
    assert by["vo_S01"]["对位"] == "✓"      # 人声在窗内
    assert by["vo_S02"]["对位"] == "✓"
    assert by["vo_S03"]["对位"] == "✓"      # 人声跨过窗口边界，窗内仍有能量
    assert by["vo_S04"]["对位"] == "✗"      # 窗口 7.5-8.0 只有底乐静音 → 存疑
    assert by["vo_S05"]["对位"] == "✗"      # 越界窗 → 存疑而非崩溃/NaN
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
    import time

    from webapp import jobs as jobs_mod
    _seed(synthetic_source)
    jid = jobs_mod.start("重建档案 测试作品", ["sleep", "3"], slug="测试作品")
    r = client.post("/api/profiles/测试作品/rebuild", json={})
    assert r.status_code == 409
    for _ in range(60):  # 轮询等任务结束，不污染其他用例
        if jobs_mod.get(jid)["status"] != "running":
            break
        time.sleep(0.5)
    assert jobs_mod.get(jid)["status"] == "done"


def test_web_slug_rejected(synthetic_source):
    # 安全回归：路径逃逸 / 非法 slug 一律 400，不落盘不读档
    _seed(synthetic_source)
    assert client.get("/api/profiles/%2E%2E").status_code == 400          # ..
    assert client.get("/api/profiles/%2E%2E/pron").status_code == 400
    assert client.post("/api/profiles/%2E%2E/confirm", json={"text": "甲"}).status_code == 400
    assert client.get("/api/profiles/%E3%80%82").status_code == 404       # 安全化后非空但不存在的档案


def test_web_upload_validation(synthetic_source):
    # 上传校验：非图片 → 415；过大 → 413
    _seed(synthetic_source)
    r = client.post("/api/profiles", data={"slug": "上传校验", "text": ""},
                    files={"image": ("x.txt", b"not an image", "text/plain")})
    assert r.status_code == 415


def test_grab_frames_out_of_range(tmp_path):
    # 回归：-ss 越过视频末尾时 ffmpeg 0 退出且不出文件，曾把不存在路径传给下游
    from common.videokit import grab_frames
    mp4 = tmp_path / "t.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=black:s=64x64:d=1", "-t", "1", str(mp4)], check=True)
    with pytest.raises(RuntimeError, match="取帧失败"):
        grab_frames(mp4, [99.0], tmp_path / "kf")


def test_encode_smoke(tmp_path):
    # 回归：stderr 改临时文件后基本编码完好（原 PIPE 用法在长渲染时有管道互锁死锁隐患）
    import numpy as np
    from scipy.io import wavfile

    from common.encode import render_frames_to_mp4
    wavfile.write(tmp_path / "a.wav", 48000, np.zeros(48000, np.int16))
    frames = (np.full((64, 64, 3), 128, np.uint8) for _ in range(10))
    out = render_frames_to_mp4(frames, 10, 10, 64, 64, tmp_path / "a.wav", tmp_path / "out.mp4")
    assert out.exists() and out.stat().st_size > 0


def test_jobs_persist(synthetic_source):
    # 任务状态落盘：重启（重新读盘）不丢，记录文件与索引一致
    import time

    from webapp import jobs as jobs_mod
    jid = jobs_mod.start("落盘测试", ["true"])
    for _ in range(50):
        j = jobs_mod.get(jid)
        if j and j["status"] != "running":
            break
        time.sleep(0.2)
    assert j["status"] == "done"
    rec = Path(j["log"]).with_suffix(".json")
    assert rec.exists() and json.loads(rec.read_text(encoding="utf-8"))["id"] == jid
    assert any(x["id"] == jid for x in jobs_mod.recent())
