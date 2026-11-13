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
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from core.profile import DEFAULT_DATA_ROOT, build_profile, load_profile  # noqa: E402


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
    if text is None and prof_json.exists() is False:
        raise SystemExit("新档案需要 --text 释文.txt")
    print(f"构建作品档案 → {data_root / '作品档案' / args.slug}")
    counts = [int(v) for v in args.col_counts.split(",")] if args.col_counts else None
    prof_dir = build_profile(Path(args.source), text, args.slug, data_root, col_counts=counts)
    prof = json.loads((prof_dir / "档案.json").read_text(encoding="utf-8"))
    prof["_dir"] = prof_dir
    return prof


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

    sdir = Path(plugin.__file__).parent
    if hasattr(plugin, "render_cmd"):  # 风格自带 CLI 约定（如 B 族 --out 直出）
        cmd = plugin.render_cmd(report["config"], project_dir, args.preview, args.skip_tts)
    else:
        renderer = sdir / "render_langdu.py"
        if not renderer.exists():
            renderer = sorted(sdir.glob("render*.py"))[0]
        cmd = [sys.executable, str(renderer), "--config", str(report["config"])]
        if args.skip_tts:
            cmd.append("--skip-tts")
        if args.preview:
            cmd += ["--preview", str(args.preview)]
    print("渲染：", " ".join(cmd))
    subprocess.run(cmd, check=True)

    # 通用 ffprobe 兜底 QC + 联系表（渲染器没自带时）
    mp4s = sorted((project_dir / "成片").glob("*.mp4")) if (project_dir / "成片").exists() else []
    if mp4s:
        from core import qc
        rep = qc.ffprobe_check(mp4s[-1])
        chk = project_dir / "检查记录"
        chk.mkdir(exist_ok=True)
        (chk / "编排ffprobe.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"ffprobe：{rep['width']}x{rep['height']} {rep['duration']}s 解码{'✓' if rep['decode_ok'] else '✗ ' + rep['decode_errors'][:120]}")
        if not list(chk.glob("*/联系表.jpg")) and not (chk / "联系表.jpg").exists():
            qc.sheet_for(mp4s[-1], chk)
            print("编排联系表 →", chk / "编排联系表.jpg")

    from core import qc
    if (project_dir / "素材" / "mix.wav").exists() and (project_dir / "timeline.csv").exists():
        align = qc.audio_align(project_dir)
        bad = [r for r in align if r["对位"] != "✓"]
        print(f"音画对位：{len(align) - len(bad)}/{len(align)} ✓" + (f"，存疑 {bad}" if bad else ""))
    else:
        print("音画对位：跳过（该风格无人声时间线，如 底乐类风格）")

    # 发布索引
    mp4s = sorted((project_dir / "成片").glob("*.mp4"))
    if mp4s:
        mp4 = mp4s[-1]
        idx = Path(args.data_root) / "发布索引.csv"
        new = not idx.exists()
        with open(idx, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["日期", "作品", "风格", "文件", "sha256前16"])
            w.writerow([datetime.now().strftime("%Y-%m-%d"), args.slug, args.style,
                        str(mp4), sha256(mp4)[:16]])
        print("发布索引 +1：", mp4)
    print("\n✅ 完成。人工确认点：")
    print("  1) 列框确认图：", prof["_dir"] / "列框确认图.jpg")
    print("  2) 释文：", prof["_dir"] / "释文.txt")
    print("  3) 成片联系表：", project_dir / "检查记录")


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
    m.add_argument("--text", help="释文 txt（新档案必填）")
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
    a = ap.parse_args()
    if a.cmd == "make":
        cmd_make(a)
    elif a.cmd == "llm":
        cmd_llm(a)


if __name__ == "__main__":
    main()
