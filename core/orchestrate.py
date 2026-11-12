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
    report = plugin.build_config(prof, project_dir, params)
    print(f"config 生成：{report['config']}（{report['n_sentences']} 句 / {report['n_columns']} 列，"
          f"预计 {report['durations_estimate']}）")

    sdir = Path(plugin.__file__).parent
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

    from core import qc
    align = qc.audio_align(project_dir)
    bad = [r for r in align if r["对位"] != "✓"]
    print(f"音画对位：{len(align) - len(bad)}/{len(align)} ✓" + (f"，存疑 {bad}" if bad else ""))

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
    a = ap.parse_args()
    if a.cmd == "make":
        cmd_make(a)


if __name__ == "__main__":
    main()
