"""文件扫描与文本抽取：.md / .txt 直接读，.pdf 用 pypdf 逐页抽取。"""

from __future__ import annotations

import hashlib
from pathlib import Path

SUPPORTED_SUFFIXES = {".md", ".txt", ".pdf"}


def scan_directory(root: str | Path, recursive: bool = True) -> list[Path]:
    """列出目录下受支持的文件（按路径排序，保证导入顺序稳定）。"""
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"knowledge directory not found: {root}")
    it = root.rglob("*") if recursive else root.glob("*")
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES)


def read_document(path: str | Path) -> str:
    """读取文档为纯文本。解析失败抛结构化异常由入口转 Err。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported file type: {suffix} (supported: {sorted(SUPPORTED_SUFFIXES)})")
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            pages = []
            for i, page in enumerate(reader.pages):
                t = page.extract_text() or ""
                if t.strip():
                    pages.append(t.strip())
            return "\n\n".join(pages)
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise ValueError(f"failed to read {path.name}: {exc}") from exc


def content_hash(text: str) -> str:
    """内容哈希（SHA-256 前 32 hex），用于热数据去重。"""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:32]
