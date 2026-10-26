# 不可检测事件备份（已从 event_catalog.py 移出）

用户给的告警清单共 **77 条**。逐条对照 Open Rails REST API 的实际可用数据后，
**41 条在游戏里没有任何数据源**，已按用户指示从 `core/event_catalog.py` 移出、
备份于此。

保留这份文档的原因：这些是**真实需求**（来自真实机车作业与规范），不是错误。
一旦出现新的数据源（OCR / 内存读取 / OR 后续版本新增端点），它们应当被重新评估。

> **铁律**：detector 只为 `event_catalog.py` 里的事件产生候选。
> 绝不为了"看起来完整"去编造状态——假报会训练用户忽略告警，比不报更危险。

核对依据的数据源（详见 `core/snapshot.py`）：
`tm` = `/API/TRACKMONITORDISPLAY`（F4 Track Monitor）·
`cab` = `/API/CABCONTROLS`（机控器）·
`hud` = `/API/HUD/{0,1}`（F5 / Shift+F5）

---

## 一、凭证、车机联控类（9 条，整类无数据源）

Open Rails **完全没有**凭证、车机联控、调度命令的概念。这一整类都需要人工或外部音频。

| event_id | 级别 | 不可检测原因 |
|---|---|---|
| `PERMIT_SINGLE_CONFIRM` | P0 | 无"凭证/双人确认"概念 |
| `PERMIT_NO_READBACK` | P0 | 无凭证复诵状态 |
| `PERMIT_DOUBT` | P0 | "凭证有疑点"是人的认知判断 |
| `RADIO_STANDARD` | P2 | 无车机联控通话记录 |
| `RADIO_ORDER_LIMIT` | P1 | 无调度命令内容 |
| `PERMIT_GREEN` | P1 | 无绿色许可证 |
| `PERMIT_RED` | P1 | 无红色许可证 |
| `PERMIT_ROAD_TICKET` | P1 | 无路票/电话记录号 |
| `PERMIT_GUIDE_SIGNAL` | P1 | 无引导信号/手信号 |

**若要覆盖**：只能靠语音识别（监听车机联控音频）或用户手动触发。不属于本次插件范围。

---

## 二、瞭望与精力状态类（6 条，整类无数据源）

| event_id | 级别 | 不可检测原因 |
|---|---|---|
| `ALERTER_WARN` | P0 | OR 的 Alerter 是**客户端选项**，未进 REST API |
| `ALERTER_NO_ACTION` | P0 | 同上；且"长时间无操作"无输入事件流可测 |
| `LOOKOUT_FOG_RAIN` | P1 | OR 未暴露天气；无雨/雪/雾数据 |
| `LOOKOUT_CROSSING` | P1 | 未暴露道口/桥隧/防洪点/看守点/施工点位置 |
| `LOOKOUT_DISTRACT` | P0 | 需判断司机是否走神/闲聊/看手机 |
| `LOOKOUT_ASSUME` | P0 | "臆测行车"是人的认知状态 |

**若要覆盖**：
- `ALERTER_*` → 有可能通过 OCR 读 HUD 文本（见下文 OCR 一节）
- `LOOKOUT_FOG_RAIN` → 需要 OR 暴露天气；或 OCR 读画面天气
- `LOOKOUT_DISTRACT` / `LOOKOUT_ASSUME` → 需外部摄像头/语音分析，超出范围

---

## 三、调车、道岔、连挂类（10 条）

| event_id | 级别 | 不可检测原因 |
|---|---|---|
| `SWITCH_UNLOCKED` | P1 | `TrackCol` 只给开通方向（►/◄），**OR 无道岔锁定标志** |
| `SWITCH_WRONG_DIR` | P0 | OR 无"径路意图"，无法判断方向是否正确 |
| `SHUNT_SIGNAL_BLUE` | P1 | OR 无调车信号灯色（只有干线 aspect） |
| `SHUNT_SIGNAL_WHITE` | P2 | 同上 |
| `SHUNT_DIST_10` | P2 | OR 无"距连挂目标多少辆"概念 |
| `SHUNT_DIST_5` | P2 | 同上 |
| `SHUNT_DIST_3` | P1 | 同上 |
| `SHUNT_COUPLING_OVERSPEED` | P0 | 需连挂语境 |
| `SHUNT_UNCOUPLED` | P2 | 需试拉/车钩软管状态 |
| `SHUNT_DERAILER` | P0 | **OR 无脱轨器概念**（用户已知） |

**若要覆盖**：
- `SWITCH_UNLOCKED` / `SWITCH_WRONG_DIR` → 需 OR 暴露道岔状态；或 OCR 读 F8 道岔监视窗口
- `SHUNT_SIGNAL_*` → 需 OR 暴露调车信号
- `SHUNT_DIST_*` → 连挂距离需知道目标车位置，OR 不提供

---

## 四、仪表与设备类（5 条）

| event_id | 级别 | 不可检测原因 | 备注 |
|---|---|---|---|
| `ELEC_CONTROL_VOLTAGE` | P2 | 未在任何机车 CVF 里见过控制电压控制项 | 换车可能有效，值得重测 |
| `MECH_SMELL` | P0 | 需人的嗅觉（机械间异味/焦糊味） | 需外部传感器 |
| `MECH_NOISE_VIB` | P1 | 需人的听觉/体感（走行部异音/振动） | 需外部传感器 |
| `WHEEL_SLIP` | P1 | 无空转/滑行控制项；`ACCELEROMETER` 不足以可靠判定 | 换车可能有效 |
| `GAUGE_TAIL_PRESSURE`（部分） | P1 | 已收录，但**仅部分机车**在 HUD 给出 `EOT` 列尾压力 | 见主表；按 `available` 判定 |

---

## 五、应急类（3 条）

| event_id | 级别 | 不可检测原因 |
|---|---|---|
| `EMERG_PHASE_BREAK` | P1 | OR 未暴露分相区 |
| `EMERG_FIRE` | P0 | 无火灾/冒烟传感器 |
| `EMERG_RESCUE` | P1 | 救援回送（调度命令/连挂方式）不在数据里 |

---

## 六、防溜与终到入库类（5 条）

| event_id | 级别 | 不可检测原因 |
|---|---|---|
| `PARK_DERAILER_NOT_SET` | P1 | OR 无防溜器具 |
| `PARK_DERAILER_NOT_REMOVED` | P0 | 同上 |
| `PARK_INBOUND_SIGNAL` | P1 | 需调车信号，OR 无 |
| `PARK_FORBIDDEN_ZONE` | P1 | OR 无禁停区标 |
| `PARK_HANDOVER` | P2 | LKJ / 视频 / 手帐均不在数据里 |
| `PARK_BRAKE_NOT_SET`（部分） | P0 | 已收录，但**只能覆盖空气制动**；手制动/铁鞋不在数据里 |

---

## 七、用户清单里自认不可检测的项（原文）

用户自己标注了两条：

- **LKJ / IC卡 / 编组参数输入未复核** → 建议人工触发或活动脚本
- **违章解除监控 / 屏蔽报警 / 盲目解锁** → 建议人工触发

这两条与上表结论一致，无补充。

---

## 八、若要覆盖这些，唯一现实出路：OCR / 屏幕捕获

用户最初提到"这些窗口的数据是外部程序可读的（通过屏幕捕获或内存读取）"。
经核查：**F4/F5/Shift+F5 的内容 API 已经结构化给出了**，不需要 OCR。
真正 API 覆盖不到的是：F8 道岔监视、Ctrl+9 调度窗口、F10 活动监视、Alerter 提示。

同域参照 `neko_pawpilot` 已经实现过这条路：
`C:\Users\25672\AppData\Local\N.E.K.O\plugins\neko_pawpilot\adapters\hud_ocr.py`
（配 `data\config\ocr_regions.json` 定义区域，`adapters\vendor\pydirectinput` 用于按键）。

评估与建议：
- **成本**：OCR 需要区域标定、分辨率/缩放适配、字体渲染差异处理，且对画面变化脆弱
- **收益**：能补齐 `ALERTER_*`、`SWITCH_UNLOCKED`、`PARK_*` 这几类
- **建议**：先把 API 覆盖的 35 条做扎实（本次范围），OCR 作为**独立第二期**，
  且必须与 API 路径解耦——OCR 失效时不能影响主告警链路

⚠ 一旦决定做 OCR，请为每条 OCR 事件明确标注"来源=OCR"及置信度，
避免与 API 来源的权威判定混在一起（API 给的是游戏真实状态，OCR 是像素推断）。
