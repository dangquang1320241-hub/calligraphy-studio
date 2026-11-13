#!/usr/bin/env python3
"""webapp 后台任务器：subprocess 跑编排 CLI，日志落文件，状态可查。"""
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path

_JOBS = {}
_LOCK = threading.Lock()
LOG_DIR = Path.home() / ".calligraphy-studio" / "jobs"


def start(label: str, cmd: list) -> str:
    jid = uuid.uuid4().hex[:8]
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{jid}.log"
    with _LOCK:
        _JOBS[jid] = {"id": jid, "label": label, "cmd": cmd, "status": "running",
                      "ts": datetime.now().strftime("%H:%M:%S"), "log": str(log_path)}
    proc = subprocess.Popen(cmd, stdout=open(log_path, "w"), stderr=subprocess.STDOUT,
                            text=True, cwd=str(Path(__file__).resolve().parent.parent))
    def wait():
        code = proc.wait()
        with _LOCK:
            _JOBS[jid]["status"] = "done" if code == 0 else f"failed({code})"
    threading.Thread(target=wait, daemon=True).start()
    return jid


def get(jid: str) -> dict | None:
    with _LOCK:
        j = _JOBS.get(jid)
        return dict(j) if j else None


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
    with _LOCK:
        js = sorted(_JOBS.values(), key=lambda j: j["ts"], reverse=True)[:n]
        return [dict(j) for j in js]
