"""Public RapidOCR adapter for upright game UI, with isolated pinned models."""

from __future__ import annotations

from hashlib import sha256

MODEL_FILES = {
    "ch_PP-OCRv5_det_mobile.onnx": "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
    "ch_PP-OCRv5_rec_mobile.onnx": "5825fc7ebf84ae7a412be049820b4d86d77620f204a041697b0494669b1742c5",
}
MODEL_SOURCE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.8.0/onnx/PP-OCRv5"


def verified_models(root):
    return all((root / name).is_file() and sha256((root / name).read_bytes()).hexdigest() == digest
               for name, digest in MODEL_FILES.items())


class UprightOCRBackend:
    def __init__(self, model_root=None):
        from rapidocr_onnxruntime import RapidOCR

        self.model = "PP-OCRv5/ch/mobile" if model_root else "PP-OCRv4/ch/mobile"
        args = {"use_cls": False, "intra_op_num_threads": 2, "inter_op_num_threads": 1}
        if model_root:
            if not verified_models(model_root):
                raise ValueError("插件 OCR 模型缺失或校验失败")
            args.update(det_model_path=str(model_root / "ch_PP-OCRv5_det_mobile.onnx"),
                        rec_model_path=str(model_root / "ch_PP-OCRv5_rec_mobile.onnx"))
        # Game HUD text is upright. Do not load incompatible direction models
        # or monkey-patch shared runtime/session defaults in the host.
        self.engine = RapidOCR(**args)

    def is_available(self):
        return self.engine is not None

    def extract_text_with_boxes(self, image):
        import numpy as np

        if self.engine is None:
            raise RuntimeError("插件 OCR 引擎已关闭")
        values, _ = self.engine(np.asarray(image.convert("RGB")), use_cls=False)
        boxes = []
        for points, text, score in values or []:
            boxes.append({"text": text, "score": float(score), "left": min(p[0] for p in points),
                          "right": max(p[0] for p in points), "top": min(p[1] for p in points), "bottom": max(p[1] for p in points)})
        return "\n".join(b["text"] for b in boxes), boxes

    def recognize_line(self, image):
        """Public RapidOCR rec-only API for an already detected single text line."""
        import numpy as np

        if self.engine is None:
            raise RuntimeError("插件 OCR 引擎已关闭")
        values, _ = self.engine(np.asarray(image.convert("RGB")), use_det=False, use_cls=False, use_rec=True)
        return [(str(item[0]), float(item[1])) for item in values or [] if len(item) >= 2]

    def close(self):
        self.engine = None
