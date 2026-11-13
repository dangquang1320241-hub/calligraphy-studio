#!/usr/bin/env python3
"""webapp 后台任务器：subprocess 跑编排 CLI，任务状态与日志全落盘（webapp 重启不丢）。

记录：LOG_DIR/<jid>.json（状态机：running → done / failed(code)）；日志：LOG_DIR/<jid>.log。
CALLIGRAPHY_JOBS_DIR 可覆盖目录（测试隔离用）。
"""
import json
import os
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(os.environ.get(
    "CALLIGRAPHY_JOBS_DIR", str(Path.home() / ".calligraphy-studio" / "jobs")))
_KEEP_RECORDS = 200  # 记录保留上限，超出按 mtime 淘汰
_LOG_KEEP_DAYS = 30


def _rec_path(jid: str) -> Path:
    return LOG_DIR / f"{jid}.json"


def _save(rec: dict):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _rec_path(rec["id"]).write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")


def _prune():
    records = sorted(LOG_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for p in records[:-_KEEP_RECORDS]:
        p.unlink(missing_ok=True)
    cutoff = datetime.now().timestamp() - _LOG_KEEP_DAYS * 86400
    for p in LOG_DIR.glob("*.log"):
        if p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)


def start(label: str, cmd: list, slug: str | None = None) -> str:
    jid = uuid.uuid4().hex[:8]
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{jid}.log"
    rec = {"id": jid, "label": label, "cmd": cmd, "slug": slug, "status": "running",
           "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "log": str(log_path)}
    _save(rec)
    log_f = open(log_path, "w")
    proc = subprocess.Popen(cmd, stdout=log_f, stderr=subprocess.STDOUT,
                            text=True, cwd=str(Path(__file__).resolve().parent.parent))

    def wait():
        code = proc.wait()
        log_f.close()
        cur = get(jid)
        if cur:  # 记录可能已被人工清理
            cur["status"] = "done" if code == 0 else f"failed({code})"
            _save(cur)

    threading.Thread(target=wait, daemon=True).start()
    _prune()
    return jid


def get(jid: str) -> dict | None:
    p = _rec_path(jid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def tail(jid: str, n: int = 50) -> str:
    j = get(jid)
    if not j:
        return ""
    p = Path(j["log"])
    if not p.exists():
        return ""
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


def recent(n: int = 12) -> list:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    js = []
    for p in LOG_DIR.glob("*.json"):
        try:
            js.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    js.sort(key=lambda j: j.get("ts", ""), reverse=True)
    return js[:n]
