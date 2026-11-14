#!/usr/bin/env python3
"""calligraphy-studio Web 界面（M6-a 核心四屏）：上传构建 → 列框确认 → 释文确认 → 风格出片。

启动：.venv/bin/python -m webapp [--host 0.0.0.0] [--port 8800]
家人局域网使用：--host 0.0.0.0 后访问 http://<Mac的IP>:8800
"""
import json
import os
import shutil
import sys
import threading
import urllib.parse
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from core.llm import annotate_pronunciation  # noqa: E402
from core.profile import DEFAULT_DATA_ROOT, safe_slug, strip_punct  # noqa: E402
from webapp import jobs  # noqa: E402

# 数据根可用环境变量覆盖（测试隔离用），默认真实数据目录
DATA = Path(os.environ.get("CALLIGRAPHY_DATA_ROOT", str(DEFAULT_DATA_ROOT)))
DATA.mkdir(parents=True, exist_ok=True)
VENV_PY = REPO / ".venv" / "bin" / "python"
if not VENV_PY.exists():  # 允许系统 python 运行 webapp、venv 只跑渲染
    VENV_PY = Path(sys.executable)

app = FastAPI(title="calligraphy-studio")
app.mount("/static", StaticFiles(directory=REPO / "webapp" / "static"), name="static")
app.mount("/data", StaticFiles(directory=str(DATA)), name="data")

# ── 访问码登录（--auth 启用；家人局域网防误用，也是 README 威胁模型要求的对外前置）──
AUTH_CODE = None  # None = 不启用（测试/本机模式）


def enable_auth(code: str):
    global AUTH_CODE
    AUTH_CODE = code


@app.middleware("http")
async def _auth_gate(request, call_next):
    if AUTH_CODE is None:
        return await call_next(request)
    path = request.url.path
    if path in ("/login", "/logout"):
        return await call_next(request)
    if request.cookies.get("cs_auth") == AUTH_CODE:
        return await call_next(request)
    return RedirectResponse("/login")


_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>calligraphy-studio · 登录</title>
<style>
  body {{ margin:0; background:#171412; color:#efe6d2; font:15px/1.6 "PingFang SC","Microsoft YaHei",sans-serif;
         display:flex; align-items:center; justify-content:center; min-height:100vh; }}
  .card {{ background:#221d19; border:1px solid #3a322b; border-radius:12px; padding:36px 40px; width:320px; }}
  h1 {{ font-size:16px; color:#b8a98f; font-weight:500; margin:0 0 18px; letter-spacing:1px; }}
  input {{ width:100%; background:#191512; border:1px solid #3a322b; color:#efe6d2; padding:10px 12px;
          border-radius:8px; font:inherit; margin-bottom:14px; }}
  button {{ width:100%; background:#b03a2e; border:none; color:#fff; padding:10px; border-radius:8px;
           font:15px inherit; cursor:pointer; }}
  .err {{ color:#e0b47f; font-size:13px; margin:0 0 10px; }}
</style></head><body>
<div class="card"><h1>calligraphy-studio 工作台</h1>
{error}<form method="post" action="/login">
<input type="password" name="password" placeholder="访问码" autofocus>
<button>进入</button></form></div></body></html>"""


@app.get("/login", response_class=HTMLResponse)
def login_form(error: str = ""):
    return _LOGIN_PAGE.format(error=f'<p class="err">{error}</p>' if error else "")


@app.post("/login")
def login_post(password: str = Form("")):
    if AUTH_CODE is not None and password != AUTH_CODE:
        return HTMLResponse(_LOGIN_PAGE.format(error='<p class="err">访问码不正确</p>'), status_code=401)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("cs_auth", AUTH_CODE or "", httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("cs_auth")
    return resp


def prof_dir(slug: str) -> Path:
    d = DATA / "作品档案" / _safe(slug)
    if not (d / "档案.json").exists():
        raise HTTPException(404, f"档案不存在：{slug}")
    return d


# 档案目录名安全化与 core/profile 共用一份实现（防路径逃逸；空名/纯点拒绝）
def _safe(s: str) -> str:
    try:
        return safe_slug(s)
    except ValueError as e:
        raise HTTPException(400, str(e))


def load(slug: str) -> dict:
    return json.loads((prof_dir(slug) / "档案.json").read_text(encoding="utf-8"))


def _ensure_crops(slug: str) -> dict:
    """列裁图 + 确认图副本（ASCII 文件名）。布局变化（档案更新）时自动重新生成。"""
    import hashlib
    d = prof_dir(slug)
    prof = load(slug)
    crops_dir = d / "列裁图"
    layout_hash = hashlib.sha1(json.dumps(prof["layout"]["columns"], ensure_ascii=False).encode()).hexdigest()[:10]
    marker = crops_dir / ".layout_hash"
    if crops_dir.exists() and marker.exists() and marker.read_text(encoding="utf-8") == layout_hash:
        pass  # 布局没变，复用
    else:
        from PIL import Image
        im = Image.open(d / "作品_校正.png").convert("RGB")
        W, H = im.size
        crops_dir.mkdir(exist_ok=True)
        for old in crops_dir.glob("col*.jpg"):
            old.unlink()
        for c in prof["layout"]["columns"]:
            x0, y0, x1, y1 = [int(v * s) for v, s in zip(c["box"], (W, H, W, H))]
            crop = im.crop((max(0, x0 - 20), max(0, y0 - 20), min(W, x1 + 20), min(H, y1 + 20)))
            crop = crop.resize((crop.width * 2, crop.height * 2))
            crop.save(crops_dir / f"col{c['index'] + 1:02d}.jpg", quality=90)
        marker.write_text(layout_hash, encoding="utf-8")
    overlay = d / "preview_overlay.jpg"
    src_overlay = d / "列框确认图.jpg"
    if not overlay.exists() or src_overlay.stat().st_mtime > overlay.stat().st_mtime:
        shutil.copy(src_overlay, overlay)
    return {"overlay": f"/data/作品档案/{urllib.parse.quote(_safe(slug))}/preview_overlay.jpg",
            "crops_dir": f"/data/作品档案/{urllib.parse.quote(_safe(slug))}/列裁图",
            "n_crops": len(list(crops_dir.glob('col*.jpg')))}


def _assert_slug_free(slug: str):
    """同作品同时只允许一个后台任务（防确认/重建竞态覆盖）。"""
    for j in jobs.recent(50):
        if j["status"] != "running":
            continue
        if j.get("slug") == slug or (j.get("slug") is None and slug in j["label"]):
            raise HTTPException(409, f"该作品已有任务在跑（{j['label']}），请等它完成再操作")


def _style_registry() -> dict:
    from core.orchestrate import discover_styles
    out = {}
    for name, mod in discover_styles().items():
        out[name] = {"needs_text": bool(mod.NEEDS_TEXT), "granularity": getattr(mod, "GRANULARITY", ""),
                     "sample": bool((DATA / "样片库" / f"{name}.mp4").exists())}
    return out


def _sample_slug() -> str:
    """样片源作品：优先人审过的档案（human-confirmed），否则取最新有释文的。"""
    best = ""
    for d in sorted((DATA / "作品档案").glob("*/档案.json"), key=lambda p: -p.stat().st_mtime):
        try:
            prof = json.loads(d.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not prof.get("transcription", {}).get("text"):
            continue
        if prof["transcription"].get("confidence") == "human-confirmed":
            return prof["slug"]
        best = best or prof["slug"]
    return best


# ─────────────────────────────── 页面
@app.get("/", response_class=HTMLResponse)
def home():
    return (REPO / "webapp" / "static" / "index.html").read_text(encoding="utf-8")


# ─────────────────────────────── ① 上传构建 / ⚡ 一键出片
@app.post("/api/profiles")
async def api_create(slug: str = Form(...), text: str = Form(""), image: UploadFile = File(...),
                     auto: str = Form("0"), style: str = Form("")):
    slug = _safe(slug)
    if not slug:
        raise HTTPException(400, "作品标识不能为空")
    _assert_slug_free(slug)
    up = DATA / "_uploads"
    up.mkdir(exist_ok=True)
    data = await image.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, "图片超过 50MB，请压缩后上传")
    ct = (image.content_type or "").lower()
    name = (image.filename or "").lower()
    if not (ct.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"))):
        raise HTTPException(415, f"仅支持图片（jpg/png/webp/heic），收到 {image.content_type or name or '未知类型'}")
    src = up / f"{slug}.jpg"  # HEIC 归一在 core.profile 注册 opener，扩展名不影响解码
    src.write_bytes(data)
    cmd = [str(VENV_PY), "-m", "core.profile", str(src), "--slug", slug]
    if text.strip():
        t = up / f"{slug}_释文.txt"
        t.write_text(text.strip() + "\n", encoding="utf-8")
        cmd += ["--text", str(t)]
    label = f"构建档案 {slug}"
    if auto == "1":
        from core.orchestrate import discover_styles
        if style not in discover_styles():
            raise HTTPException(400, f"未知风格：{style}")
        cmd = [str(VENV_PY), "-m", "core.orchestrate", "auto", str(src),
               "--style", style, "--slug", slug, "--data-root", str(DATA)]
        if text.strip():
            cmd += ["--text", str(t)]
        label = f"一键出片 {slug} · {style}"
    jid = jobs.start(label, cmd, slug=slug)
    return {"job": jid, "slug": slug, "auto": auto == "1"}


# ─────────────────────────────── ② 列框确认
@app.get("/api/profiles/{slug}")
def api_profile(slug: str):
    slug = _safe(slug)
    prof = load(slug)
    counts = [len(c["chars"]) for c in prof["layout"]["columns"]]
    return {
        "slug": slug,
        "n_columns": prof["layout"]["n_columns"],
        "counts": counts,
        "per_column": prof["transcription"].get("per_column", []),
        "text": prof["transcription"].get("text", ""),
        "confidence": prof["transcription"].get("confidence", ""),
        "paper_rect": prof["geometry"]["paper_rect"],
        "preview": _ensure_crops(slug),
        "pron_confirmed": prof.get("pronunciation", {}),
    }


@app.post("/api/profiles/{slug}/rebuild")
def api_rebuild(slug: str, body: dict):
    slug = _safe(slug)
    _assert_slug_free(slug)
    d = prof_dir(slug)
    prof = load(slug)
    src = d / "作品原图.jpg"
    text = body.get("text", prof["transcription"].get("text", "")).strip()
    cmd = [str(VENV_PY), "-m", "core.profile", str(src), "--slug", slug, "--data-root", str(DATA)]
    if text:  # 有释文才走文字分配；无释文重建=按列字数占位切字（释文在③填写）
        t = d / "释文.txt"
        t.write_text(text + "\n", encoding="utf-8")
        cmd += ["--text", str(t)]
    if body.get("col_counts"):
        cmd += ["--col-counts", ",".join(str(v) for v in body["col_counts"])]
    if body.get("signature"):
        cmd += ["--signature"]
    jid = jobs.start(f"重建档案 {slug}", cmd, slug=slug)
    return {"job": jid}


# ─────────────────────────────── ③ 释文确认
@app.get("/api/profiles/{slug}/pron")
def api_pron(slug: str):
    slug = _safe(slug)
    prof = load(slug)
    confirmed = prof.get("pronunciation", {})
    text = prof["transcription"].get("text", "")
    draft = annotate_pronunciation(text)
    return [{"char": ch, "readings": v["readings"],
             "state": "已确认" if ch in confirmed else "待定夺",
             "tts_char": confirmed.get(ch, {}).get("tts_char", "")} for ch, v in draft.items()]


@app.post("/api/profiles/{slug}/confirm")
def api_confirm(slug: str, body: dict):
    """确认释文 + 读音：写入档案（confidence=human-confirmed），按逐列文本（权威）或全文+列字数重建字框。"""
    slug = _safe(slug)
    text = body.get("text", "").strip()
    per_column = [c for c in (body.get("per_column") or []) if strip_punct(c)]
    if not text and not per_column:
        raise HTTPException(400, "释文为空")
    _assert_slug_free(slug)
    pron = {k: v for k, v in (body.get("pronunciation") or {}).items() if v.get("pinyin") or v.get("tts_char")}
    cmd = [str(VENV_PY), "-m", "core.profile", str(prof_dir(slug) / "作品原图.jpg"),
           "--slug", slug, "--data-root", str(DATA)]
    if per_column:
        cmd += ["--columns-text", "|".join(per_column)]
        text = text or "".join(per_column)
    if text:
        t = prof_dir(slug) / "释文.txt"
        t.write_text(text + "\n", encoding="utf-8")
        cmd += ["--text", str(t)]
    counts = body.get("col_counts") or []
    if counts and not per_column:
        cmd += ["--col-counts", ",".join(str(v) for v in counts)]
    jid = jobs.start(f"确认释文 {slug}", cmd, slug=slug)

    def patch():
        import time
        while True:
            j = jobs.get(jid)
            if j is None or j["status"] != "running":
                break
            time.sleep(1)
        if not j or j["status"] != "done":
            # 重建失败时档案未变，仍盖 human-confirmed 会让释文与字框失配——只在成功后写
            print(f"[confirm] 任务未成功（{j['status'] if j else '任务记录丢失'}），不写入确认状态")
            return
        d = DATA / "作品档案" / slug / "档案.json"
        prof = json.loads(d.read_text(encoding="utf-8"))
        prof["transcription"]["text"] = text
        prof["transcription"]["confidence"] = "human-confirmed"
        prof["pronunciation"] = pron
        (d.parent / "释文.txt").write_text(text + "\n", encoding="utf-8")
        d.write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    threading.Thread(target=patch, daemon=True).start()
    return {"job": jid, "note": "重建完成后写入确认状态"}


# ─────────────────────────────── ④ 风格出片
@app.get("/api/styles")
def api_styles():
    return _style_registry()


@app.post("/api/sample/{style}")
def api_sample(style: str):
    """生成风格样片（样片源自动选人审过的档案）；已有样片时前端直接播放，不会走到这里。"""
    from core.orchestrate import discover_styles
    if style not in discover_styles():
        raise HTTPException(404, f"未知风格：{style}")
    slug = _sample_slug()
    if not slug:
        raise HTTPException(400, "作品库为空：请先上传一件作品（样片用它试渲）")
    _assert_slug_free(slug)
    cmd = [str(VENV_PY), "-m", "core.orchestrate", "sample", style,
           "--slug", slug, "--data-root", str(DATA)]
    jid = jobs.start(f"生成样片 {style}", cmd, slug=slug)
    return {"job": jid, "slug": slug}


@app.post("/api/make")
def api_make(body: dict):
    slug = _safe(body.get("slug", ""))
    style = body.get("style", "")
    _assert_slug_free(slug)
    prof = load(slug)  # 校验档案存在
    src = prof["source"]["file"]  # 用档案记录的原始图（sha256 校验基于它）
    cmd = [str(VENV_PY), "-m", "core.orchestrate", "make", style, str(src),
           "--slug", slug, "--data-root", str(DATA)]
    if body.get("title"):
        cmd += ["--title", body["title"]]
    if body.get("author"):
        cmd += ["--author", body["author"]]
    if body.get("title_sub"):
        cmd += ["--title-sub", body["title_sub"]]
    if body.get("preview"):
        cmd += ["--preview", str(body["preview"])]
    pfile = DATA / "_uploads" / f"{slug}_{style}_params.json"
    if body.get("params"):
        pfile.write_text(json.dumps(body["params"], ensure_ascii=False), encoding="utf-8")
        cmd += ["--params", str(pfile)]
    jid = jobs.start(f"出片 {slug} · {style}", cmd, slug=slug)
    return {"job": jid}


# ─────────────────────────────── 任务与作品库
@app.get("/api/jobs/{jid}")
def api_job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "任务不存在")
    return {**j, "tail": jobs.tail(jid, 30)}


@app.get("/api/jobs")
def api_jobs():
    return jobs.recent()


@app.get("/api/library")
def api_library():
    items = []
    for d in sorted((DATA / "作品档案").glob("*/档案.json"), key=lambda p: -p.stat().st_mtime):
        try:
            prof = json.loads(d.read_text(encoding="utf-8"))
            items.append({"slug": prof["slug"], "confidence": prof["transcription"].get("confidence", ""),
                          "n_columns": prof["layout"]["n_columns"], "date": prof.get("created", "")})
        except Exception:
            continue
    return items


def main():
    import argparse
    import secrets
    import uvicorn
    ap = argparse.ArgumentParser(description="calligraphy-studio Web 界面")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--auth", action="store_true",
                    help="启用访问码登录（码持久化在 ~/.calligraphy-studio/webauth.json）")
    a = ap.parse_args()
    if a.auth:
        code_file = Path.home() / ".calligraphy-studio" / "webauth.json"
        code_file.parent.mkdir(parents=True, exist_ok=True)
        if code_file.exists():
            code = json.loads(code_file.read_text(encoding="utf-8"))["code"]
        else:
            code = secrets.token_hex(3)  # 6 位，好输
            code_file.write_text(json.dumps({"code": code}), encoding="utf-8")
        enable_auth(code)
        print("访问码：", code)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
