# KARDS 开发待办（历史方案）

> 本文件记录 2026-10-18 的 OCR/整局录制/独立 Worker 旧方案，不再作为当前执行清单。纯 YOLO 第一版已移除 OCR 和整局录制入口，不能继续按下文重新安装或恢复它们。当前版本、修复结果与未验收能力以 [第一版收口说明](v1-closure.md) 为准。本文件保留用于回溯历史，不表示这些旧功能应继续实现。

更新日期：2026-10-18

插件源码根目录：

```text
C:\Users\MIKU\.zcode\workspace\default\n.e.k.o_plugin_kards_companion
```

训练工程根目录（在插件仓库外）：

```text
C:\Users\MIKU\.zcode\workspace\default\training
```

本文链接相对 `docs/TODO.md`。已有文件使用可点击链接；尚不存在的文件标为「待新增」。勾选表示对应代码或工具已经存在，不代表完整功能已经过真实对局验收。

## 当前状态

- 进程内采集、OCR、轨迹、事件和录制代码已接线。
- 独立 worker 仍是骨架，只支持 `ping`、`selfcheck`、`shutdown`；`frame.process` 未实现，主插件尚未通过 worker 执行感知。
- Python 嵌入式运行时、第三方运行库、OCR 模型和卡图资源尚未装配。
- `models/kards_det.onnx` 缺失。推理运行库和 KARDS 检测模型是两项独立资源。
- 卡图公开再分发条件未确认；完整离线发行包尚未生成。
- 训练环境和 reviewed 数据集尚未建立，现有预标需要复核。

## 已有实现

- [x] 分层采集调度、预设和参数校验。
  - 文件：[perception/scheduling.py](../perception/scheduling.py)
  - 测试：[tests/test_scheduling.py](../tests/test_scheduling.py)
- [x] 窗口采集、健康检查和后端降级。
  - 文件：[perception/capture.py](../perception/capture.py)、[runtime/health.py](../runtime/health.py)
  - 测试：[tests/test_capture_health.py](../tests/test_capture_health.py)
- [x] 比例 ROI、锚点校准和 HUD 读取接口。
  - 文件：[perception/regions.py](../perception/regions.py)、[perception/hud_reader.py](../perception/hud_reader.py)
  - 测试：[tests/test_regions.py](../tests/test_regions.py)
- [x] 单位位置轨迹、多帧确认及结构化事件证据。
  - 文件：[perception/detector.py](../perception/detector.py)、[state/model.py](../state/model.py)、[state/tracker.py](../state/tracker.py)、[runtime/match_adapter.py](../runtime/match_adapter.py)
  - 测试：[tests/test_perception_units.py](../tests/test_perception_units.py)、[tests/test_tracker.py](../tests/test_tracker.py)
- [x] 局内临时 JPEG 录制、容量滚动和清理。
  - 文件：[runtime/recording.py](../runtime/recording.py)
  - 测试：[tests/test_recording.py](../tests/test_recording.py)
  - 说明：[docs/recording.md](./recording.md)
- [x] 面板采样预设、高级帧率和录制设置。
  - 文件：[ui/panel.tsx](../ui/panel.tsx)、[__init__.py](../__init__.py)、[plugin.toml](../plugin.toml)
- [x] Worker 协议、启动骨架和包内环境自检。
  - 文件：[worker/bootstrap.py](../worker/bootstrap.py)、[worker/protocol.py](../worker/protocol.py)、[worker/package_check.py](../worker/package_check.py)
  - 测试：[tests/test_bundle_runtime.py](../tests/test_bundle_runtime.py)
- [x] 离线卡图索引、资源校验和待构建清单接口。
  - 文件：[knowledge/assets.py](../knowledge/assets.py)、[knowledge/assets_manifest.schema.json](../knowledge/assets_manifest.schema.json)
  - 测试：[tests/test_card_assets.py](../tests/test_card_assets.py)
- [x] 分发 manifest、同步工具及两者的布局校验。
  - 文件：[scripts/bundle_manifest.py](../scripts/bundle_manifest.py)、[scripts/sync_installations.py](../scripts/sync_installations.py)、[bundle-manifest.schema.json](../bundle-manifest.schema.json)
  - 测试：[tests/test_bundle_manifest.py](../tests/test_bundle_manifest.py)
- [x] README 重写。
  - 文件：[README.md](../README.md)

## P0：装配 Python、OCR 和推理运行库

### 1. 包内 Python

- [ ] 下载并装配官方 Windows x64 CPython 3.11 嵌入式运行时。
- [ ] 固定解释器版本和来源校验值，配置包内模块搜索路径。
- [ ] 验证无需系统 Python、用户 site-packages 或 PATH 中的解释器即可运行。

对应位置：

- 自检与路径解析：[worker/package_check.py](../worker/package_check.py)
- Worker 入口：[worker/bootstrap.py](../worker/bootstrap.py)
- 运行时产物：`vendor/python/`、`vendor/python/python.exe`（待构建）
- 运行时元数据：`worker-runtime.json`（待生成）
- 元数据格式：[worker-runtime.schema.json](../worker-runtime.schema.json)

完成条件：包内解释器在隔离环境下正常启动，报告的解释器路径属于插件目录。

### 2. 固定依赖与原生 DLL

- [ ] 为 CPython 3.11 / win_amd64 构建依赖树。
- [ ] 纳入 numpy、Pillow、OpenCV headless、RapidOCR、CPU ONNX Runtime 及实际需要的传递依赖。
- [ ] 保留轮子中的原生 DLL、字典、配置和其他运行必需资源。
- [ ] 记录版本、来源及 SHA-256；构建时只保留一个 cv2 提供者。
- [ ] 验证每个依赖的加载路径均属于包内，不借用宿主已经加载的模块。

对应位置：

- 依赖声明：[requirements-bundle.txt](../requirements-bundle.txt)（当前是占位文件，不可直接作为安装锁使用）
- 依赖产物：`vendor/python/Lib/site-packages/`（待构建）
- 自检：[worker/package_check.py](../worker/package_check.py)
- 构建脚本：`scripts/build_bundle.py`（建议新增，当前不存在）

完成条件：版本和哈希可追溯，导入测试通过；运行时不执行 pip，不在线补装。

### 3. OCR 模型、配置和字典

- [ ] 打包文字检测、文字识别、方向分类模型。
- [ ] 打包所选 OCR 版本对应的字典和配置。
- [ ] 初始化 OCR 时显式指定包内路径。
- [ ] 完成一次真实中文和数字识别，不只检查 import 成功。

对应位置：

- OCR 模型与配置：`ocr_models/`、`ocr_models/runtime.json`（待构建）
- 现有 OCR 调用：[perception/hud_reader.py](../perception/hud_reader.py)、[perception/screens.py](../perception/screens.py)
- 模型和依赖检查：[worker/package_check.py](../worker/package_check.py)

完成条件：断网且用户缓存为空时可加载、可识别；不调用宿主模型下载接口。

## P0：把感知链接到独立 Worker

### 4. Worker 实际执行感知

- [ ] 实现感知会话启动、配置更新、状态读取和停止协议。
- [ ] 将采集、OCR、ONNX 推理及依赖原生库的轨迹处理迁入 worker。
- [ ] 复用现有分层调度、ROI、可信帧检查和录制逻辑。
- [ ] 返回有界结构化观测，不跨管道传输高频全尺寸 base64 图像。
- [ ] 校验协议版本、session、frame_id、观测时间和观测有效性。

对应位置：

- 协议：[worker/protocol.py](../worker/protocol.py)
- 执行入口：[worker/bootstrap.py](../worker/bootstrap.py)（当前 `frame.process` 返回未实现）
- 现有感知模块：[perception/](../perception/)
- 现有录制器：[runtime/recording.py](../runtime/recording.py)
- 主入口：[__init__.py](../__init__.py)
- 宿主端进程客户端：`adapters/perception_worker.py`（建议新增，当前不存在）

完成条件：主插件入口不因缺少 numpy、Pillow、OCR 或 ONNX Runtime 而在启动 worker 前导入失败；SDK、角色路由和消息投递继续留在宿主侧。

### 5. 进程生命周期

- [ ] 用包内解释器绝对路径启动 worker，不使用 shell 或 PATH 查找。
- [ ] 处理启动失败、超时、协议损坏、进程崩溃和受控重启。
- [ ] 停止、重载、宿主退出后回收 worker；使用 Job Object 或等价机制防止孤儿进程。
- [ ] 断开连接后失效旧观测，不补播旧事件；录制仍遵守局内与清理规则。

对应位置：

- 主生命周期：[__init__.py](../__init__.py)
- Worker 入口与协议：[worker/bootstrap.py](../worker/bootstrap.py)、[worker/protocol.py](../worker/protocol.py)
- 宿主端进程客户端：`adapters/perception_worker.py`（建议新增）
- 现有生命周期测试：[tests/test_lifecycle_contract.py](../tests/test_lifecycle_contract.py)

### 6. 面板组件状态

- [ ] 在诊断页显示包内 Python、OCR、CPU 推理库、资源完整性和 worker 状态。
- [ ] 分开显示“推理引擎可用”和“KARDS 检测模型缺失”。
- [ ] 缺文件时显示实际原因，不将骨架自检通过显示为完整功能就绪。

对应位置：[ui/panel.tsx](../ui/panel.tsx)、[__init__.py](../__init__.py)、[worker/package_check.py](../worker/package_check.py)。

## P1：卡图资源

### 7. 来源与分发条件

- [ ] 核实 KARDS 卡图的公开再分发条件，保存适用条款或授权记录。
- [ ] 授权未确认时保持 `blocked_unlicensed`，不把卡图放入公开发行包。

对应位置：

- 来源元数据：[knowledge/cards_builtin.json](../knowledge/cards_builtin.json)
- 资源校验：[knowledge/assets.py](../knowledge/assets.py)
- 资源格式：[knowledge/assets_manifest.schema.json](../knowledge/assets_manifest.schema.json)
- 说明：[docs/card-assets.md](./card-assets.md)

### 8. 获取、校验与加载

- [ ] 先验证代表样本的下载和解码，再构建完整资源集。
- [ ] 以唯一 `card_id` 索引 1083 条记录，不使用有重复的 `import_id` 作为文件主键。
- [ ] 验证格式、尺寸、大小、SHA-256 和覆盖率；拒绝错误页、缺图和损坏图片。
- [ ] 建立本地卡图索引和匹配器；缺图不回退到运行时在线下载。

对应位置：

- 索引与读取：[knowledge/assets.py](../knowledge/assets.py)
- 匹配器：[perception/card_matcher.py](../perception/card_matcher.py)
- 卡图产物：`assets/cards/images/`、`assets/cards/thumbnails/`、`assets/cards/manifest.json`（待构建）
- 资源构建脚本：`scripts/build_card_assets.py`（建议新增，当前不存在）

完成条件：已纳入发行清单的卡图可完全离线读取。卡图齐全不等于遮挡手牌、动画卡面和所有卡名均能稳定识别。

## P1：清单、许可证与完整包验收

### 9. 许可证

- [ ] 收集实际打包组件及模型的许可证、版权行和来源。
- [ ] 将第三方声明模板替换为构建产物对应的真实清单。
- [ ] 核对解释器、原生 DLL、OCR 模型和卡图的分发条件。

对应位置：[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)、[LICENSE](../LICENSE)、`licenses/`（待新增）。

### 10. 生成完整包

- [ ] 生成真实 `bundle-manifest.json`，逐文件记录相对路径、大小、哈希、版本和许可证。
- [ ] 校验所有必需资源，缺失时拒绝构建或发布。
- [ ] 将 KARDS 检测模型标为可选；缺失状态必须出现在能力清单中。
- [ ] 排除训练集、原始录屏、临时帧、用户配置、凭据和开发缓存。
- [ ] 输出包体、解压、安装和备份的空间需求。

对应位置：

- 构建与校验：[scripts/bundle_manifest.py](../scripts/bundle_manifest.py)
- 分发格式：[bundle-manifest.schema.json](../bundle-manifest.schema.json)
- 同步与备份：[scripts/sync_installations.py](../scripts/sync_installations.py)
- 产物：`bundle-manifest.json`、`worker-runtime.json`（待生成）
- 流程：[docs/deployment.md](./deployment.md)、[docs/offline-bundle.md](./offline-bundle.md)

### 11. 测试与真实验收

- [ ] 运行 Python 3.11 全量测试及 TSX 编译检查。
- [ ] 在无系统 Python、无用户 site-packages、无宿主依赖可借用的环境中自检。
- [ ] 断网执行 OCR 和 CPU ONNX 推理，确认所有依赖加载自包内。
- [ ] 验证中文路径、含空格路径、移动目录和标准用户权限。
- [ ] 验证 worker 崩溃/重启/停止、录制清理和资源缺失路径。
- [ ] 在真实 N.E.K.O 中验证 dev 加载、面板和监控启停。
- [ ] 有真实 KARDS 画面后，验证实际处理帧率、ROI、轨迹、事件确认和临时录像清理。

对应位置：

- 测试：[tests/](../tests/)
- 包体测试：[tests/test_bundle_runtime.py](../tests/test_bundle_runtime.py)、[tests/test_bundle_manifest.py](../tests/test_bundle_manifest.py)、[tests/test_card_assets.py](../tests/test_card_assets.py)
- 宿主检查：[scripts/host_acceptance.py](../scripts/host_acceptance.py)、[docs/host-acceptance.md](./host-acceptance.md)
- 行为对照：[docs/backend-parity.md](./backend-parity.md)

## P2：安装与正式版晋级

- [ ] 先检查差异，再同步两个 dev 副本；保留 dev 身份、配置和启用覆盖。
- [ ] 为当前产物生成新的宿主验收报告，不复用旧包指纹。
- [ ] dev 验收通过后同步两个正式副本，保留备份和逐文件哈希。
- [ ] 正式版文件同步与启用分别记录；不自动切换 dev/formal 启用选择。
- [ ] 更新 README 和验收文档中的实际包体大小及限制。
- [ ] 公开资源分发条件通过后，再单独执行 GitHub Release 或插件市场发布。

对应文件：[scripts/sync_installations.py](../scripts/sync_installations.py)、[docs/deployment.md](./deployment.md)、[README.md](../README.md)。

安装位置：

```text
# 宿主当前布局的安装根
%LOCALAPPDATA%\N.E.K.O\.neko-plugin-installations\plugins\kards_companion_dev\
%LOCALAPPDATA%\N.E.K.O\.neko-plugin-installations\plugins\kards_companion\

# 本机仍保留的旧布局副本
%LOCALAPPDATA%\N.E.K.O\plugins\kards_companion_dev\
%LOCALAPPDATA%\N.E.K.O\plugins\kards_companion\
```

复制前核实实际加载路径和目标身份，不删除目标侧未知文件，不覆盖 `config/`、运行记录或用户模型。

## 独立任务：YOLO 训练

训练工程在插件仓库外。它不阻塞 Python/OCR/CPU 推理库的装配，但没有合格模型时不能启用单位检测能力。

- [ ] 建立训练环境并固定依赖。
  - 位置：`training/.venv/`（待创建）、[training/README.md](../../training/README.md)。
- [ ] 建立 reviewed 工作副本，原始素材不变。
  - 输入：[training/dataset/raw/](../../training/dataset/raw/)
  - 工具：[prepare_reviewed_dataset.py](../../training/tools/prepare_reviewed_dataset.py)、[dataset_common.py](../../training/tools/dataset_common.py)
  - 输出：`training/dataset/reviewed/`（待创建）。
- [ ] 复核自动预标，补齐 HQ、手牌和打出牌；空标签不得直接视为已确认背景。
  - 现有预标工具：[training/prelabel.py](../../training/prelabel.py)
  - 核查工具：[validate_yolo_dataset.py](../../training/tools/validate_yolo_dataset.py)
  - 操作仅作用于 reviewed 标签副本。
- [ ] 按录屏/对局分组切分，并处理同一局跨录屏的情况。
  - 工具：[prepare_reviewed_dataset.py](../../training/tools/prepare_reviewed_dataset.py)
  - 说明：[reviewed-dataset-pipeline.md](../../training/docs/reviewed-dataset-pipeline.md)
  - 验收：同局组在 train/val 间无交集；关键类别在验证集中有有效样本。
- [ ] 从原始录屏定向补充少见类别，不使用全部相邻帧随机切分。
  - 工具：[training/extract_frames.py](../../training/extract_frames.py)
- [ ] 接入保守 UI 增强并执行 5–10 epoch 试训。
  - 入口：[training/train.py](../../training/train.py)
  - 配置：[train_augment_conservative.yaml](../../training/configs/train_augment_conservative.yaml)
  - 输出：[training/runs/](../../training/runs/)。
- [ ] 正式训练，逐类检查 Precision、Recall、mAP 和预测图。
  - 当前快照存在 `hq=0` 和小类别覆盖不足；补齐和复核前不视为正式训练就绪。
- [ ] 达标后导出 ONNX，检查类别顺序、输入输出、CPU 推理和 dev 加载。
  - 导出入口：[training/export_onnx.py](../../training/export_onnx.py)
  - 插件加载：[perception/detector.py](../perception/detector.py)
  - 最终模型：`models/kards_det.onnx`（当前缺失）。
- [ ] dev 实机验证通过后晋级正式版，保留旧模型备份。

## 验收命令

在插件根目录运行：

```powershell
# 全量测试
py -3.11 -m pytest tests -q

# 离线包相关测试
py -3.11 -m pytest tests/test_bundle_runtime.py tests/test_bundle_manifest.py tests/test_card_assets.py -q

# 运行环境自检；资源尚未装配时返回 blocked 属当前预期
py -3.11 worker/bootstrap.py --check

# 安装差异预检，不写目标目录
py -3.11 scripts/sync_installations.py --check --channel dev
```

测试通过数以当前命令输出为准。测试通过、SDK 接受提交、LLM 生成和 TTS 播放分别记录，不互相替代。
