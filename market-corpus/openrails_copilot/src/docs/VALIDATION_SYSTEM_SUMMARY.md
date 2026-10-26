# Open Rails Copilot - 实战验证体系总结

## 🎉 完成内容

本次为 Open Rails Copilot 插件建立了**完整的实战验证与调优体系**。

---

## 📦 交付物清单

### 1. 核心工具

| 文件 | 用途 | 关键功能 |
|------|------|---------|
| `field_test.py` | 实战验证工具 | • 运行指定时长的监控周期<br>• 收集所有候选告警和实际播报<br>• 生成 JSON 原始数据 + Markdown 报告<br>• 自动分析过滤率、事件分布、场景分布<br>• 给出调优建议 |
| `config_manager.py` | 配置管理工具 | • 保存/加载不同的参数配置<br>• 对比两个配置的差异<br>• 导出配置对比表（Markdown）<br>• 自动备份当前配置 |

### 2. 完整文档

| 文档 | 用途 | 亮点 |
|------|------|------|
| `FIELD_TEST_QUICKSTART.md` | 验证工具快速开始 | • 3 步上手<br>• 实时输出示例<br>• 报告解读指南<br>• 检查清单 |
| `TUNING_GUIDE.md` | 参数调优指南 | • 5 类参数详解（阈值、冷却、限流、门控、断路器）<br>• 6 个常见场景 + 解决方案<br>• 保守型/激进型推荐配置<br>• 调试技巧 |
| `ROADMAP.md` | 验证与调优路线图 | • 5 阶段完整流程（基线→调优→压力测试→静默测试→定型）<br>• 预计时间：6-8 天<br>• 成功标准<br>• 工具箱索引 |

### 3. 更新的主文档

- `README.md` - 新增"实战验证与调优"章节，更新架构图和版本历史

---

## 🎯 核心价值

### 问题：架构完成了，但参数还没调好

插件核心架构在上一阶段已经重构完成：
- ✅ 模块化设计（core/ 目录）
- ✅ 告警链路（detector → arbiter → push_sender）
- ✅ 场景门控、冷却机制、断路器保护
- ✅ 36 个可检测事件

但是：
- ❓ 阈值是否合理？（会不会太敏感或太迟钝）
- ❓ 冷却时间是否合适？（会不会刷屏或过度静默）
- ❓ 场景分类是否准确？（会不会在错误的场景下告警）

### 解决方案：实战验证 + 数据驱动调优

不是"拍脑袋"设参数，而是：

1. **收集真实数据**：在真实游戏场景中运行 30-60 分钟，记录所有候选告警和实际播报
2. **分析告警质量**：过滤率、事件分布、误报/漏报
3. **调整参数**：根据数据分析结果，调整阈值、冷却时间等
4. **A/B 对比**：保存不同配置，对比效果
5. **迭代优化**：重复 2-3 轮，直到满意

---

## 🔧 工作流演示

### 第一轮：基线验证

```bash
# 1. 启动 Open Rails 游戏，开始一段正常驾驶

# 2. 运行验证工具
python plugin/plugins/openrails_copilot/field_test.py --duration 1800

# 实时输出：
# ✅ [00:00] 速度 0 km/h / 限速 45 km/h | 场景: station_idle
# ✅ [00:30] 速度 25 km/h / 限速 45 km/h | 场景: mainline
# 🚨 [01:15] OVERSPEED
# 🚨 [01:17] OVERSPEED  <-- 重复了！
# 🚨 [01:19] OVERSPEED  <-- 又来！
# ...

# 3. 查看报告
cat field_test_summary.md

# 报告显示：
# - 候选告警：120 次
# - 实际播报：95 次（79% 播报率）
# - 被过滤：25 次
# - ⚠️ 过滤率过低 (<30%)：可能导致告警刷屏
# - ⚠️ `OVERSPEED` 触发频率过高 (450/900 周期)
```

### 第二轮：参数调优

```bash
# 1. 保存当前配置为基线
python plugin/plugins/openrails_copilot/config_manager.py save baseline

# 2. 根据报告调整参数
# 编辑 core/constants.py:
#   OVERSPEED_THRESHOLD_KMH = 5.0  →  8.0  (放宽阈值)
# 编辑 core/arbiter.py:
#   EventSpec("OVERSPEED").cooldown_s = 20.0  →  30.0  (延长冷却)

# 3. 保存新配置
python plugin/plugins/openrails_copilot/config_manager.py save tuned_v1

# 4. 再次验证
python plugin/plugins/openrails_copilot/field_test.py --duration 1800

# 实时输出：
# ✅ [00:00] 速度 0 km/h / 限速 45 km/h | 场景: station_idle
# ✅ [00:30] 速度 25 km/h / 限速 45 km/h | 场景: mainline
# 🚨 [01:15] OVERSPEED
# ✅ [01:30] 速度 48 km/h / 限速 60 km/h | 场景: mainline
# ... (安静了很多)

# 5. 查看新报告
cat field_test_summary.md

# 报告显示：
# - 候选告警：60 次（从 120 降到 60）
# - 实际播报：35 次（58% 播报率，从 79% 降到 58%）
# - 被过滤：25 次
# - ✅ 告警分布合理，无明显调优需求

# 6. 对比两个配置
python plugin/plugins/openrails_copilot/config_manager.py diff baseline tuned_v1

# 输出：
# ━━━ constants.py ━━━
#   OVERSPEED_THRESHOLD_KMH              : 5.0             → 8.0
# 
# ━━━ arbiter.py ━━━
#   (在 event_catalog.py 中 OVERSPEED 的 cooldown_s 从 20.0 改为 30.0)
```

### 效果对比

| 指标 | baseline | tuned_v1 | 改善 |
|------|----------|----------|------|
| 候选告警 | 120 | 60 | -50% |
| 实际播报 | 95 | 35 | -63% |
| 播报率 | 79% | 58% | ✅ 更合理 |
| OVERSPEED 触发率 | 50% | 20% | ✅ 更合理 |

---

## 📊 验证工具的输出

### 1. 实时输出（终端）

```
[INFO] 🚂 Open Rails Copilot 实战验证启动
[INFO]    监控时长: 1800秒 (30分钟)
[INFO]    按 Ctrl+C 可提前停止

✅ [00:00] 速度 0 km/h / 限速 45 km/h | 场景: station_idle
✅ [00:30] 速度 25 km/h / 限速 45 km/h | 场景: mainline
🚨 [01:15] OVERSPEED
✅ [01:00] 速度 48 km/h / 限速 60 km/h | 场景: mainline
🚨 [02:30] SIGNAL_APPROACH_RED
...
```

### 2. JSON 原始数据（`field_test_report.json`）

```json
{
  "meta": {
    "duration_seconds": 1843.5,
    "cycle_count": 921,
    "start_time": "2024-01-15T10:30:00Z",
    "end_time": "2024-01-15T11:00:43Z"
  },
  "summary": {
    "total_candidates": 120,
    "total_spoken": 35,
    "blocked_count": 85,
    "block_rate": 0.708
  },
  "event_distribution": {
    "OVERSPEED": 45,
    "SIGNAL_APPROACH_RED": 12,
    "BRAKE_EMERGENCY": 8,
    ...
  },
  "alert_log": [
    {
      "time": 1705318215.3,
      "event_id": "OVERSPEED",
      "message": "当前速度 108 km/h 超过限速 100 km/h",
      "severity": "P1",
      "allowed": true,
      "block_reason": null,
      "scenario": "mainline"
    },
    ...
  ]
}
```

### 3. Markdown 报告（`field_test_summary.md`）

```markdown
# Open Rails Copilot - 实战验证报告

**验证时长**: 30.7 分钟 (921 个监控周期)
**开始时间**: 2024-01-15 10:30:00
**结束时间**: 2024-01-15 11:00:43

## 📊 告警统计

- **候选告警总数**: 120
- **实际播报**: 35 (29.2%)
- **被过滤**: 85 (70.8%)

### 告警事件分布（所有候选）

| 事件 ID | 候选次数 | 播报次数 | 过滤次数 | 播报率 |
|---------|---------|---------|---------|-------|
| `OVERSPEED` | 45 | 12 | 33 | 27% |
| `SIGNAL_APPROACH_RED` | 12 | 8 | 4 | 67% |
| `BRAKE_EMERGENCY` | 8 | 3 | 5 | 38% |
...

## 💡 调优建议

⚠️ **过滤率过高 (>80%)**：大量候选被过滤...
```

---

## 🎯 成功标准

### 告警质量指标

| 指标 | 目标 | 说明 |
|------|------|------|
| 播报率 | 30%-70% | 太高=刷屏，太低=过度静默 |
| 误报率 | < 10% | 用户标记为"不应该告警"的 |
| 漏报率 | < 5% | 用户标记为"应该告警但没有"的 |

### 场景适应性

- ✅ 停站时不误报"制动未缓解""超速"
- ✅ 调车时不误报"超过干线限速"
- ✅ 干线行驶时正常告警

### 用户体验

- ✅ 正常驾驶时安静（30 分钟 ≤ 3 次告警）
- ✅ 违规时立即提醒（2-4 秒内）
- ✅ 不重复刷屏（同一问题不会短时间内多次播报）

---

## 🚀 下一步行动

### 立即可做

1. **运行第一次验证**
   ```bash
   python plugin/plugins/openrails_copilot/field_test.py --duration 1800
   ```

2. **查看报告，识别问题**
   ```bash
   cat field_test_summary.md
   ```

3. **参考调优指南调整参数**
   - 参考 `TUNING_GUIDE.md`
   - 编辑 `core/constants.py` 和 `core/arbiter.py`

### 完整流程（6-8 天）

详见 `ROADMAP.md`：
- 第 1-2 天：基线验证
- 第 3-5 天：参数调优（2-3 轮迭代）
- 第 6 天：压力测试
- 第 7 天：静默测试
- 第 8 天：定型与文档

---

## 💡 设计亮点

### 1. 数据驱动，而非经验主义

不是"感觉这个阈值应该是 X"，而是：
- 在真实场景中收集数据
- 看实际触发分布
- 根据数据调整

### 2. A/B 测试，而非一次定型

- 保存多个配置
- 对比效果
- 回滚方便

### 3. 自动分析，而非人工判断

- 工具自动计算过滤率、事件分布
- 自动给出调优建议
- 报告清晰易读

### 4. 迭代优化，而非一步到位

- 第一轮不求完美
- 快速迭代 2-3 轮
- 逐步收敛到最佳配置

---

## 📚 文档结构

```
plugin/plugins/openrails_copilot/
├── README.md                      # 主文档（已更新）
├── FIELD_TEST_QUICKSTART.md       # 验证工具快速开始（新）
├── TUNING_GUIDE.md                # 详细调优指南（新）
├── ROADMAP.md                     # 验证与调优路线图（新）
├── field_test.py                  # 验证工具（新）
├── config_manager.py              # 配置管理工具（新）
├── UNDETECTABLE_EVENTS.md         # 不可检测事件（已有）
├── SUMMARY.md                     # 项目总结（已有）
├── PROJECT_COMPLETE.md            # 项目完成报告（已有）
├── QUICKSTART.md                  # 快速开始（已有）
├── INSTALL.md                     # 安装说明（已有）
└── GUIDE.md                       # 使用指南（已有）
```

---

## 🎊 总结

Open Rails Copilot 插件现在拥有：

1. ✅ **完整的核心架构**（上一阶段完成）
   - 模块化设计
   - 告警链路
   - 场景门控、冷却、断路器

2. ✅ **完整的验证工具**（本次完成）
   - 实战数据收集
   - 自动分析报告
   - 配置管理

3. ✅ **完整的调优文档**（本次完成）
   - 快速开始指南
   - 详细调优指南
   - 完整路线图

**接下来只需要**：在真实游戏中运行验证工具，根据报告调优参数，2-3 轮迭代后即可达到生产可用的质量！

---

**立即开始实战验证！** 🚂✨

```bash
python plugin/plugins/openrails_copilot/field_test.py
```
