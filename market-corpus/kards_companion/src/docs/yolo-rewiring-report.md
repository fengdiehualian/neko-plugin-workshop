# YOLO 运行时改造交付报告（子智能体 D）

> 历史实现快照，以下出牌认领、回合推断和模型缺失等描述已不代表当前版本。6.0.1 已删除放大牌面到出牌/棋盘身份的自动认领，未知回合保持 null，并启用模型能力硬门。当前证据与限制以 [第一版说明](v1-closure.md) 和 `contracts-yolo-v1.md` 为准；历史测试数量不作为当前验收结果。

对应契约：`docs/contracts-yolo-v1.md`（已冻结）。改造范围：双 YOLO 模型接入、
OCR 全链路移除、录像功能移除、感知主循环重接与事件接线。测试全部使用合成
张量/假 ONNX 会话，不需要真实模型权重。

## 1. 改动清单（文件 → 要点）

| 文件 | 要点 |
|---|---|
| `perception/models.py`（新增） | 双模型加载层：`UiDetector`（models/ui_det.onnx，类别表来自 ui_classes.json 10 类，校验输出通道==4+num_classes、task=="detect"、manifest sha256）与 `CardClassifier`（models/card_cls.onnx，校验输出类别数==1083、task=="classify"；预处理短边 224 双线性+中心裁剪+RGB/255；top1/margin 双阈值→unknown，阈值读 manifest `unknown_policy` 0.55/0.15；conflict 与 asset_status!=ok 类在取 top 前概率置 0）。文件缺失→显式 `unavailable` 状态不抛异常；推理异常→`error` 状态并保留诊断。另含 `postprocess_detect`（类别表注入的 ultralytics 张量后处理）、`load_trusted_card_count`（trusted_cards.json 缺失则 0）。 |
| `perception/detector.py` | 删除 `CLASS_NAMES`/`CLASS_TO_ID` 七类常量与 `YoloDetector`（旧 kards_det.onnx 加载点）。保留 letterbox/NMS/Detection/UnitTracker/`build_unit_observations`（2 帧登场/2 帧移动/3 帧缺失、侧别只信 frontline 框的语义未动）。 |
| `perception/screens.py` | 重写为无 OCR `ScreenClassifier`：输入检测框集合，输出 (scene, my_turn)。screen_victory/screen_defeat 优先→victory/defeat；frontline 或 end_turn_button(_active) 连续 2 帧稳定→match；否则 unknown。my_turn：active→True、仅非 active→False、都无→None。 |
| `perception/scheduling.py` | `TASKS=("change","unit","card")`；PRESETS 四元组（采集/变化/单位/卡牌）low_cost 15/8/2/3、balanced 20/10/4/6、quality 30/12/6/10；CONFIG_KEYS={perception_preset,capture/change/unit/card_fps}（card_fps 0.5..12）。新增 `LEGACY_CONFIG_KEYS` + `legacy_config_warnings()`（hud_fps/ocr_fps/roi_*/recording_* 忽略并产出 warning 文案）。scheduler 新增接受时刻滚动窗口与 `measured_fps()`（实测 Hz）。删除 recording/roi 字段。 |
| `perception/capture.py` | 删除 `FrameRing`、`on_frame` 回调链、`_notify_frame`、`callback_errors` 统计与 `ring_enabled`（参数保留但忽略以兼容旧调用）。`latest_capture()` 语义保留。 |
| `__init__.py` | 主循环重接（见 §2）；删除 RapidOCR 惰性导入、HudReader、`_hud_read_gated`、`_region_anchors`、RegionCalibrator、recorder 全家（见 §3）；`STATUS_SCHEMA_VERSION="kards_status_v2"`；状态面新增 `models`/`recognized_cards`/`fps_measured`；`_MODEL_PATH` 指向 ui_det.onnx、新增 `_CARD_CLS_MODEL_PATH`；my_hq/enemy_hq/my_credits/my_credit_cap 恒 None（不填 0，HQ 事件线自然休眠）。 |
| `worker/package_check.py` | 必需标记只剩 python_worker/assets_manifest/third_party_notices（ocr_runtime 删除）；可选模型改为 `kards_ui_det=models/ui_det.onnx` 与 `kards_card_cls=models/card_cls.onnx`（旧 kards_det 声明删除）；`DEFAULT_DEPENDENCIES` 移除 rapidocr；capabilities 改为 bundle_ready/kards_ui_det/kards_card_cls；model 自检按双模型循环。 |
| `scripts/bundle_manifest.py` | REQUIRED 去 ocr_runtime（JSON_REQUIRED 只剩 assets_manifest）；BUNDLE_DIRS 去 ocr_models；`OPTIONAL_MODELS` 双模型；capabilities 改 `bundle_ready`+双模型状态。 |
| `plugin.toml` | 采样预设注释更新为新四元组；新增 `card_fps`；删除 hud_fps/roi_auto_calibrate/recording_enabled/recording_max_mb。 |
| `requirements-bundle.txt` | 移除 OCR 引擎依赖候选与 ocr capability 映射。 |
| `.gitignore` | 新增 `.venv311/`。 |
| 测试 | 新增 `tests/test_models.py`、`tests/test_screens.py`、`tests/test_yolo_removals.py`；重写/更新 test_scheduling/test_detector/test_perception_units/test_capture_health/test_lifecycle_contract/test_api_contract/test_bundle_manifest/test_bundle_runtime；删除 test_recording.py、test_regions.py。 |

### 主循环数据流（__init__.py `_perceive_once`）

```
change 任务：dHash 帧差门控（不跑模型）；hamming>2 置 dirty
unit 任务（dirty 时）：UiDetector.detect → ScreenClassifier → scene/my_turn
                      → _stage_card_crops（card_hand/played_card 框入队）
                      → scenario.observe_scene（3 帧确认/unknown 不确认离场不变）
                      → UnitTracker.update_observations（2/2/3 帧语义不变）
card 任务：pending 帧高水位去重 → 裁图（短边<40px 跳过）→ CardClassifier
           → played_card 且 conf≥0.6 → state.last_order_card（side=mine）
             → tracker `_order_diff` 产出 E_ORDER_CARD
           → 新登场确认单位在 6s 窗内认领 played 身份
             （BoardUnit.card_id/card_name/name_confidence，手牌/棋盘分开）
           → recognized_cards（上限 20 条，region=hand/played_card/board）
```

## 2. 状态面 kards_status_v2

* `models`：`{ui_det:{loaded,file,sha256,nc,task,class_map_version,last_ms,diagnostics,state}, card_cls:{…,unknown_policy}, class_map_versions:{ui_classes,card_classes}, trusted_card_count}`（当前仓库无 trusted_cards.json → 0；`status()` 异常时退化为最小诊断视图）。
* `recognized_cards`：`[{instance_id,card_id,name,region,conf}]`（≤20）。
* `fps_measured`：`{capture,change,unit,card}` 实测 Hz（scheduler 滚动窗口；capture 由帧计数差/墙钟差）。
* 移除：`recording`、`ocr_available`、`ocr_telemetry`、`perception_settings.regions`。
* `perception_settings_set` 白名单：`perception_preset/capture_fps/change_fps/unit_fps/card_fps`，热更新语义不变。

## 3. 移除清单（每处入口的处置）

| 入口 | 处置 |
|---|---|
| `perception/hud_reader.py`（RapidOCR HUD 数字读取） | 文件删除；`parse_hp/parse_credits` 用例随删 |
| `perception/regions.py`（RegionCalibrator ROI 校准） | 文件删除；`_sync_region_calibrator`/`_region_snapshot`/`_region_anchors`/`_apply_detector` 内校准段删除 |
| `perception/screens.py` OCR 实现 | 重写为检测框驱动；OCR 关键词/裁剪区域常量删除 |
| `__init__.py` RapidOCR 惰性导入 + `self._ocr`/`self.hud`/`self.classifier(OCR)` | 删除；`_hud_read_gated`/`_hud_cache`/`_hud_hash` 删除 |
| `ocr_available`/`ocr_telemetry` 状态键、`hud_fps` 设置项与 `ocr_fps` 别名 | 删除；config 读取路径对旧键记 warning 忽略，动作白名单按 unknown 拒绝 |
| `runtime/recording.py`（TemporaryFrameRecorder） | 文件删除 |
| `__init__.py` recorder 全家：`_make_recorder`/`_recording_config`/`_recording_payload`/`_refresh_recording_config`/`_recording_set_active`/`_start_recording_match`/`_end_recording_match`/`_stop_recording`/`_on_capture_frame`/`_recording_frame_allowed`/startup 孤儿清理/pause/resume/stop 内录像段 | 全部删除 |
| `RECORDING_*` 常量、settings `recording_enabled/recording_max_mb`、plugin.toml 两个键、状态面 `recording` 段 | 删除 |
| capture.py `on_frame` 参数/`FrameRing`/ring_enabled 生效路径/`_notify_frame` | 删除（参数兼容保留并忽略） |
| `tests/test_recording.py`、`tests/test_regions.py`、`docs/recording.md` | 删除 |
| bundle：`ocr_runtime` 必需标记、ocr_models 目录、rapidocr 依赖、旧 `kards_det` 可选声明 | 删除；替换为 kards_ui_det/kards_card_cls 可选声明 |

grep 契约测试（`tests/test_yolo_removals.py`）断言运行时源码不再出现
`kards_det.onnx`、`rapidocr/RapidOCR`、`hud_reader/HudReader`、
`TemporaryFrameRecorder/RecordingConfig`、`RegionCalibrator`、`FrameRing`、
`ocr_models/runtime.json`、`ocr_runtime`。

## 4. 新配置面

* `perception_preset`：low_cost 15/8/2/3、balanced 20/10/4/6、quality 30/12/6/10、custom。
* `capture_fps`(1..30) / `change_fps`(0.5..15) / `unit_fps`(0.5..12) / `card_fps`(0.5..12)，任务帧率不得超采集帧率。
* 兼容别名：`detect_fps→change_fps`、`tracker_fps→unit_fps`、`low→low_cost`。
* 忽略并告警：`hud_fps/ocr_fps/roi_auto_calibrate/auto_calibrate/roi_mode/recording_*`。

## 5. 测试结果（Python 3.11.9，`.venv311`）

```
.venv311\Scripts\python.exe -m pytest tests -q
626 passed in 8.93s
```

覆盖点：模型加载校验拒绝（假 session 输出通道 11!=14、任务不符、输出类别数
512!=1083）；阈值→unknown（conf/margin 两路）；conflict 类概率置 0 后由次高
接管、唯一强 conflict 恒 unknown 且 conf/margin 报 0；ScreenClassifier 各场景
与三态 my_turn；scheduling 新任务集/预设/legacy 键；状态面 v2 键；card 任务
接线（E_ORDER_CARD、单位认领、同一帧去重、小裁图跳过、unknown 不入列）；
grep 断言；全部旧合同（生命周期/投递/仲裁/安全门/timeline/bundle）继续通过。

## 6. 已知限制与协调事项

1. **权重未部署**：仓库内 `models/` 为空，`UiDetector/CardClassifier` 处于
   `unavailable`（状态面如实呈现）；`tests` 全部走假会话，不依赖真实 onnx。
2. **跨边界热修**：`scripts/sync_installations.py`（主智能体所有）的
   `bundle_input` 在双 optional 模型上线后存在 `path` 变量遮蔽 manifest 路径
   的 bug（load_manifest 会读到 `models/card_cls.onnx` 报 BundleError）。已做
   一行热修（循环变量改名 `entry_path`，带 NOTE(D) 注释），请主智能体复核。
3. **panel.tsx（E 所有）**：仍按旧状态面渲染 `recording/ocr_*` 键时，v2 状态
   面已不再提供这些键；E 需按 `kards_status_v2`（models/recognized_cards/
   fps_measured）更新。
4. **旧文档残留**：`docs/backend-integration-contract.md`、`docs/deployment.md`、
   `docs/offline-bundle.md`、`docs/host-acceptance.md`、`README.md` 仍描述
   OCR/录像/ocr_models 布局（文档所有权不在 D，未改动）。
5. `my_turn` 三态中 None 在 `MatchState.my_turn`（bool）上落为 False；三态语义
   保留在 `ScreenClassifier.my_turn` 属性中。
6. `worker/bootstrap.py` 文档字符串仍提及旧 OCR 依赖名（该文件不在 D 所有权
   内，功能无影响）。
7. 手牌（card_hand）分类结果只进 `recognized_cards`（region=card_hand），不
   直接生成事件；board 单位身份来自 played_card 身份认领链。
