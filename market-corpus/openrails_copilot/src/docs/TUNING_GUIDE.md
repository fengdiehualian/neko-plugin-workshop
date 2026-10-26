# Open Rails Copilot - 调优指南

本文档说明如何根据实战验证结果调整插件参数，平衡**误报率**与**漏报率**。

---

## 📐 核心参数地图

### 1. 检测阈值（`core/constants.py`）

控制**什么时候触发候选告警**。阈值越严格，候选越少（漏报风险↑）；越宽松，候选越多（误报风险↑）。

```python
# 速度相关
OVERSPEED_THRESHOLD_KMH = 5.0           # 超限速多少才算超速
SPEED_DROP_THRESHOLD_KMH = 15.0         # 速度下降多少算急减速
EMERGENCY_BRAKE_THRESHOLD = 0.7         # 制动缸压力超过多少算紧急制动

# 信号相关
SIGNAL_DISTANCE_WARN_M = 800.0          # 距离红灯多远开始警告
SIGNAL_DISTANCE_CRITICAL_M = 400.0      # 距离红灯多近算危险

# 坡道相关
STEEP_GRADIENT_THRESHOLD_PCT = 15.0     # 多陡算陡坡
BRAKE_ON_SLOPE_THRESHOLD = 0.5          # 坡道上多大制动力算"保压不足"
```

**调整原则**：
- 如果某个事件**频繁触发但多数是正常操作**（误报） → **收紧阈值**
- 如果某个事件**应该触发但没有**（漏报） → **放宽阈值**

---

### 2. 冷却时间（`core/arbiter.py`）

控制**同一事件触发后多久才能再次播报**。避免短时间内重复刷屏。

```python
# EventSpec 中的 cooldown_s 字段
EventSpec(
    event_id="OVERSPEED",
    severity="P1",
    cooldown_s=20.0,  # ← 超速告警后 20 秒内不再重复
    ...
)
```

**调整原则**：
- 如果某个事件**持续触发**（比如长时间超速，每2秒报一次） → **增加冷却时间**
- 如果某个事件**恢复后又复发**但被冷却压制了 → **减少冷却时间**或改用**上升沿检测**

---

### 3. 全局限流（`core/arbiter.py`）

控制**任意告警之间的最小间隔**。防止多个不同事件同时爆发时刷屏。

```python
class Arbiter:
    def __init__(self, ...):
        self._global_cooldown_s = 12.0  # ← 任意两条告警之间至少间隔 12 秒
```

**调整原则**：
- 如果**紧急事件被全局限流压制**（比如红灯冲进后又超速，但超速没播报） → **缩短全局冷却**或给紧急事件加**抢占权**
- 如果**仍然觉得太吵** → **延长全局冷却**

---

### 4. 场景门控（`core/arbiter.py`）

控制**在特定作业场景下，哪些类别的告警整体静默**。

```python
# Arbiter.update_scenario() 自动调用
_CATEGORY_GATE = {
    "station_idle": {"overspeed", "brake_release"},  # 停站时不谈超速和制动缓解
    "shunting": {"overspeed"},                       # 调车时不套干线限速规则
    "mainline": set(),                               # 干线全开
}
```

**调整原则**：
- 如果某个场景下**某类告警持续误报** → **把该类别加入对应场景的门控集合**
- 如果某个场景下**关键告警被错误静默** → **从门控集合中移除该类别**

---

### 5. 断路器（`core/safety_guard.py`）

保护推送通道：连续失败超过阈值后自动停止发送，防止无限重试。

```python
class SafetyGuard:
    def __init__(self):
        self._failure_window_s = 60.0      # 统计窗口：60秒
        self._failure_threshold = 5        # 窗口内失败超过 5 次就跳闸
```

**调整原则**：
- 如果**网络偶尔抖动就跳闸** → **增加阈值**或**延长窗口**
- 如果**推送故障时仍然狂发** → **减少阈值**

---

## 🔧 常见调优场景

### 场景 A：超速告警太频繁

**现象**：在限速边缘反复触发，比如限速 100 km/h，速度在 99-101 之间波动。

**诊断**：
```bash
grep "OVERSPEED" field_test_report.json | jq '.alert_log[] | select(.event_id == "OVERSPEED")'
```

**解决方案**：
1. **提高阈值**：`OVERSPEED_THRESHOLD_KMH` 从 5.0 改为 8.0
2. **增加冷却**：`EventSpec.cooldown_s` 从 20.0 改为 30.0
3. **改用上升沿**：只在"从不超速→超速"时触发，而不是持续超速时重复触发（需修改 `detector.py`）

---

### 场景 B：紧急制动误报（停站保压时触发）

**现象**：列车停站时制动缸保持高位，每次采样都判定为"紧急制动"。

**诊断**：
```bash
grep "BRAKE_EMERGENCY" field_test_report.json
```

**解决方案**：
1. **场景门控**（推荐）：在 `station_idle` 场景下静默 `brake_emergency` 类别
   ```python
   _CATEGORY_GATE = {
       "station_idle": {"overspeed", "brake_release", "brake_emergency"},
   }
   ```
2. **提高阈值**：`EMERGENCY_BRAKE_THRESHOLD` 从 0.7 改为 0.85
3. **增加速度条件**：只在速度 > 10 km/h 时才判定紧急制动（需修改 `detector.py`）

---

### 场景 C：红灯警告漏报

**现象**：距离红灯 300 米时没有告警，直到 100 米才发现。

**诊断**：
- 检查 `snapshot.signal_distance_m` 是否正确采集
- 检查 `SIGNAL_DISTANCE_WARN_M` 是否太小

**解决方案**：
1. **放宽阈值**：`SIGNAL_DISTANCE_WARN_M` 从 800 改为 1200
2. **分级警告**：增加"预警"和"危险"两档（需修改 `event_catalog.py`）

---

### 场景 D：告警刷屏（多个事件同时爆发）

**现象**：进站时同时触发"超速""制动不足""距离信号过近"，短时间内播报 3 条。

**诊断**：
```bash
jq '.spoken_log[] | select(.time > 1234567890 and .time < 1234567900)' field_test_report.json
```

**解决方案**：
1. **延长全局冷却**：`_global_cooldown_s` 从 12.0 改为 18.0
2. **设置抢占优先级**：让高优先级事件（如"冲进信号"）可以打断低优先级事件的冷却（需修改 `arbiter.py`）
3. **合并相关告警**：比如"超速 + 制动不足"合并为一条"进站速度过高"（需修改 `detector.py`）

---

### 场景 E：断路器误跳闸

**现象**：偶尔一次网络抖动，插件就停止播报，直到重启监控。

**诊断**：
```bash
jq '.alert_log[] | select(.block_reason == "circuit_open")' field_test_report.json
```

**解决方案**：
1. **放宽阈值**：`_failure_threshold` 从 5 改为 8
2. **延长窗口**：`_failure_window_s` 从 60.0 改为 120.0
3. **自动恢复**：增加"半开"状态，允许定期重试（需修改 `safety_guard.py`）

---

## 📊 验证工作流

### 第一轮：基线验证

```bash
# 运行 30 分钟，收集基线数据
python plugin/plugins/openrails_copilot/field_test.py --duration 1800

# 查看报告
cat field_test_summary.md
```

### 第二轮：参数调整

根据报告中的"调优建议"和实际体验，编辑：
- `core/constants.py` （阈值）
- `core/arbiter.py` （冷却时间、全局限流、场景门控）
- `core/event_catalog.py` （事件定义）

### 第三轮：对比验证

```bash
# 备份第一轮报告
mv field_test_summary.md field_test_summary_v1.md
mv field_test_report.json field_test_report_v1.json

# 再次运行
python plugin/plugins/openrails_copilot/field_test.py --duration 1800

# 对比
diff field_test_summary_v1.md field_test_summary.md
```

### 迭代直到满意

重复第二轮、第三轮，直到：
- ✅ 播报率在 30%-70% 之间（既不刷屏，也不静默）
- ✅ 没有明显的误报（用户标记的"不应该告警"< 10%）
- ✅ 没有明显的漏报（用户标记的"应该告警但没有"< 5%）

---

## 🎯 推荐配置（保守型 vs 激进型）

### 保守型（宁可漏报，不要误报）

适合**老司机**，只在**确实危险**时才提醒。

```python
# constants.py
OVERSPEED_THRESHOLD_KMH = 10.0          # 超限速 10 km/h 才算
EMERGENCY_BRAKE_THRESHOLD = 0.85        # 制动缸 > 85% 才算紧急
SIGNAL_DISTANCE_WARN_M = 600.0          # 距离红灯 600 米才警告

# arbiter.py
_global_cooldown_s = 20.0               # 任意告警间隔 20 秒
EventSpec.cooldown_s = 30.0             # 单事件冷却 30 秒
```

---

### 激进型（宁可误报，不要漏报）

适合**新手**或**教学场景**，任何可疑操作都提醒。

```python
# constants.py
OVERSPEED_THRESHOLD_KMH = 2.0           # 超限速 2 km/h 就算
EMERGENCY_BRAKE_THRESHOLD = 0.6         # 制动缸 > 60% 就算紧急
SIGNAL_DISTANCE_WARN_M = 1200.0         # 距离红灯 1200 米就警告

# arbiter.py
_global_cooldown_s = 8.0                # 任意告警间隔 8 秒
EventSpec.cooldown_s = 15.0             # 单事件冷却 15 秒
```

---

## 🧪 调试技巧

### 1. 查看某个事件的所有候选

```bash
jq '.alert_log[] | select(.event_id == "OVERSPEED")' field_test_report.json
```

### 2. 查看某个时间段的告警

```bash
# 假设你记得 10:05 左右有个误报
jq '.spoken_log[] | select(.time > 1704531900 and .time < 1704532200)' field_test_report.json
```

### 3. 统计每个事件的播报率

```bash
jq -r '.event_distribution | to_entries[] | "\(.key): \(.value)"' field_test_report.json
```

### 4. 查看断路器状态

```bash
jq '.alert_log[] | select(.block_reason == "circuit_open")' field_test_report.json
```

---

## 📝 提交调优记录

每次调优后，建议记录到 `TUNING_LOG.md`：

```markdown
## 2024-01-15 调优记录

**问题**：超速告警在限速边缘频繁触发

**调整**：
- `OVERSPEED_THRESHOLD_KMH`: 5.0 → 8.0
- `EventSpec("OVERSPEED").cooldown_s`: 20.0 → 30.0

**结果**：
- 播报率：从 85% 降至 45%
- 用户反馈：误报明显减少
```

---

## 💡 高级：自定义调优脚本

如果需要**批量实验**不同参数组合，可以写一个脚本：

```python
# auto_tune.py
import subprocess
import json

configs = [
    {"overspeed": 5.0, "cooldown": 20.0},
    {"overspeed": 8.0, "cooldown": 20.0},
    {"overspeed": 8.0, "cooldown": 30.0},
]

for i, cfg in enumerate(configs):
    # 写入配置
    # ... 修改 constants.py 和 arbiter.py ...
    
    # 运行验证
    subprocess.run([
        "python", "field_test.py",
        "--duration", "600",  # 短周期快速测试
    ])
    
    # 读取结果
    with open("field_test_report.json") as f:
        report = json.load(f)
    
    print(f"Config {i}: block_rate={report['summary']['block_rate']:.2f}")
```

---

**下一步**：运行 `field_test.py`，根据报告调整参数，重复验证！
