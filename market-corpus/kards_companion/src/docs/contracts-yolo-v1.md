# YOLO 改造公共契约 v1（已冻结 · 主智能体维护）

本文件是 A/B/C/D/E/F 各子任务的唯一契约真源。改动必须由主智能体执行。

## 1. 文件与所有权

| 文件 | 所有者 | 说明 |
|---|---|---|
| knowledge/ui_classes.json | 主 | 界面模型 10 类 v1（已冻结，见文件） |
| knowledge/card_classes.json | A 生成 → 主审定 | 1083 类，card_id 字典序，append-only |
| knowledge/models_manifest.json | 主 | 模型清单（sha256/输入输出/能力） |
| models/ui_det.onnx, models/card_cls.onnx | B/C 产出 → 主放置 | 不进 git，部署时随包 |
| __init__.py, perception/, state/, runtime/, scripts/bundle_manifest.py, worker/package_check.py, plugin.toml, requirements-bundle.txt, tests/ | D | 运行时改造 |
| ui/panel.tsx | E | 面板与文案（文案主审定稿） |
| scripts/sync_installations.py | 主/F | 部署同步（需扩展 models/ 与新映射文件同步） |

## 2. 模型契约

### 2.1 ui_det.onnx（检测）
- 输入 `1x3x640x640 float32`，letterbox(640, pad=114) RGB / 255（与旧 kards_det.onnx 预处理一致）。
- 输出 ultralytics 原始检测张量 `(1, 4+10, N)`；插件端 conf>=0.30、按类 NMS iou=0.45。
- 加载校验：输出通道=14 且 class_map.task=="detect"，否则拒绝加载。

### 2.2 card_cls.onnx（分类）
- 输入 `1x3x224x224 float32`，RGB / 255；ImageNet mean/std 归一化与 softmax 已内嵌进导出图（导出时包 wrapper）。
- **预处理（与 ultralytics 分类验证管线一致）**：短边等比缩放到 224（双线性），再中心裁剪 224x224；RGB / 255 送入。
- 输出 `(1, 1083)` 概率。插件端 top1_conf 与 top1-top2 margin 双阈值，低于阈值 → card_id=unknown。
- **类别完整性**：训练只含 asset_status=ok 的类；导出 wrapper 将训练类概率投影回全 1083 维（asset_status=missing 的类恒 0 概率，永远不可能成为 top1 → 恒 unknown）。
- 裁图来源：仅对 ui_det 的 `card_hand` 与 `played_card` 框裁图分类；`unit_board` 裁图默认输出 unknown（除非白名单声明该能力）。
- 加载校验：输出类别数==card_classes 数组长度 且 task=="classify"，否则拒绝加载。

## 3. 运行时数据流（D 实现）

```
ScreenCapture 生产线程（单一生产者，有界环形，消费最新帧）
  └─ perception loop：
     change 任务（约10fps）：dHash 帧差门控（不跑模型），变化才推进场景/检测
     unit 任务（约4-8fps）：ui_det 检测 →
        · 场景推断 scene：screen_victory/screen_defeat 命中→victory/defeat（多帧确认）；
          frontline 或 end_turn_button 稳定出现→match；否则 unknown（unknown 永不确认离场，沿用 scenario 现有 3 帧逻辑）
        · my_turn：第一版保持 None；按钮位置不证明行动方，未验证的 active 类被能力门拦截
        · UnitTracker.update_observations（沿用 2 帧确认/3 帧缺失语义）
     card 任务（约2-6fps）：对最近一次 unit 检测的 card_hand/played_card 框裁图 → card_cls →
        仅生成卡面观测（observation_id/frame_id/card_id/name/region/conf）；不写 last_order_card、不产出出牌事件、不自动命名棋盘单位
        同一帧重复消费不增加确认次数（沿用 scheduler 帧号高水位）
```

- HUD 数字字段 `my_hq/enemy_hq/my_credits/my_credit_cap` 全部置 None（第一版不读动态数字）；tracker 的 HQ 事件线自然休眠，不得用 0 填充。
- OCR 全链路移除：hud_reader.py、screens.py 的 OCR 实现、regions.py、RapidOCR 依赖、bundle ocr marker。
- 录像全链路移除：runtime/recording.py、recording_* 设置、面板入口、JPEG 帧存档。
- perception/scheduling.py：TASKS 改为 ("change","unit","card")；preset 四元组 = capture/change/unit/card：
  low_cost 15/8/2/3、balanced 20/10/4/6、quality 30/12/6/10。
- 状态面（E 的数据源）：schema 升级为 kards_status_v2，新增
  `models`（ui_det/card_cls 的 loaded/file/sha256/nc/状态、class_map_versions、trusted_card_count、白名单能力摘要），
  `recognized_cards`（卡面观测 [{observation_id, frame_id, card_id, name, region, conf}]，不是场上实例身份），
  `fps_measured`（capture/change/unit/card 实测频率，与目标值并列展示），
  移除 `recording`、`ocr_available`、`ocr_telemetry`、`regions`。
- 事件链、仲裁、投递、gate、安全门、scenario、timeline、target 路由全部保留复用。
- Python 3.11 兼容；仅标准库+numpy+Pillow+onnxruntime(可选)。

## 4. 卡牌类别映射生成规则（A 执行）

- card_classes.json：`{"schema":"kards.card-classes","version":1,"task":"classify","model_file":"card_cls.onnx","classes":[{"id":i,"card_id":slug,"name":中文名,"import_id":...}],"ordering_rule":"card_id 字典序，append-only"}`。
- 类别 id = 全部卡按 card_id UTF-8 字典序排序后的下标，一次生成后永久冻结。
- 同图不同 card_id（艺术复用）必须在 manifest 中标记 `conflict_group`，此类卡运行时永远输出 unknown，除非真实画面验证能区分。

## 5. 验收口径（F 执行）

- 卡牌逐类 P/R 需要录屏裁图的独立可靠真值。感知嵌入/另一模型只能交叉核对；模型间一致不等于真值。没有足够独立实例时不启用该类别；当前可信白名单为空。
- 界面模型逐类 P/R + mAP 用分组切分的 val/test 集。
- 白名单粒度 card_id × appearance（hand_full / zoom_full / board_crop）。
- ONNX 与源框架一致性：同一批输入 max|Δprob| < 1e-4（分类）、框坐标差 < 0.5px（检测）。
