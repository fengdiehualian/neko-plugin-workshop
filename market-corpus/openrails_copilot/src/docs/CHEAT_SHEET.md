# Open Rails Copilot - 快速参考卡片

> 打印或保存此页面，方便随时查阅常用命令

---

## 🚀 基本使用

### 在 N.E.K.O 中启动监控
```
请启动 Open Rails 监控
```

### 查询当前状态
```
Open Rails 现在的状态如何？
```

### 停止监控
```
停止 Open Rails 监控
```

---

## 🔧 验证与调优

### 运行验证（30 分钟）
```bash
python plugin/plugins/openrails_copilot/field_test.py --duration 1800
```

### 查看报告
```bash
cat field_test_summary.md
```

### 保存当前配置
```bash
python plugin/plugins/openrails_copilot/config_manager.py save my_config
```

### 加载配置
```bash
python plugin/plugins/openrails_copilot/config_manager.py load my_config
```

### 对比两个配置
```bash
python plugin/plugins/openrails_copilot/config_manager.py diff config1 config2
```

### 列出所有配置
```bash
python plugin/plugins/openrails_copilot/config_manager.py list
```

---

## 📊 报告解读

### 播报率

| 播报率 | 含义 | 行动 |
|--------|------|------|
| < 20% | 过度静默 | 减少冷却时间或放宽阈值 |
| 30%-70% | ✅ 合理 | 无需调整 |
| > 80% | 刷屏 | 增加冷却时间或收紧阈值 |

### 自动建议

报告中的"调优建议"会自动标记：
- ⚠️ 过滤率过高/过低
- ⚠️ 某事件触发频率过高
- ✅ 告警分布合理

---

## ⚙️ 参数调整速查

### 收紧告警（减少误报）

**编辑 `core/constants.py`**
```python
OVERSPEED_THRESHOLD_KMH = 8.0  # 从 5.0 改为 8.0
EMERGENCY_BRAKE_THRESHOLD = 0.85  # 从 0.7 改为 0.85
```

**编辑 `core/event_catalog.py`**
```python
EventSpec(
    event_id="OVERSPEED",
    cooldown_s=30.0,  # 从 20.0 改为 30.0（延长冷却）
)
```

### 放宽告警（减少漏报）

**编辑 `core/constants.py`**
```python
OVERSPEED_THRESHOLD_KMH = 3.0  # 从 5.0 改为 3.0
SIGNAL_DISTANCE_WARN_M = 1200.0  # 从 800.0 改为 1200.0
```

**编辑 `core/event_catalog.py`**
```python
EventSpec(
    event_id="OVERSPEED",
    cooldown_s=15.0,  # 从 20.0 改为 15.0（缩短冷却）
)
```

### 调整全局限流

**编辑 `core/arbiter.py`**
```python
class Arbiter:
    def __init__(self, ...):
        self._global_cooldown_s = 15.0  # 从 12.0 改为 15.0
```

### 场景门控（静默特定场景下的告警）

**编辑 `core/arbiter.py`**
```python
_CATEGORY_GATE = {
    "station_idle": {"overspeed", "brake_release", "brake_emergency"},
    "shunting": {"overspeed"},
    "mainline": set(),
}
```

---

## 🎯 常见场景速查

### 场景 A：超速告警太频繁
- 提高 `OVERSPEED_THRESHOLD_KMH`（5.0 → 8.0）
- 延长冷却时间（20.0 → 30.0）

### 场景 B：停站时误报"制动未缓解"
- 在 `_CATEGORY_GATE["station_idle"]` 中添加 `"brake_emergency"`

### 场景 C：红灯警告漏报
- 增大 `SIGNAL_DISTANCE_WARN_M`（800 → 1200）

### 场景 D：告警刷屏
- 延长 `_global_cooldown_s`（12.0 → 18.0）

### 场景 E：断路器误跳闸
- 增大 `_failure_threshold`（5 → 8）
- 延长 `_failure_window_s`（60.0 → 120.0）

---

## 📁 关键文件位置

```
plugin/plugins/openrails_copilot/
├── core/
│   ├── constants.py       ← 阈值参数
│   ├── arbiter.py         ← 冷却时间、全局限流、场景门控
│   ├── event_catalog.py   ← 事件定义
│   ├── detector.py        ← 检测逻辑
│   └── safety_guard.py    ← 断路器参数
├── field_test.py          ← 验证工具
├── config_manager.py      ← 配置管理
└── (文档)
```

---

## 📚 文档索引

| 需求 | 文档 |
|------|------|
| 快速上手 | [FIELD_TEST_QUICKSTART.md](FIELD_TEST_QUICKSTART.md) |
| 详细调优 | [TUNING_GUIDE.md](TUNING_GUIDE.md) |
| 完整流程 | [ROADMAP.md](ROADMAP.md) |
| API 限制 | [UNDETECTABLE_EVENTS.md](UNDETECTABLE_EVENTS.md) |
| 使用说明 | [README.md](README.md) |

---

## 🆘 故障排查

### 插件无法连接游戏
```bash
# 手动测试 API
curl http://localhost:2150/API/CABCONTROLS
```

### 验证工具报错
```bash
# 检查依赖
pip install httpx
```

### 告警完全静默
- 检查 `monitoring_enabled` 是否为 true（调用 start_monitoring）
- 检查断路器是否跳闸（查看日志）
- 运行 `check_api` 命令确认游戏连接

### 报告中"无数据"
- 确认游戏正在运行（不是暂停状态）
- 确认在驾驶舱视图中
- 查看插件日志获取详细错误

---

## 💡 最佳实践

### 验证周期
- ✅ 第一次验证：30 分钟完整行程
- ✅ 调优迭代：10-15 分钟快速测试
- ✅ 最终验证：60 分钟压力测试

### 配置命名
- `baseline` - 基线配置
- `conservative` - 保守型（少误报）
- `aggressive` - 激进型（少漏报）
- `tuned_v1`, `tuned_v2` - 调优版本

### 调优顺序
1. 先解决高频误报（触发次数最多的）
2. 再处理低频边缘情况
3. 最后调整心跳频率

---

**保持这张卡片在手边，开始验证吧！** 🚂✨

```bash
python plugin/plugins/openrails_copilot/field_test.py
```
