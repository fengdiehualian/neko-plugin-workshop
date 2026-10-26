"""场上单位兜底识别：卡面图像匹配（Python 3.11，numpy 实现，无 cv2/imagehash）。

候选池由卡组代码 + 对手国家决定；打分 = dHash 相似度 + 颜色直方图相关。
预留 ``embed_fn`` 接口：若 pHash 精度不达标（50 例核对 <80%），换成
DINOv2-small ONNX 特征而不动调用方。卡名 OCR 校验用 difflib 模糊比对。
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

import numpy as np
from PIL import Image

from .capture import hamming

_HASH_SIZE = 8  # 64 位 dHash
_HIST_BINS = 4  # 每通道 4 bin -> 64 维


@dataclass(frozen=True)
class ImageFeature:
    digest: int
    hist: tuple[float, ...]


def _dhash_array(gray: np.ndarray, size: int = _HASH_SIZE) -> int:
    small = np.asarray(Image.fromarray(gray).resize((size + 1, size), Image.BILINEAR), dtype=np.int16)
    bits = (small[:, 1:] > small[:, :-1]).ravel()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def _features(image: Image.Image) -> ImageFeature:
    """64 位 dHash + 4x4 空间颜色直方图（L2 归一化，与 match 打分配套）。"""
    rgb = image.convert("RGB").resize((64, 64), Image.BILINEAR)
    arr = np.asarray(rgb, dtype=np.float32)
    digest = _dhash_array(np.asarray(image.convert("L")))
    spatial = np.zeros((3, _HIST_BINS, _HIST_BINS), dtype=np.float32)
    block = 64 // _HIST_BINS
    for gy in range(_HIST_BINS):
        for gx in range(_HIST_BINS):
            patch = arr[gy * block:(gy + 1) * block, gx * block:(gx + 1) * block]
            for c in range(3):
                index = int(min(_HIST_BINS - 1, max(0, patch[:, :, c].mean() * _HIST_BINS / 256)))
                spatial[c, gy, index] += 1.0
    vector = spatial.reshape(-1)
    norm = float(np.linalg.norm(vector)) or 1.0
    vector = vector / norm
    return ImageFeature(digest=digest, hist=tuple(vector.tolist()))


class CardMatcher:
    """候选池特征索引 + 最近邻匹配。"""

    def __init__(self, *, min_score: float = 0.72,
                 embed_fn: Callable[[Image.Image], np.ndarray] | None = None) -> None:
        self._pool: dict[str, ImageFeature] = {}
        self._names: dict[str, str] = {}
        self._min_score = float(min_score)
        # DINOv2 升级钩子：签名 image -> 单位向量 ndarray；提供后优先于 pHash
        self.embed_fn = embed_fn

    def build(self, items: Iterable[tuple[str, str, Image.Image]]) -> int:
        """items: (card_id, name, 卡图缩略)。重建索引，返回池大小。"""
        self._pool.clear()
        self._names.clear()
        for card_id, name, image in items:
            self._pool[card_id] = _features(image)
            self._names[card_id] = name
        return len(self._pool)

    def match(self, crop: Image.Image) -> tuple[str | None, float]:
        if not self._pool:
            return None, 0.0
        if self.embed_fn is not None:
            try:
                return self._match_embedded(crop)
            except Exception:
                pass  # 嵌入模型异常回退 pHash
        probe = _features(crop)
        best_id, best_score = None, -1.0
        probe_vector = np.asarray(probe.hist, dtype=np.float32)
        for card_id, feature in self._pool.items():
            hash_sim = 1.0 - hamming(probe.digest, feature.digest) / 64.0
            hist_sim = float(np.dot(probe_vector, np.asarray(feature.hist, dtype=np.float32)))
            score = 0.6 * hash_sim + 0.4 * hist_sim
            if score > best_score:
                best_id, best_score = card_id, score
        if best_id is None or best_score < self._min_score:
            return None, max(0.0, best_score)
        return best_id, best_score

    def _match_embedded(self, crop: Image.Image) -> tuple[str | None, float]:
        raise NotImplementedError("DINOv2 升级位：接入嵌入模型时实现（需宿主侧加载 ONNX）")

    @staticmethod
    def confirm_name(ocr_name: str, candidate_name: str) -> float:
        """OCR 卡名与候选名的一致性 0~1（艺术字体常吞空格/字符）。"""
        a = (ocr_name or "").replace(" ", "").lower()
        b = (candidate_name or "").replace(" ", "").lower()
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()
