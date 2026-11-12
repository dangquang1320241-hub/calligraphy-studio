#!/usr/bin/env python3
"""Overlay a measurement grid on a cleaned artwork so you can fill config.json boxes."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("image")
    p.add_argument("-o", "--out", default="")
    p.add_argument("--step", type=int, default=100)
    args = p.parse_args()
    src = Path(args.image)
    im = Image.open(src).convert("RGB")
    w, h = im.size
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype(FONT, 22, index=2)
    except Exception:
        f = ImageFont.load_default()
    step = args.step
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=(180, 40, 40), width=1)
        d.text((x + 2, 4), str(x), fill=(160, 0, 0), font=f)
        d.text((x + 2, h - 28), str(x), fill=(160, 0, 0), font=f)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=(40, 80, 180), width=1)
        d.text((4, y + 2), str(y), fill=(0, 40, 160), font=f)
    # also print normalized hints along the top in a second pass? skip
    out = Path(args.out) if args.out else src.with_name(src.stem + "_grid.jpg")
    im.save(out, quality=95)
    print(f"saved {out}  size={w}x{h}")
    print("在 config.json 里用归一化坐标: x / 宽, y / 高，例如 x=500, 宽=1587 → 0.315")


if __name__ == "__main__":
    main()
