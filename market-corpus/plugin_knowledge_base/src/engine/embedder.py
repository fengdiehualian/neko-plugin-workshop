"""本地 ONNX 嵌入器：all-MiniLM-L6-v2（384 维，CPU）。

推理用宿主自带的 ``onnxruntime`` + ``tokenizers``，不需要 torch /
sentence-transformers。模型目录默认 ``<插件目录>/models/all-MiniLM-L6-v2``，
包含 ``model.onnx`` 与 ``tokenizer.json``。

均值池化（attention mask 加权）+ L2 归一化，与 sentence-transformers 的
MiniLM 用法一致。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

MODEL_FILES = ("model.onnx", "tokenizer.json")
DEFAULT_DIMENSION = 384
MAX_SEQ_LEN = 256


def _sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def model_identity(model_dir: Path) -> str:
    """由模型自身推导稳定标识（**不依赖目录名**）。

    历史版本拿模型目录的目录名当标识（``embedder.py`` 旧实现
    ``self.model_id = self._dir.name``），于是把**完全相同**的模型文件复制到
    名为 ``my_minilm`` 的目录再指向 ``models_dir``，就会被向量空间守卫判成
    「不同模型」而全量拒绝 —— 而 README 恰恰鼓励用 ``models_dir``。

    新标识 = ``<元数据里的模型名>-<model.onnx 内容 SHA-256 前 12 位>``：

    * 同一份权重在任何目录名下得到**同一**标识（守不再误报）；
    * 不同权重即使架构/元数据相同，哈希也不同（守照样拦下真正的混用）；
    * 元数据名取自 ``config.json`` 的 ``_name_or_path``（退回 ``model_type``），
      完全无元数据时退回固定串 ``onnx-model``，仍带内容哈希。
    """
    name = ""
    cfg_path = model_dir / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cfg = {}
        if isinstance(cfg, dict):
            raw = str(cfg.get("_name_or_path") or cfg.get("model_type") or "").strip()
            if raw:
                name = Path(raw.rstrip("/")).name
    if not name:
        name = "onnx-model"
    onnx = model_dir / "model.onnx"
    try:
        digest = _sha256_file(onnx)[:12] if onnx.exists() else ""
    except OSError:
        digest = ""
    # 哈希不可得（文件缺失/不可读）时才退回目录名；此时加载必然失败，
    # 标识不会被写进索引元数据。
    return f"{name}-{digest}" if digest else f"{name}-dir:{model_dir.name}"


class EmbedderError(Exception):
    """嵌入器结构化错误（模型缺失、推理失败等）。"""


class OnnxEmbedder:
    dimension = DEFAULT_DIMENSION

    def __init__(self, model_dir: str | Path, max_seq_len: int = MAX_SEQ_LEN) -> None:
        self._dir = Path(model_dir)
        self._max_len = max_seq_len
        self._tok = None
        self._count_tok = None
        self._session = None
        self._input_names: tuple[str, ...] = ()
        self.dimension = DEFAULT_DIMENSION
        # 未加载时标识为空串：绝不把目录名当模型的「身份」暴露出去
        # （需要标识的调用方必须确认 loaded 后再读，见入口层守卫）。
        # load() 成功后由 model_identity() 覆盖为基于权重内容哈希的稳定标识。
        self.model_id = ""

    # ------------------------------------------------------------------

    @property
    def loaded(self) -> bool:
        return self._session is not None

    def load(self) -> None:
        """同步加载（阻塞）。入口层请用 :meth:`load_async`，避免阻塞事件循环。"""
        self._load_sync()

    async def load_async(self) -> None:
        """在线程池中加载模型，不阻塞事件循环（CPU 密集 + 磁盘 IO）。"""
        import asyncio

        await asyncio.to_thread(self.load)

    def _load_sync(self) -> None:
        missing = [f for f in MODEL_FILES if not (self._dir / f).exists()]
        if missing:
            raise EmbedderError(
                "embedding model files missing in "
                f"{self._dir}: {', '.join(missing)}. "
                "运行 kb_model_download 入口下载（默认镜像 hf-Mirror.com），"
                "或手动放置 sentence-transformers/all-MiniLM-L6-v2 的 ONNX 导出。"
            )
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            self._tok = Tokenizer.from_file(str(self._dir / "tokenizer.json"))
            self._tok.enable_truncation(max_length=self._max_len)
            self._tok.enable_padding(pad_id=0, pad_token="[PAD]")
            # 计数专用：不截断不填充，分块预算用真实 token 数。
            # tokenizer.json 自带 truncation(max_length=128)/padding 配置，
            # from_file 会照单全收，必须显式关断，否则长段落计数恒被压在 128。
            self._count_tok = Tokenizer.from_file(str(self._dir / "tokenizer.json"))
            self._count_tok.no_truncation()
            self._count_tok.no_padding()
            self._session = ort.InferenceSession(
                str(self._dir / "model.onnx"), providers=["CPUExecutionProvider"]
            )
            self._input_names = tuple(i.name for i in self._session.get_inputs())
            # 探测真实输出维度（不信任预设 384）
            probe = self.embed(["dimension probe"])
            self.dimension = int(probe.shape[1])
            self.model_id = model_identity(self._dir)
        except EmbedderError:
            raise
        except Exception as exc:
            self._session = None
            raise EmbedderError(f"embedding model load failed: {exc}") from exc

    def _require(self):
        if self._session is None or self._tok is None:
            raise EmbedderError("embedder not loaded; call load() first")
        return self._tok, self._session

    # ------------------------------------------------------------------

    def embed(self, texts: list[str]) -> np.ndarray:
        """批量嵌入，返回 (n, 384) 的 L2 归一化矩阵。空列表返回 (0, 384)。"""
        tok, session = self._require()
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        try:
            enc = tok.encode_batch(list(texts))
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed: dict = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._input_names:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = session.run(None, feed)[0]  # last_hidden_state (n, seq, h)
        except Exception as exc:
            raise EmbedderError(f"embedding inference failed: {exc}") from exc
        m = mask[:, :, None].astype(np.float32)
        summed = (hidden * m).sum(axis=1)
        counts = np.clip(m.sum(axis=1), 1e-9, None)
        emb = summed / counts
        norm = np.linalg.norm(emb, axis=1, keepdims=True)
        return emb / np.clip(norm, 1e-12, None)

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]

    def encode(self, text: str):
        """暴露 tokenizer.encode 供分块器做 token 预算（**不截断**，真实计数）。"""
        _tok, _ = self._require()
        return self._count_tok.encode(text)

    def decode(self, ids) -> str:
        """token id 序列 -> 文本（分块滑窗回读用）。"""
        _tok, _ = self._require()
        return self._count_tok.decode(list(ids), skip_special_tokens=True)
