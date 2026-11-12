#!/usr/bin/env python3
"""Title card and end card shared by both templates."""
from __future__ import annotations

from PIL import Image, ImageDraw

from media import (
    CREAM,
    GOLD,
    INK,
    MUTED,
    alpha_blit,
    font,
    make_seal,
    paste_centered,
    text_image,
)


def draw_rule(width: int, color=GOLD, thickness: int = 2, opacity: float = 1.0) -> Image.Image:
    im = Image.new("RGBA", (width, thickness + 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    a = int(255 * opacity)
    d.rectangle((0, 1, width - 1, thickness), fill=color + (a,))
    return im


def compose_title_card(bg: Image.Image, title: str, subtitle: str, opacity: float = 1.0, t_local: float = 2.0):
    """bg is already the paper (or paper over faint work). t_local is seconds into the title."""
    dst = bg.convert("RGBA")
    W, H = dst.size
    # title fade + slight rise
    # Title is already readable at t=0 and settles in quickly (first 2s must hold).
    title_op = opacity * min(1.0, max(0.80, 0.80 + t_local / 0.40))
    title_op = min(opacity, title_op)
    sub_op = opacity * min(1.0, max(0.0, (t_local - 0.35) / 0.45))
    rule_p = min(1.0, max(0.0, (t_local - 0.18) / 0.35))
    rise = int((1.0 - title_op) * 10)

    title_im = text_image(title, font(78, bold=True), INK + (255,), tracking=4)
    sub_im = text_image(subtitle, font(36, bold=False), tuple(list(GOLD) + [255]), tracking=14)

    cy = int(H * 0.46)
    dst = paste_centered(dst, title_im, W // 2, cy - 20 - rise, title_op)

    rule_w = int(min(W * 0.28, 280) * (0.15 + 0.85 * rule_p))
    rule = draw_rule(max(8, rule_w), GOLD, 2)
    dst = paste_centered(dst, rule, W // 2, cy + 48, title_op * rule_p)

    dst = paste_centered(dst, sub_im, W // 2, cy + 100, sub_op)
    return dst


def compose_end_card(
    canvas: Image.Image,
    title: str,
    subtitle: str,
    author: str,
    author_note: str,
    seal_char: str,
    opacity: float = 1.0,
):
    if opacity <= 0:
        return canvas
    dst = canvas.convert("RGBA")
    W, H = dst.size
    title_im = text_image(title, font(48, bold=True), CREAM + (255,), tracking=3)
    sub_im = text_image(subtitle, font(28, bold=False), GOLD + (255,), tracking=12)
    author_im = text_image(author, font(40, bold=False), CREAM + (255,), tracking=6)
    note_im = text_image(f"（{author_note}）", font(20, bold=False), MUTED + (255,), tracking=2)
    seal = make_seal(seal_char or "书", size=132, angle=-4.5)

    # typography sits in the lower-middle, above the safe area
    ty = 1180
    dst = paste_centered(dst, title_im, W // 2, ty, opacity)
    dst = paste_centered(dst, sub_im, W // 2, ty + 58, opacity * 0.95)

    # author + seal as a group
    gap = 22
    group_w = author_im.width + gap + seal.width
    gx = (W - group_w) // 2
    gy = ty + 130
    dst = alpha_blit(dst, author_im, gx, gy + (seal.height - author_im.height) // 2, opacity)
    dst = alpha_blit(dst, seal, gx + author_im.width + gap, gy - 8, opacity)
    if author_note:  # 空说明时不画「（）」
        dst = paste_centered(dst, note_im, W // 2, gy + seal.height + 10, opacity * 0.9)
    return dst
