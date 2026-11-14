#!/usr/bin/env python3
"""编排 CLI（M1）：

  python -m core.orchestrate make 朗读版 <作品图> --text 释文.txt --title 《三槐堂铭》节选 \
      --author 郭德贤 --slug 三槐堂铭节选 [--preview N] [--skip-tts] [--rebuild-profile]

流程：作品档案（建/复用）→ 风格插件生成 config → 渲染器 → QC → 发布索引。
"""
import argparse
import csv
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from core.profile import DEFAULT_DATA_ROOT, build_profile, load_profile, strip_punct  # noqa: E402


def sha256(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def discover_styles():
    """扫描 styles/*/plugin.py，按 NAME 注册。"""
    out = {}
    for pkg_dir in sorted((REPO / "styles").iterdir()):
        mod_file = pkg_dir / "plugin.py"
        if not pkg_dir.is_dir() or not mod_file.exists():
            continue
        mod = importlib.import_module(f"styles.{pkg_dir.name}.plugin")
        out[mod.NAME] = mod
    return out


def ensure_profile(args) -> dict:
    data_root = Path(args.data_root)
    prof_json = data_root / "作品档案" / args.slug / "档案.json"
    if prof_json.exists() and not args.rebuild_profile:
        prof = load_profile(args.slug, data_root, expect_sha=sha256(Path(args.source)))
        print(f"复用作品档案：{prof_json}")
        prof["_dir"] = prof_json.parent
        return prof
    text = Path(args.text).read_text(encoding="utf-8") if args.text else None
    # 无释文新档案允许（版式级风格 / ③屏补释文）；需释文风格已在 cmd_make 按 NEEDS_TEXT 拦截
    print(f"构建作品档案 → {data_root / '作品档案' / args.slug}")
    counts = [int(v) for v in args.col_counts.split(",")] if args.col_counts else None
    prof_dir = build_profile(Path(args.source), text, args.slug, data_root, col_counts=counts)
    prof = json.loads((prof_dir / "档案.json").read_text(encoding="utf-8"))
    prof["_dir"] = prof_dir
    return prof


def newest_mp4(project_dir: Path):
    """成片目录最新 mp4（按 mtime）；无则 None。历史残留的旧半成品不干扰 QC/索引。"""
    d = project_dir / "成片"
    mp4s = sorted(d.glob("*.mp4"), key=lambda p: p.stat().st_mtime) if d.exists() else []
    return mp4s[-1] if mp4s else None


def build_render_cmd(plugin, report: dict, project_dir: Path, preview, skip_tts) -> list:
    """渲染命令：render_cmd 钩子优先；否则 A 族 --config 约定（--skip-tts 仅对声明的渲染器传）。"""
    sdir = Path(plugin.__file__).parent
    if hasattr(plugin, "render_cmd"):  # 风格自带 CLI 约定（如 B 族 --out 直出）
        return plugin.render_cmd(report["config"], project_dir, preview, skip_tts)
    renderer = sdir / "render_langdu.py"
    if not renderer.exists():
        renderer = sorted(sdir.glob("render*.py"))[0]
    cmd = [sys.executable, str(renderer), "--config", str(report["config"])]
    if skip_tts and '"--skip-tts"' in renderer.read_text(encoding="utf-8"):
        # 部分渲染器（章法解析/节奏卡点/临帖对比/书写路径）未声明 --skip-tts，传了会 argparse 崩溃
        cmd.append("--skip-tts")
    if preview:
        # 整数秒传整数字面量：B 族有 type=int 的 --preview（笔顺复现），"10.0" 会 argparse 崩溃
        pv = float(preview)
        cmd += ["--preview", str(int(pv)) if pv.is_integer() else str(pv)]
    return cmd


def stage_qc(project_dir: Path) -> None:
    """编排 QC：ffprobe 兜底 + 联系表兜底 + 音画对位（无成片时跳过）。"""
    from core import qc
    mp4 = newest_mp4(project_dir)
    if mp4:
        rep = qc.ffprobe_check(mp4)
        chk = project_dir / "检查记录"
        chk.mkdir(exist_ok=True)
        (chk / "编排ffprobe.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"ffprobe：{rep['width']}x{rep['height']} {rep['duration']}s 解码{'✓' if rep['decode_ok'] else '✗ ' + rep['decode_errors'][:120]}")
        if not list(chk.glob("*/联系表.jpg")) and not (chk / "联系表.jpg").exists():
            qc.sheet_for(mp4, chk)
            print("编排联系表 →", chk / "编排联系表.jpg")
    if (project_dir / "素材" / "mix.wav").exists() and (project_dir / "timeline.csv").exists():
        align = qc.audio_align(project_dir)
        bad = [r for r in align if r["对位"] != "✓"]
        print(f"音画对位：{len(align) - len(bad)}/{len(align)} ✓" + (f"，存疑 {bad}" if bad else ""))
    else:
        print("音画对位：跳过（该风格无人声时间线，如 底乐类风格）")


def stage_index(args, project_dir: Path) -> None:
    """发布索引：追加一行；同作品+同风格+同内容（sha 前16）幂等跳过。"""
    mp4 = newest_mp4(project_dir)
    if not mp4:
        return
    row_sha = sha256(mp4)[:16]
    idx = Path(args.data_root) / "发布索引.csv"
    idx.parent.mkdir(parents=True, exist_ok=True)
    if idx.exists():
        with open(idx, encoding="utf-8") as f:
            rows = list(csv.reader(f))
        if any(r[1:3] == [args.slug, args.style] and r[4:5] == [row_sha] for r in rows[1:]):
            print("发布索引：同内容已记账，跳过", mp4)
            return
    new = not idx.exists()
    with open(idx, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["日期", "作品", "风格", "文件", "sha256前16"])
        w.writerow([datetime.now().strftime("%Y-%m-%d"), args.slug, args.style,
                    str(mp4), row_sha])
    print("发布索引 +1：", mp4)


def cmd_make(args):
    styles = discover_styles()
    if args.style not in styles:
        raise SystemExit(f"未知风格：{args.style}（可用：{'、'.join(styles)}）")
    plugin = styles[args.style]
    if plugin.NEEDS_TEXT and not args.text and not (Path(args.data_root) / "作品档案" / args.slug / "档案.json").exists():
        raise SystemExit("该风格需要释文：--text 释文.txt")

    prof = ensure_profile(args)
    project_dir = Path(args.data_root) / "项目" / f"{args.slug}_{args.style}"
    params = {k: v for k, v in dict(title=args.title, author=args.author, title_sub=args.title_sub).items() if v}
    if args.params:
        params.update(json.loads(Path(args.params).read_text(encoding="utf-8")))
    report = plugin.build_config(prof, project_dir, params)
    bits = []
    if report.get("n_sentences"):
        bits.append(f"{report['n_sentences']} 段")
    if report.get("n_columns"):
        bits.append(f"{report['n_columns']} 列")
    if report.get("n_chars"):
        bits.append(f"{report['n_chars']} 组字对")
    if report.get("n_shots"):
        bits.append(f"{report['n_shots']} 镜")
    print(f"config 生成：{report['config']}（{' / '.join(bits) or 'ok'}，"
          f"预计 {report.get('durations_estimate', '?')}）"
          + (f"；{report['note']}" if report.get("note") else ""))

    cmd = build_render_cmd(plugin, report, project_dir, args.preview, args.skip_tts)
    print("渲染：", " ".join(cmd))
    subprocess.run(cmd, check=True)

    stage_qc(project_dir)
    stage_index(args, project_dir)
    print("\n✅ 完成。人工确认点：")
    print("  1) 列框确认图：", prof["_dir"] / "列框确认图.jpg")
    print("  2) 释文：", prof["_dir"] / "释文.txt")
    print("  3) 成片联系表：", project_dir / "检查记录")


def cmd_sample(args):
    """风格样片：用样片源作品试渲前 N 秒 → 样片库/<风格>.mp4（+首帧缩略图）。

    与 make 的区别：不进 项目/、不记账发布索引；TTS 能跳则跳（样片看视觉风格）。
    """
    styles = discover_styles()
    if args.style not in styles:
        raise SystemExit(f"未知风格：{args.style}（可用：{'、'.join(styles)}）")
    plugin = styles[args.style]
    data_root = Path(args.data_root)
    prof = load_profile(args.slug, data_root)
    prof["_dir"] = data_root / "作品档案" / args.slug
    if plugin.NEEDS_TEXT and not prof["transcription"].get("text"):
        raise SystemExit(f"样片源档案「{args.slug}」缺释文：请先在 ③ 补齐，或换一件已确认的作品（--slug）")

    sample_dir = data_root / "样片库"
    project_dir = sample_dir / "_build" / args.style  # 与 项目/ 完全隔离
    report = plugin.build_config(prof, project_dir, {"title": f"风格样片 · {args.style}"})
    print(f"config 生成：{report['config']}")

    cmd = build_render_cmd(plugin, report, project_dir, preview=args.seconds, skip_tts=True)
    print("样片渲染：", " ".join(cmd))
    subprocess.run(cmd, check=True)
    mp4 = newest_mp4(project_dir)
    if not mp4:
        raise SystemExit("渲染未产出成片")
    out = sample_dir / f"{args.style}.mp4"
    shutil.copy(mp4, out)
    print("样片 →", out)

    try:  # 首帧缩略图（可选项，失败不阻断样片本身）
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
        from PIL import Image

        from videokit import grab_frames
        frames = grab_frames(str(out), [0.2], sample_dir)
        if frames:
            Image.open(frames[0]).convert("RGB").save(sample_dir / f"{args.style}.jpg", quality=85)
            Path(frames[0]).unlink(missing_ok=True)
            print("缩略图 →", sample_dir / f"{args.style}.jpg")
    except Exception as e:  # noqa: BLE001 - 缩略图尽力而为
        print("缩略图跳过：", e)


def cmd_auto(args):
    """一键出片：图（+可选释文）→ 建档案 → 直接渲染（QC/索引与 make 完全一致）。

    释文策略（①A 决策）：填了零 LLM 直出；没填走视觉认字，档案标 llm-auto，
    低置信字超阈值退回 ②③ 确认流程——认字结果可随时在 ③ 修正后重出（档案复用，重出=一次渲染）。
    """
    styles = discover_styles()
    if args.style not in styles:
        raise SystemExit(f"未知风格：{args.style}（可用：{'、'.join(styles)}）")
    plugin = styles[args.style]
    source, data_root = Path(args.source), Path(args.data_root)
    slug_dir = data_root / "作品档案" / args.slug

    if args.text:
        text = Path(args.text).read_text(encoding="utf-8").strip()
        build_profile(source, text, args.slug, data_root)
        print(f"释文：您已提供（{len(strip_punct(text))} 字，不含标点）——零 LLM 调用")
    elif not plugin.NEEDS_TEXT:
        build_profile(source, None, args.slug, data_root)
        print("释文：该风格无需释文——零 LLM 调用")
    else:
        from core import llm
        build_profile(source, None, args.slug, data_root)
        prov = llm.get_provider()
        print(f"释文：未提供 → {prov.name} 视觉认字（{prov.model}）…")
        try:
            draft = llm.draft_transcription(slug_dir, provider=prov)
        except Exception as e:  # noqa: BLE001 - 网络/解析错误统一退回确认流程
            raise SystemExit(f"LLM 认字失败（{e}）；档案已建，可走 ②③ 确认流程或稍后重试")
        low = draft.get("low_confidence_chars", [])
        if len(low) > args.max_low_conf:
            raise SystemExit(f"低置信字 {len(low)} 个（{'、'.join(low[:8])}）超过阈值 {args.max_low_conf}，"
                             "已退回确认流程：请在 ② 查列框、③ 确认释文后再出片")
        try:
            build_profile(source, None, args.slug, data_root, per_column=draft["per_column"])
        except ValueError as e:
            raise SystemExit(f"LLM 逐列释文与画面检出不一致（{str(e).splitlines()[0]}）；"
                             "档案已建，请走 ②③ 确认流程")
        # 红线标注：LLM 直出 ≠ 人审——③ 修正确认后即转 human-confirmed
        pj = slug_dir / "档案.json"
        prof = json.loads(pj.read_text(encoding="utf-8"))
        prof["transcription"]["confidence"] = "llm-auto"
        prof["transcription"]["draft"] = {"provider": prov.name, "model": prov.model,
                                          "low_confidence_chars": low,
                                          "source_text": draft.get("source_text")}
        prof["pronunciation"] = llm.annotate_pronunciation(prof["transcription"].get("text", ""))
        pj.write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"释文：{prov.name} 认字{'（低置信：' + '、'.join(low) + '）' if low else ''}"
              "——可随时在 ③ 修正后重出")

    make_args = argparse.Namespace(
        style=args.style, source=str(source), text=None, slug=args.slug,
        data_root=str(data_root), title=args.title, author=args.author,
        title_sub=args.title_sub, params=None, preview=args.preview,
        skip_tts=False, rebuild_profile=False, col_counts=None)
    cmd_make(make_args)


def cmd_llm(args):
    """LLM 草稿三件套：释文草稿 / 多音字预标注 / 文案草稿。输出一律 draft，待人工确认。"""
    from core import llm
    data_root = Path(args.data_root)
    prof_dir = data_root / "作品档案" / args.slug
    if args.what == "transcribe":
        if args.export:
            pkg = llm.export_transcription_package(prof_dir)
            print(f"已导出 llm包（逐列裁图 + prompt）→ {pkg}\n把回复存为 {prof_dir}/释文草稿回复.txt 后："
                  f"python -m core.orchestrate llm transcribe --slug {args.slug} --import")
            return
        if args.import_reply:
            reply = Path(args.import_reply).read_text(encoding="utf-8")
            d = llm.import_transcription_draft(prof_dir, reply, provider="export")
        else:
            d = llm.draft_transcription(prof_dir)
        print(f"释文草稿（draft，待确认）：来源={d.get('source_text')} 低置信字={'、'.join(d['low_confidence_chars']) or '无'}")
        print(f"→ {prof_dir}/释文草稿.txt（确认后覆盖 释文.txt 并重建档案）")
    elif args.what == "pron":
        text = (prof_dir / "释文.txt").read_text(encoding="utf-8")
        flags = llm.annotate_pronunciation(text)
        prof = json.loads((prof_dir / "档案.json").read_text(encoding="utf-8"))
        new = {k: v for k, v in flags.items() if k not in prof.get("pronunciation", {})}
        (prof_dir / "读音草稿.json").write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"多音字预标注（draft）：{len(flags)} 个多音字，其中 {len(new)} 个待定夺 → {prof_dir}/读音草稿.json")
        for k, v in flags.items():
            mark = "（已确认）" if k in prof.get("pronunciation", {}) else ""
            print(f"  {k}: {'/'.join(v['readings'])} {mark}")
    elif args.what == "copy":
        prof = json.loads((prof_dir / "档案.json").read_text(encoding="utf-8"))
        prof["_dir"] = prof_dir
        out = llm.draft_copy(prof)
        print(f"文案草稿 → {out}")


def main():
    ap = argparse.ArgumentParser(description="calligraphy-studio 编排器")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make", help="出片：档案 → config → 渲染 → QC")
    m.add_argument("style")
    m.add_argument("source", help="作品原图（或已建档案时的任一等价文件）")
    m.add_argument("--text", help="释文 txt（需释文风格的新档案必填；无释文可先建档案后③补）")
    m.add_argument("--slug", required=True, help="作品标识（档案目录名）")
    m.add_argument("--title", help="片头标题（默认 《slug》）")
    m.add_argument("--title-sub", dest="title_sub", help="副标题（默认 行草）")
    m.add_argument("--author", help="署名（片尾）")
    m.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    m.add_argument("--preview", type=float, help="只渲前 N 秒")
    m.add_argument("--skip-tts", action="store_true")
    m.add_argument("--rebuild-profile", action="store_true")
    m.add_argument("--col-counts", help="人工指定各列字数（右→左逗号分隔），覆盖自动估计")
    m.add_argument("--params", help="风格参数 JSON 文件（sentences/scene/char/evolution 等）")
    l = sub.add_parser("llm", help="LLM 草稿：transcribe（释文）/ pron（多音字）/ copy（文案）")
    l.add_argument("what", choices=["transcribe", "pron", "copy"])
    l.add_argument("--slug", required=True)
    l.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    l.add_argument("--export", action="store_true", help="transcribe：导出逐列裁图+prompt（无 API key 模式）")
    l.add_argument("--import-reply", dest="import_reply", help="transcribe：导入外部 LLM 回复文件")
    s = sub.add_parser("sample", help="生成风格样片（试渲前 N 秒 → 样片库/<风格>.mp4 + 首帧缩略图）")
    s.add_argument("style")
    s.add_argument("--slug", required=True, help="样片源作品档案（建议用人审过的档案）")
    s.add_argument("--seconds", type=float, default=10.0, help="样片时长（前 N 秒试渲）")
    s.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    au = sub.add_parser("auto", help="一键出片：图（+可选释文）→ 建档案（LLM 认字兜底）→ 直接渲染")
    au.add_argument("source")
    au.add_argument("--style", required=True)
    au.add_argument("--slug", required=True)
    au.add_argument("--text", help="释文 txt（可选；填了零 LLM 调用）")
    au.add_argument("--title", help="片头标题")
    au.add_argument("--author", help="署名")
    au.add_argument("--title-sub", dest="title_sub", help="副标题")
    au.add_argument("--max-low-conf", dest="max_low_conf", type=int, default=3,
                    help="低置信字超过该数量则退回 ②③ 确认流程（默认 3）")
    au.add_argument("--preview", type=float, help="只渲前 N 秒")
    au.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    a = ap.parse_args()
    if a.cmd == "make":
        cmd_make(a)
    elif a.cmd == "llm":
        cmd_llm(a)
    elif a.cmd == "sample":
        cmd_sample(a)
    elif a.cmd == "auto":
        cmd_auto(a)


if __name__ == "__main__":
    main()
