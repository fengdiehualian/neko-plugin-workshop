# THIRD_PARTY_NOTICES.md — 模板 / 待构建清单（禁止直接发布）

> **STATUS: BLOCKED_PENDING_BUILD**
>
> 本文件只是离线包第三方声明模板，不是最终声明。当前没有 `vendor/` 运行时、
> 没有构建生成的锁定清单、没有逐项核验的许可证文本，因此**不得**随离线包发布，
> 也不得据此对外声称任何第三方组件的许可证名称、版本、版权年份或来源 URL。

## 发布拒绝门（publication refusal gate）

构建/发布脚本必须逐项确认以下条件；任一未满足即拒绝发布，而不是用占位文本过关：

- [ ] 离线包由隔离的 **CPython 3.11.x / win_amd64 / CPU-only** 构建生成，未复制
      当前 Python 3.14 环境或 N.E.K.O 宿主解释器的任何包。
- [ ] `vendor/python/`、`vendor/python/Lib/site-packages/` 与模型文件均存在，
      且 `bundle-manifest.json` 中每个产物的版本、文件名、大小、SHA-256 已填写。
- [ ] `worker-runtime.json`（`kards-worker-runtime/1`）与
      `bundle-manifest.json`（`neko-kards-bundle-manifest/1`）是两份独立文件；
      worker 只读后者，未生成或覆盖它。
- [ ] `worker/package_check.py` 自检为 `ready`（不是 `blocked`/`degraded`），
      且所有依赖的 import origin 位于包内，而不是系统/用户 site-packages。
- [ ] 每个第三方组件的**实际许可证文本**已从对应发行物中提取并归档；未核验的
      组件只能保持“pending”，不得补写猜测的许可证。
- [ ] 每个组件的版本号、版权行、上游项目 URL 与实际发行物一致；不得虚构版本或
      版权年份。
- [ ] `kards_det.onnx` 的来源、训练衍生权利与分发许可已单独核验；未核验时该模型
      必须标记为 `required=false` 或不随包发布。
- [ ] `requirements-bundle.txt` 已由构建替换为生成的锁定清单（含哈希），而不是
      当前的 `BLOCKED_PENDING_BUILD` 模板。
- [ ] 本文件已由构建生成最终版本，并移除 `BLOCKED_PENDING_BUILD` 标记。

## 待构建清单（仅名称，不作版本/许可声明）

以下只是构建候选，**不代表已经确定版本，也不代表已经确认许可证**：

| 组件 | 用途 | 版本 | 许可证 | 版权/来源 | 状态 |
| --- | --- | --- | --- | --- | --- |
| CPython embeddable runtime (3.11.x, win_amd64) | 包内解释器 | 待构建 | 待核验 | 待构建 | pending |
| NumPy | 数值/图像后处理 | 待构建 | 待核验 | 待构建 | pending |
| Pillow | 图像解码/采集回退 | 待构建 | 待核验 | 待构建 | pending |
| opencv-python-headless（模块 `cv2`，可选） | 可选视觉能力 | 待构建 | 待核验 | 待构建 | pending |
| onnxruntime（CPU，可选） | YOLO/OCR 推理 | 待构建 | 待核验 | 待构建 | pending |
| RapidOCR / rapidocr-onnxruntime（可选） | HUD OCR | 待构建 | 待核验 | 待构建 | pending |
| `kards_det.onnx`（可选） | 单位检测模型 | 待构建 | 待核验 | 待核验 | pending |

## 构建时必须写入的字段

最终发布版本必须为每个组件补充：

- 精确版本号与 wheel/artifact 文件名；
- SHA-256；
- 许可证名称（以实际文本为准）与许可证文件路径；
- 版权声明原文；
- 上游项目/发行物 URL；
- 是否修改、如何修改（如有）；
- 对应源码获取方式（如许可证要求）。

## 本插件自身

本仓库代码按根目录 `LICENSE` 分发（AGPL-3.0）。这一行不构成对任何第三方组件
许可证的推断或替代；第三方组件仍以构建时核验的许可证文本为准。

## 当前阻塞

- `vendor/python/` 不存在；
- `vendor/python/Lib/site-packages/` 不存在；
- `models/kards_det.onnx` 不存在；
- `worker-runtime.json`、`bundle-manifest.json`、`assets/manifest.json`、
  `ocr_models/runtime.json` 尚未由构建生成；
- `requirements-bundle.txt` 尚未由构建生成；
- 第三方许可证文本与版本尚未核验。

在以上阻塞解除前，离线包只能标记为 **blocked**，不得发布。
