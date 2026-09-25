"""Shared constants for writer power analysis nodes."""

from __future__ import annotations

import re

DEFAULT_BASE_URL = ""
DEFAULT_MODEL = ""
DEFAULT_MODEL_LIST_PATH = "/v1/models"
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_TEMPERATURE = 0.35
DEFAULT_MAX_ARTICLE_CHARS = 1_000_000
DEFAULT_FALLBACK_MODELS: list[str] = []

TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")
CHAT_COMPLETIONS_RE = re.compile(r"/(?:v\d+/)?chat/completions/?$")
GEMINI_GENERATE_RE = re.compile(r"/v1beta/models/[^/]+:generateContent/?$")
DATA_PREFIX_RE = re.compile(r"^data:\s*")

BUILTIN_MODEL_IDS = [
    # Qwen 系列 (N.E.K.O 默认供应商)
    "qwen-max",
    "qwen-plus",
    "qwen-turbo",
    "qwen3.5-plus",
    "qwen3.6-flash-2026-04-16",
    "qwen3.5-flash-2025-11-12",
    # DeepSeek 系列
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "deepseek-r1",
    "deepseek-v3",
    # Claude 系列
    "claude-fable-5",
    "claude-sonnet-5",
    "claude-opus-4-8",
    "claude-haiku-4-5-20251001",
    # GLM 系列
    "glm-5.1",
    "glm-5.2",
    # Kimi 系列
    "kimi-k2.6",
    # GPT 系列
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
    # 其他
    "minimax-m2.7",
    "qwen3.6-35b-a3b",
]
