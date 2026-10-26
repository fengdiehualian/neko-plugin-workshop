# 离线运行时与 worker（第一阶段）

本文件记录离线包第一阶段的实现边界、路径约定、自检语义和 JSON-lines 协议。
第一阶段只搭好 **包内路径解析 + 自检 + 协议骨架**，不包含模型推理、不联网、
不安装依赖、不启动宿主，也不修改任何安装副本。

当前仓库状态是 **blocked by design**：

- `vendor/python/` 不存在；
- `vendor/python/Lib/site-packages/` 不存在；
- `models/kards_det.onnx` 不存在（本包不附带该模型）；
- 可选的 `worker-runtime.json` 尚未由构建生成（缺失时只报告 bundle manifest
  components，依赖状态留空，状态不会是 `ready`）；
- 分发用 `bundle-manifest.json` 尚未由 `scripts/bundle_manifest.py` 生成；
- `assets/cards/manifest.json`（或 `assets/manifest.json`）、
  `ocr_models/runtime.json`（或 `worker/models/ocr/runtime.json`）不存在；
- `requirements-bundle.txt` 只是待构建模板，不是 pip lock。

因此 `python worker/bootstrap.py --check` 必须返回 `status=blocked`，并把
`kards_det` 报告为 `missing` / `ready=false`。检查过程不会创建 `vendor/`、
`models/`、`assets/`、`ocr_models/` 或任何目录，也不会下载资源，更不会生成或
覆盖 `bundle-manifest.json`。

## 1. 为什么优先使用独立 worker，而不是把 vendor 塞进宿主进程

N.E.K.O 主进程可能已经导入了它自己的 `numpy`、Pillow、`onnxruntime` 等扩展。
即使同为 CPython 3.11，二进制扩展仍绑定具体的 wheel 构建、`python311.dll`、
`_multiarray_umath.pyd`、OpenMP/MKL/VC runtime 等 DLL。把离线包的
`vendor/python/Lib/site-packages` 追加进同一个进程的 `sys.path` 会带来：

- **先导入者获胜**：`import numpy` 只会命中已缓存的宿主版本；包内版本、ABI、
  `numpy._core` / `_multiarray_umath` 的 C 符号可能不匹配，表现为
  `ImportError`、`AttributeError`、DLL load failed，甚至进程崩溃；
- **DLL 搜索路径冲突**：`onnxruntime`、`cv2` 等会加载同名 native DLL；宿主与
  vendor 的版本/架构不一致时，错误可能发生在 import 之后，难以归因；
- **无法证明离线完整性**：同进程无法区分模块来自包内还是宿主/用户
  site-packages，自检会失去意义；
- **升级互相污染**：宿主的 import 缓存、全局配置和线程池会被 worker 代码影响。

所以正式集成方向是：**main 进程保留 N.E.K.O SDK 边界**；worker 作为独立子进程，
由包内 `vendor/python/python.exe`（隔离的 `_pth`/干净环境）启动
`worker/bootstrap.py --serve`，只通过 stdin/stdout 的 JSON-lines 通信。worker
**不导入** `plugin.sdk`，**不向宿主 push**；main 进程负责把结构化状态转换成宿主
消息。第一阶段不实现 main 侧启动器，只固定 worker 边界和协议。

## 2. 包内布局

所有路径都从 bundle root 派生，默认 root 是 `worker/` 的父目录
（`Path(__file__).resolve().parent.parent`），显式 `--bundle-root` 必须是
Windows 绝对盘符路径。

```text
kards_companion/                    bundle root
├─ worker/
│  ├─ bootstrap.py                  入口；--check / --serve
│  ├─ protocol.py                   JSON-lines 协议 v1
│  └─ package_check.py              路径解析 + 离线自检
├─ vendor/                          第一阶段不存在
│  └─ python/
│     ├─ python.exe                 vendored CPython 3.11
│     ├─ python311.dll
│     └─ Lib/site-packages/         唯一允许的第三方依赖来源
├─ models/
│  └─ kards_det.onnx                第一阶段不存在；缺失时绝不标 ready
├─ assets/
│  └─ cards/manifest.json           分发包资源 marker；缺失即 blocked
├─ ocr_models/
│  └─ runtime.json                  OCR runtime marker；缺失即 blocked
├─ worker-runtime.json              可选 worker 自检元数据；缺失时仅报告 bundle components
├─ worker-runtime.schema.json       worker runtime 契约（kards-worker-runtime/1）
├─ bundle-manifest.json             唯一分发清单，仅由 scripts/bundle_manifest.py 生成；worker 只读
├─ bundle-manifest.schema.json      分发清单契约（neko-kards-bundle-manifest/1）
├─ requirements-bundle.txt          待构建候选模板（不是 lock）
└─ THIRD_PARTY_NOTICES.md           待构建声明模板（禁止直接发布）
```

两套 JSON 分工固定：

| 文件 | schema | 所有者 | 用途 |
| --- | --- | --- | --- |
| `worker-runtime.json` | `kards-worker-runtime/1` | worker/离线构建（**可选**） | `requires_python`、`platform`、`cpu_only`、runtime 路径、依赖与模型声明 |
| `bundle-manifest.json` | `neko-kards-bundle-manifest/1` | `scripts/bundle_manifest.py` | 分发 payload 文件清单、required/optional marker、components、integrity |

worker 只读 `bundle-manifest.json`，绝不生成/覆盖它。没有这张分发清单、
`assets/cards/manifest.json`、`ocr_models/runtime.json` 或 `vendor/` 时，自检
保持 `blocked`。`worker-runtime.json` 只是可选的 worker 自检元数据；缺失时
worker 报告 bundle manifest 的 `components`、`required`、`optional`，并把
`dependencies` 留空，不会伪造依赖状态。它不会写同名 `bundle-manifest.json`。

路径规则：

- bundle root 与所有成员路径最终都是 Windows 绝对路径；
- 解析不读取 `PATH`，不使用 `%APPDATA%` 用户 site-packages，不调用
  `site.getusersitepackages()` 作为查找来源；
- 显式 root 拒绝相对路径、UNC/设备路径、包含 `..` 的原始路径；
- `worker-runtime.json` 存在时，其中的 `runtime.*`、`models[].path`、
  `files[].path` 必须是 bundle-relative、以 `/` 分隔、无盘符/冒号/反斜杠/
  空段/`.`/`..`；
- `bundle-manifest.json` 的 `files[].path`、`required.*`、`optional.kards_det`
  和 `components` 前缀都做同样的越界校验，且只允许 `worker/`、`vendor/`、
  `assets/`、`models/`、`ocr_models/` 与根目录第三方声明；
- worker 校验 manifest 的 `components` 与 `files` 一致性、`integrity` 指纹，
  并对 required marker 和可选模型做实际哈希；其余 payload 文件的逐文件哈希
  仍由 `scripts/bundle_manifest.py --check` 负责；
- 解析后的真实路径（含 symlink/junction 解析）必须仍位于 bundle root 内，
  否则返回 `unsafe_path`；
- 检查只读：不 `mkdir`、不写文件、不替换安装副本。

## 3. 离线自检

`worker/package_check.py` 的 `check_bundle()` 返回一个可 JSON 序列化的对象，
CLI 为：

```bat
py -3.11 worker/bootstrap.py --check
```

退出码：`0` = `ready`，`2` = `blocked`/`degraded`，`64` = 参数错误。
输出只有一行 JSON（stdout），不会混入日志或 traceback。

> 在构建完成的离线包内，必须用 **包内解释器** 运行自检才能得到权威的
> `ready`：`vendor\python\python.exe worker\bootstrap.py --check`。用系统
> Python 运行时，依赖的 import origin 不在 bundle 内，只能得到 `external`
> 或 `degraded`/`blocked`；这是有意的 fail-closed，不是用于声称包内依赖可用。

主要字段：

| 字段 | 含义 |
| --- | --- |
| `schema` | `kards-offline-runtime-check/1` |
| `status` | `ready` / `degraded` / `blocked` |
| `bundle_root` | 解析后的绝对路径 |
| `runtime_root` | vendored Python 根目录（默认 `vendor/python`） |
| `compatibility` | `requires_python="3.11"`, `platform="win_amd64"`, `cpu_only=true`，以及当前解释器是否匹配/是否来自包内 |
| `worker_runtime` | 可选 `worker-runtime.json`：`loaded` / `absent` / `invalid`、schema、bundle id/version、错误列表 |
| `distribution` | `bundle-manifest.json`、`assets/cards/manifest.json`、`ocr_models/runtime.json`、`vendor/`、第三方声明的存在/解析状态；manifest 的 `components` / `required` / `optional` |
| `components` | 直接来自 `bundle-manifest.json` 的 component 元数据（`version`、`license`、`file_count`、`size_bytes`） |
| `python` | `python_exe`、`site_packages`、是否存在、`python311.dll`、当前解释器信息 |
| `dependencies` | `worker-runtime.json` 存在时为 `numpy` / `PIL` / `cv2` / `onnxruntime` / `rapidocr` 的逐项状态；不存在时为 `{}` |
| `model.kards_det` | `missing` / `present_unverified` / `present_verified` / `ready` 等 |
| `blockers` | 会导致 `blocked` 的结构化问题 |
| `warnings` | 能力降级或环境提示（部分会令状态变为 `degraded`） |
| `blocked_resources` / `degraded_resources` | 资源级归因 |

依赖状态：

- `ready`：能 import，且 import origin 位于 bundle 的
  `vendor/python` 或 `vendor/python/Lib/site-packages` 内；
- `external`：只能从宿主/系统/用户 site-packages import；不满足离线保证；
- `missing`：没有可 import 的模块；
- `broken`：找到包内模块但 import 失败（常见于 ABI/DLL 不匹配）。

`numpy`、`PIL` 默认视为硬依赖；`cv2`、`onnxruntime`、`rapidocr` 默认是可选能力。
`worker-runtime.json` 存在时，它可以把可选能力升级为必需，但不能把 `numpy`/`PIL`
降级；外部/缺失的硬依赖是 blocker，可选能力缺失只令结果降级为 `degraded`。
`worker-runtime.json` 缺失时 worker **不探测** 依赖，`dependencies` 为 `{}`，
只报告 `bundle-manifest.json` 的 component entries，并产生
`worker_runtime_absent` warning（状态最高为 `degraded`，不会是 `ready`）。

`kards_det` 的判定是保守的：

- 文件不存在：`state="missing"`, `ready=false`；
- 存在但没有声明：`present_unverified`, `ready=false`；
- 声明来源可以是可选的 `worker-runtime.json`（优先），或
  `bundle-manifest.json` 的 `optional.kards_det`；
- 声明了 `sha256` 但校验不匹配：`checksum_mismatch`, blocker；
- 哈希匹配但 `onnxruntime` 不可用：`present_verified`, `ready=false`；
- 只有 **声明 + 大小/哈希匹配 + `onnxruntime=ready`** 才是 `ready`。

缺失模型永远不会被伪造成 `ready`。

分发 readiness 同样 fail-closed：

- `bundle-manifest.json` 缺失、JSON 无效、schema 不是
  `neko-kards-bundle-manifest/1`、`files[]` 路径越界、required marker 缺失、
  components 与 files 不一致、integrity 指纹不符：blocker；
- `files[]` 声明的非可选文件缺失或大小不符：blocker；`models/kards_det.onnx`
  是 optional，缺失只产生 warning；
- `assets/cards/manifest.json`（兼容 `assets/manifest.json`）或
  `ocr_models/runtime.json`（兼容 `worker/models/ocr/runtime.json`）缺失、为空、
  非 JSON object：blocker；
- `vendor/` 缺失：blocker；
- `THIRD_PARTY_NOTICES` 缺失：blocker；仍是 `BLOCKED_PENDING_BUILD` 模板：
  warning（使状态至少为 `degraded`，不允许发布）；
- worker 只读 `bundle-manifest.json`，不生成、不覆盖；required marker 与可选
  模型做实际哈希，其余 payload 文件由分发脚本做完整校验。

## 4. JSON-lines 协议 v1

入口：

```bat
vendor\python\python.exe worker\bootstrap.py --serve
```

一请求一行、一响应一行，UTF-8 JSON。stdout 只允许协议对象；诊断信息只能写
stderr。worker 不导入宿主 SDK、不调用 `push_message`、不发送图片、不做 base64
全帧传输。

请求：

```json
{"v":1,"id":"req-1","op":"ping","session_id":"match-1","frame_id":42,"timeout_ms":1000,"payload":{}}
```

字段约束：

- `v` 必须等于 `1`；其他版本返回 `unsupported_protocol_version`；
- `id` 必填，匹配 `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`；
- `op`：`ping`、`selfcheck`、`shutdown`；`frame.process` 被识别但本阶段返回
  `not_implemented`；其他 op 返回 `unsupported_op`；
- `session_id`：可选；`frame.process` 必填；最长 128，字符集同上；
- `frame_id`：可选；`frame.process` 必填；整数 `1..2^63-1`；
- `timeout_ms`：可选，默认 `15000`，允许 `1..60000`；
- `payload` 必须是对象；禁止 `frame_b64`、`image_b64`、`*_bytes`、`pixels`、
  `screenshot`、`data:image/...;base64,...` 等图像载荷；单个内联字符串上限
  16384 字符；嵌套深度上限 16。

响应：

```json
{"v":1,"id":"req-1","session_id":"match-1","frame_id":42,"ok":true,"state":{"op":"ping"}}
{"v":1,"id":"req-1","ok":false,"error":{"code":"timeout","message":"...","retryable":true}}
```

只有 `state` 或 `error` 两种结果；不回显请求 payload，不包含宿主推送字段。
`session_id` / `frame_id` 在语法合法时回显，便于关联。

长度上限：

- 单行请求/响应 1,048,576 字节（不含行尾 CRLF）；超限返回 `line_too_large`，
  输入流会丢弃该行剩余部分后继续；
- 响应超限返回 `response_too_large`。

超时语义（第一阶段）：

- `timeout_ms` 是处理预算，从 worker 接收完整行后开始计时；
- handler 是进程内协作式执行；若返回时已超过 deadline，结果被丢弃，返回
  `error.code="timeout"`、`retryable=true`；
- 第一阶段**不**强杀 handler、不创建线程/子进程；真正需要硬取消的长操作留到
  后续阶段做隔离；
- 超时不会导致 worker 退出，后续请求继续处理。

常用错误码：`invalid_json`、`invalid_encoding`、`line_too_large`、
`invalid_request`、`unsupported_protocol_version`、`invalid_request_id`、
`invalid_session_id`、`invalid_frame_id`、`missing_session_id`、
`missing_frame_id`、`invalid_timeout`、`payload_too_deep`、
`inline_payload_too_large`、`binary_payload_rejected`、`unsupported_op`、
`not_implemented`、`timeout`、`internal_error`、`response_too_large`。

## 5. 两套 JSON 契约（不互相覆盖）

### 5.1 `worker-runtime.json` — 可选的 worker 自检元数据

`worker-runtime.schema.json` 固定了 `requires_python="3.11"`、
`platform="win_amd64"`、`cpu_only=true`。构建**可以**生成
`worker-runtime.json`，至少包含：

- `schema` = `kards-worker-runtime/1`；
- `bundle_id`、`bundle_version`、`generated_at`；
- `runtime.python_root` / `python_exe` / `site_packages`（bundle-relative）；
- `dependencies[]`（`name`、`import_names`、`required`，可选精确 `version`）；
- `models[]`（`id`、`path`、`required`、**`sha256`**、`size_bytes`）；
- 可选 `files[]`（路径 + 哈希 + 大小）。

这是 **worker 专属、可选** 的文件；不要把它命名为 `bundle-manifest.json`，
也不要让分发脚本写入它。缺失时 worker 不探测依赖、`dependencies` 为 `{}`，
只报告 `bundle-manifest.json` 的 `components` / `required` / `optional`，并产生
`worker_runtime_absent` warning（状态最多为 `degraded`）。存在时 worker 对其中
路径做遍历/越界校验，并只在模型实际存在、大小/哈希匹配、依赖从包内 import 时
报告对应能力可用。若希望 `--check` 达到 `ready`，构建必须生成该文件。

### 5.2 `bundle-manifest.json` — 唯一分发清单

`bundle-manifest.json` 的 schema 固定为 `neko-kards-bundle-manifest/1`，由
`scripts/bundle_manifest.py` 生成和校验，覆盖 `worker/`、`vendor/`、`assets/`、
`models/`、`ocr_models/` 与根目录第三方声明。根目录
`bundle-manifest.schema.json` 只描述这一套 schema。worker **只读** 该文件，用于：

- 确认分发清单存在、schema 正确、顶层字段形状正确；
- 校验 `files[]` 的路径/大小/哈希/component/version/license，以及
  `components` 与 files 的一致性；
- 检查 `required` marker 的 `present`、expected 路径、对应文件存在和哈希；
- 检查 `optional.kards_det` 的 state/size/sha256；
- 校验 `integrity.file_count` / `total_bytes` / `sha256` 指纹；
- 与 `assets/cards/manifest.json`（兼容 `assets/manifest.json`）、
  `ocr_models/runtime.json`（兼容 `worker/models/ocr/runtime.json`）、`vendor/`
  的实际存在状态交叉检查。

未列出文件、reparse point 和其余 payload 文件的逐文件哈希仍由
`scripts/bundle_manifest.py --check` 负责；worker 不写回、不覆盖、也不生成
manifest。`worker-runtime.json` 缺失只产生 warning；分发清单或分发 marker 缺失
仍产生 blocker，因此两者都缺失时状态仍是 `blocked`。

## 6. 构建与发布门

`requirements-bundle.txt` 当前是 `BLOCKED_PENDING_BUILD` 模板：只有候选组件名，
**没有版本 pin、没有哈希、不是可直接安装的 lock**。构建必须：

1. 在隔离的 CPython **3.11.x / win_amd64 / CPU-only** 环境解析并锁定依赖；
2. 为每个 wheel/产物写入精确版本、文件名、SHA-256；
3. 绝不复制当前 Python 3.14 环境或 N.E.K.O 宿主解释器的 site-packages；
4. 只使用 CPUExecutionProvider；不携带 CUDA/DirectML/TensorRT；
5. 生成可选的 `worker-runtime.json`（schema `kards-worker-runtime/1`）；缺失时
   `--check` 最多为 `degraded`，因此要得到 `ready` 必须生成它，并证明所有
   import origin 在 bundle 内；
6. 用 `scripts/bundle_manifest.py --generate` 生成分发清单
   `bundle-manifest.json`（schema `neko-kards-bundle-manifest/1`）；worker
   只读该文件，绝不覆盖；
7. 用构建生成的 lock 替换 `requirements-bundle.txt` 模板；
8. 确认 `assets/cards/manifest.json`（兼容 `assets/manifest.json`）、
   `ocr_models/runtime.json`（兼容 `worker/models/ocr/runtime.json`）、
   `vendor/` 和 `THIRD_PARTY_NOTICES` 都存在；任一缺失都保持 `blocked`。

`THIRD_PARTY_NOTICES.md` 同样是模板。未核验许可证文本、版本、版权行和来源前，
不得发布，也不得补写猜测值。发布脚本必须执行其中的“发布拒绝门”检查。

## 7. 测试与验证

第一阶段 owned tests：

```bat
py -3.11 -m pytest tests/test_bundle_runtime.py -q
py -3.11 -m py_compile worker\__init__.py worker\bootstrap.py worker\protocol.py worker\package_check.py
py -3.11 worker\bootstrap.py --check
```

测试使用 `tmp_path` 构造 fake bundle，覆盖：

- 路径解析为绝对路径且不依赖 `PATH`；
- 相对 root、`..`、绝对成员路径的拒绝；
- `worker-runtime.json` 与 distribution `bundle-manifest.json` 分开读取；
- `worker-runtime.json` 缺失时为 `absent`，只报告 bundle manifest components，
  `dependencies={}`，状态为 `degraded` 而不是伪造依赖；
- 缺失 distribution manifest、assets manifest、OCR runtime、vendor、model 时为
  `blocked`/`missing`，且不创建目录；
- `bundle-manifest.json` 生成后不会被 worker 改写（字节级比较）；
- distribution schema 不是 `neko-kards-bundle-manifest/1`、files 路径越界、
  components 不一致或 integrity 指纹不符时为 blocker；
- distribution optional `kards_det` 可在没有 worker runtime 时提供模型声明；
- 模型未声明或哈希不匹配时绝不 `ready`；
- JSON 行超限后可恢复读取下一行；
- 未知协议版本/未知 op 返回结构化错误；
- 超时返回 `timeout` 且回显 request/session/frame id；
- 内联 base64/超长 payload 被拒绝；
- `bootstrap.py --check` 与 `--serve` 子进程冒烟；
- worker 源码不包含 SDK/网络/建目录路径。

当前源码树运行 `--check` 的预期结果是退出码 `2`、`status=blocked`，
`blocked_resources` 包含 `distribution_manifest`、`assets_manifest`、
`ocr_runtime`、`vendor`、`third_party_notices`、`vendored_python`、
`vendored_site_packages`；`degraded_resources` 包含 `worker_runtime`（可选文件
缺失）、`third_party_notices`（仍是 `BLOCKED_PENDING_BUILD` 模板）和
`model:kards_det`；`dependencies` 为 `{}`，`model.kards_det.state` 为 `missing`。

## 8. 明确不做 / 留到后续

- 不下载 Python、wheel、模型或任何网络资源；
- 不安装依赖、不创建 `vendor/` 或 `models/`；
- 不修改 `__init__.py`、`ui/`、`scripts/sync_installations.py` 或安装副本；
- 不启动宿主；
- 不在 worker 中导入 `plugin.sdk`，不实现宿主 push；
- 不接收 base64 全帧；`frame_id` 只是标识；
- 不读取 keys、聊天、原始视频或训练数据；
- 推理、采集、OCR、模型加载、main 侧启动器/host 胶水属于后续阶段。
