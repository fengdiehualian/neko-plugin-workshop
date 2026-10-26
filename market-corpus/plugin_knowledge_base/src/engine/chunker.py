"""分块：按段落切分，默认预算 240 tokens，重叠 64。

计数规则（关键）：预算取 ``max(词表 token 数, CJK 字符数)``。本插件默认的
all-MiniLM-L6-v2 是英文词表，连续汉字会折叠成极少量 [UNK] token（实测 1080 字
≈128 token），按词表计数会让中文长段落永远不触发滑窗——而每个汉字 ≈ 1 token
与中文 BERT 系分词惯例一致。CJK 主导的段落按字符滑窗，其余按 token id 滑窗。

ATX 标题（# 开头行）并入其后的段落，保证标题语义随块保留。
默认 240：MiniLM 推理窗口 256，扣除特殊 token 后块预算取 240 才能整块覆盖；
可用 config ``[knowledge] chunk_tokens/overlap_tokens`` 调整。
"""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_PARA_SPLIT_RE = re.compile(r"\n\s*\n")
_CJK_RANGES = (
    (0x3400, 0x4DBF),   # CJK 扩展 A
    (0x4E00, 0x9FFF),   # CJK 基本区
    (0xF900, 0xFAFF),   # 兼容表意文字
    (0x20000, 0x2A6DF), # 扩展 B
)


def _cjk_count(text: str) -> int:
    return sum(
        1
        for ch in text
        if any(lo <= ord(ch) <= hi for lo, hi in _CJK_RANGES)
    )


def _token_count(text: str, encode_fn) -> int:
    enc = encode_fn(text)
    ids = getattr(enc, "ids", enc)
    return len(ids)


def _merge_headings(text: str) -> list[str]:
    """段落切分 + 标题并入后随段落。"""
    raw = [p.strip() for p in _PARA_SPLIT_RE.split(text) if p.strip()]
    merged: list[str] = []
    pending_head: list[str] = []
    for para in raw:
        lines = para.splitlines()
        if lines and _HEADING_RE.match(lines[0]):
            pending_head.append(para)
            continue
        if pending_head:
            merged.append("\n\n".join(pending_head + [para]))
            pending_head = []
        else:
            merged.append(para)
    if pending_head:
        merged.append("\n\n".join(pending_head))
    return merged


def chunk_text(
    text: str,
    encode_fn,
    decode_fn,
    max_tokens: int = 240,
    overlap: int = 64,
) -> list[str]:
    """把文本切成块。

    encode_fn(text) -> Encoding（需 .ids）；decode_fn(ids) -> str。
    短段落合并成块；超长段落按预算滑窗（重叠 overlap）：CJK 主导按字符，
    其余按 token id。
    """
    if max_tokens <= overlap:
        raise ValueError("max_tokens must be greater than overlap")
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len
        if buf:
            chunks.append("\n\n".join(buf))
            buf, buf_len = [], 0

    for para in _merge_headings(text):
        n_ids = _token_count(para, encode_fn)
        cjk = _cjk_count(para)
        budget = max(n_ids, cjk)
        if budget <= 0:
            continue
        if budget > max_tokens:
            flush()
            step = max_tokens - overlap
            if cjk >= n_ids:
                # CJK 主导：词表折叠使 token 流失去粒度，按字符滑窗（1 字≈1 token）
                for start in range(0, len(para), step):
                    piece = para[start : start + max_tokens].strip()
                    if piece:
                        chunks.append(piece)
                    if start + max_tokens >= len(para):
                        break
            else:
                ids = encode_fn(para).ids
                for start in range(0, len(ids), step):
                    window = ids[start : start + max_tokens]
                    if len(window) <= overlap and start > 0:
                        break  # 尾窗完全被上一窗覆盖时丢弃
                    piece = decode_fn(window).strip()
                    if piece:
                        chunks.append(piece)
                    if start + max_tokens >= len(ids):
                        break
            continue
        if buf_len + budget + 1 > max_tokens:
            flush()
        buf.append(para)
        buf_len += budget + 1
    flush()
    return [c for c in chunks if c.strip()]
