#!/usr/bin/env python3
"""LLM 接入（M1.5，契约见 docs/架构.md §7.5）。

三个草稿生成器：释文草稿 / 多音字预标注 / 文案草稿。
红线：LLM 输出一律标 draft（confidence='llm-draft'），人工确认（确认点 2）前不进入渲染。

Provider：
  - GLMProvider：OpenAI 兼容接口，环境变量 ZAI_API_KEY（可选 ZAI_BASE_URL、ZAI_MODEL，默认 glm-4.6v 视觉）
  - 无 key 时走"导出/导入"模式：把逐列裁图 + prompt 导出到 档案目录/llm包/，
    人工粘贴给任意大模型，回复存成 释文草稿回复.txt 后 --import 收回。
"""
import base64
import json
import os
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
sys_path_placeholder = None


class LLMProvider:
    name = "base"

    def chat(self, prompt: str, images: list[Path] | None = None) -> str:
        raise NotImplementedError


class GLMProvider(LLMProvider):
    name = "glm"

    def __init__(self, model: str | None = None):
        self.key = os.environ.get("ZAI_API_KEY", "")
        self.base = os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4")
        self.model = model or os.environ.get("ZAI_MODEL", "glm-4.6v")
        if not self.key:
            raise RuntimeError("未设置 ZAI_API_KEY；或改用导出/导入模式：llm-transcribe --export")

    def chat(self, prompt, images=None):
        content = [{"type": "text", "text": prompt}]
        for p in images or []:
            b64 = base64.b64encode(Path(p).read_bytes()).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        body = json.dumps({"model": self.model, "messages": [{"role": "user", "content": content}],
                           "temperature": 0.2}).encode()
        req = urllib.request.Request(f"{self.base}/chat/completions", data=body,
                                     headers={"Authorization": f"Bearer {self.key}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]


def get_provider(name: str | None = None) -> LLMProvider:
    name = name or os.environ.get("CALLIGRAPHY_LLM", "glm")
    if name == "glm":
        return GLMProvider()
    raise ValueError(f"未知 LLM provider：{name}")


# ---------------------------------------------------------------- 释文草稿
TRANSCRIBE_PROMPT = """你是书法释文专家。图里是一件行草书法作品（按传统从右到左、自上而下书写）。
请逐列给出释文，并给出你对每个字的置信度（high/low）。
若能识别出是某篇著名诗文，请指出篇名与作者，并按原文校正。
只输出 JSON：{"来源": "篇名或null", "columns": ["列1释文（右起）", "列2…"], "置信度低的字": ["字…"]}"""


def export_transcription_package(prof_dir: Path) -> Path:
    """无 API 时的人工/外部 LLM 模式：导出逐列裁图与提示词。"""
    prof_dir = Path(prof_dir)
    prof = json.loads((prof_dir / "档案.json").read_text(encoding="utf-8"))
    out = prof_dir / "llm包"
    out.mkdir(exist_ok=True)
    im = Image.open(prof_dir / "作品_校正.png").convert("RGB")
    W, H = im.size
    for c in prof["layout"]["columns"]:
        x0, y0, x1, y1 = [int(v * s) for v, s in zip(c["box"], (W, H, W, H))]
        crop = im.crop((max(0, x0 - 20), max(0, y0 - 20), min(W, x1 + 20), min(H, y1 + 20)))
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
        crop.save(out / f"列{c['index'] + 1}.jpg", quality=92)
    (out / "prompt.txt").write_text(TRANSCRIBE_PROMPT, encoding="utf-8")
    (out / "整幅.jpg").write_bytes(prof_dir.joinpath("作品_校正.png").read_bytes())
    return out


def import_transcription_draft(prof_dir: Path, reply_text: str, provider: str = "export") -> dict:
    """把 LLM 回复解析为释文草稿（draft，不覆盖已确认释文）。"""
    prof_dir = Path(prof_dir)
    prof = json.loads((prof_dir / "档案.json").read_text(encoding="utf-8"))
    i, j = reply_text.find("{"), reply_text.rfind("}")
    if i < 0 or j < i:
        raise ValueError("回复里没有找到 JSON")
    data = json.loads(reply_text[i:j + 1])
    cols = [c.strip() for c in data["columns"]]
    text = "".join(cols)  # 右→左顺序拼接；列间标点由人工确认阶段补
    draft = {
        "text_raw": text,
        "per_column": cols,
        "source_text": data.get("来源"),
        "low_confidence_chars": data.get("置信度低的字", []),
        "provider": provider,
        "confidence": "llm-draft",
    }
    prof["transcription"]["draft"] = draft
    (prof_dir / "释文草稿.txt").write_text(
        f"# LLM 草稿（{provider}）——人工确认前不生效\n# 来源：{draft['source_text']}\n"
        f"# 低置信字：{'、'.join(draft['low_confidence_chars']) or '无'}\n\n{text}\n",
        encoding="utf-8")
    (prof_dir / "档案.json").write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    return draft


def draft_transcription(prof_dir: Path, provider: LLMProvider | None = None) -> dict:
    """API 模式：直接调视觉模型出释文草稿。"""
    prof_dir = Path(prof_dir)
    prof = json.loads((prof_dir / "档案.json").read_text(encoding="utf-8"))
    prov = provider or get_provider()
    reply = prov.chat(TRANSCRIBE_PROMPT, [prof_dir / "作品_校正.png"])
    return import_transcription_draft(prof_dir, reply, provider=prov.name)


# ---------------------------------------------------------------- 多音字预标注
def annotate_pronunciation(text: str) -> dict:
    """多音字预标注（本地 pypinyin，无需网络）：列出多音字与候选读音，人工/LLM 定夺。
    只产出草稿；已确认的 pronunciation 不受影响。"""
    from pypinyin import pinyin, Style
    from core.profile import PUNCT, strip_punct
    out = {}
    for ch in dict.fromkeys(strip_punct(text)):
        r = pinyin(ch, style=Style.TONE, heteronym=True)[0]
        uniq = sorted(set(r))
        if len(uniq) > 1:
            out[ch] = {"readings": uniq, "tts_char": None, "status": "draft-需定夺"}
    return out


# ---------------------------------------------------------------- 文案草稿
def draft_copy(profile: dict, project_dir: Path | None = None) -> Path:
    """基于档案与 config 生成发布文案/分镜说明草稿（模板版；LLM 版后续接入 provider）。"""
    slug = profile["slug"]
    prof_dir = Path(profile["_dir"])
    lines = [f"# {slug} · 发布文案草稿（draft，人工确认后使用）\n"]
    src = profile["transcription"].get("draft", {}).get("source_text") if isinstance(
        profile["transcription"].get("draft"), dict) else None
    lines.append(f"- 作品：《{slug}》" + (f"（{src}）" if src else ""))
    lines.append(f"- 书体：见列框确认图；署名见作品落款\n")
    lines.append("## 建议文案\n")
    lines.append(f"新作一件，书{src or slug}。慢下来，听一笔一画。\n")
    lines.append("## 核查项\n- 出处与释文是否经作者确认（确认点 2）\n- 点评类措辞是否客观\n")
    out = prof_dir / "文案草稿.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
