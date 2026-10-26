# Open Rails Copilot - 实战验证与调优路线图

## 当前状态

✅ **核心架构完成**
- 告警链路：detector → arbiter → push_sender
- 场景门控、冷却机制、断路器保护
- 36 个可检测事件（41 个无数据源已归档）

---

## 📍 现在的位置：实战验证阶段

### 目标
通过真实游戏场景收集数据，调整参数，达到**准确告警、不刷屏、不漏报**的平衡点。

---

## 🗺️ 完整路线图

### 阶段 1：基线验证（1-2 天）

**任务**：
1. 运行完整行程（30-60 分钟），收集基线数据
   ```bash
   python plugin/plugins/openrails_copilot/field_test.py --duration 3600
   ```

2. 查看报告，识别问题
   ```bash
   cat field_test_summary.md
   ```

3. 标记误报/漏报案例

**产出**：
- `field_test_report.json` - 原始数据
- `field_test_summary.md` - 可读报告
- 问题清单（哪些事件误报、哪些漏报）

---

### 阶段 2：参数调优（2-3 天）

**任务**：
1. 保存当前配置为基线
   ```bash
   python plugin/plugins/openrails_copilot/config_manager.py save baseline
   ```

2. 根据报告调整参数（参考 `TUNING_GUIDE.md`）
   - 编辑 `core/constants.py` - 阈值
   - 编辑 `core/arbiter.py` - 冷却时间
   - 编辑 `core/event_catalog.py` - 事件定义

3. 保存新配置
   ```bash
   python plugin/plugins/openrails_copilot/config_manager.py save tuned_v1
   ```

4. 再次验证
   ```bash
   python plugin/plugins/openrails_copilot/field_test.py --duration 1800
   ```

5. 对比结果
   ```bash
   python plugin/plugins/openrails_copilot/config_manager.py diff baseline tuned_v1
   ```

**产出**：
- 2-3 个候选配置（保守型、平衡型、激进型）
- 对比表 `config_comparison.md`

---

### 阶段 3：压力测试（1 天）

**任务**：
1. 故意违规驾驶（超速、冲信号、急刹车）
2. 验证告警是否灵敏
3. 验证是否有漏报

**产出**：
- 灵敏度验证报告
- 漏报修复清单

---

### 阶段 4：静默测试（1 天）

**任务**：
1. 完全按规矩驾驶
2. 验证是否有误报
3. 验证心跳报告是否合理

**产出**：
- 误报修复清单
- 心跳频率调整建议

---

### 阶段 5：定型与文档（1 天）

**任务**：
1. 选定最终配置
2. 更新 README.md，说明推荐配置
3. 录制演示视频（可选）
4. 准备发布（如果要分享给社区）

**产出**：
- 最终配置文件
- 完整的使用指南
- 演示视频（可选）

---

## 📦 工具箱

### 1. 验证工具
- `field_test.py` - 实战验证，生成报告
- `FIELD_TEST_QUICKSTART.md` - 快速启动指南

### 2. 调优工具
- `TUNING_GUIDE.md` - 详细调优指南
- `config_manager.py` - 配置管理工具

### 3. 文档
- `README.md` - 使用说明
- `SUMMARY.md` - 项目总结
- `UNDETECTABLE_EVENTS.md` - 不可检测事件备份

---

## 🎯 成功标准

### 告警质量
- ✅ 播报率 30%-70%（既不刷屏，也不过度静默）
- ✅ 误报率 < 10%（用户标记为"不应该告警"的）
- ✅ 漏报率 < 5%（用户标记为"应该告警但没有"的）

### 场景适应
- ✅ 停站时不误报"制动未缓解""超速"
- ✅ 调车时不误报"超过干线限速"
- ✅ 干线行驶时正常告警

### 用户体验
- ✅ 正常驾驶时安静（30 分钟 ≤ 3 次告警）
- ✅ 违规时立即提醒（2-4 秒内）
- ✅ 不重复刷屏（同一问题不会短时间内多次播报）

---

## 🚀 立即开始

### 第一步：运行基线验证
```bash
# 启动 Open Rails 游戏
# 然后运行验证工具
python plugin/plugins/openrails_copilot/field_test.py --duration 1800
```

### 第二步：查看报告
```bash
cat field_test_summary.md
```

### 第三步：根据报告调优
参考 `TUNING_GUIDE.md` 中的建议，调整参数。

### 第四步：重复验证
```bash
# 保存当前配置
python plugin/plugins/openrails_copilot/config_manager.py save baseline

# 调整参数后再次验证
python plugin/plugins/openrails_copilot/field_test.py --duration 1800

# 对比结果
python plugin/plugins/openrails_copilot/config_manager.py diff baseline tuned_v1
```

---

## 📊 预计时间

| 阶段 | 时间 | 说明 |
|------|------|------|
| 基线验证 | 1-2 天 | 跑几次完整行程，收集数据 |
| 参数调优 | 2-3 天 | 迭代 2-3 轮，找到最佳配置 |
| 压力测试 | 1 天 | 测试灵敏度 |
| 静默测试 | 1 天 | 测试误报率 |
| 定型文档 | 1 天 | 整理文档，准备发布 |
| **总计** | **6-8 天** | 可以根据实际情况缩短 |

---

## 💡 小贴士

### 1. 不必一次跑完 30 分钟
- 可以跑 10 分钟，看看报告，快速迭代
- 最后定型前再跑一次完整行程

### 2. 保存每次的配置
- 方便回滚到之前的版本
- 方便对比不同配置的效果

### 3. 记录调优日志
- 在 `TUNING_LOG.md` 中记录每次调整的原因和结果
- 方便回顾调优过程

### 4. 优先解决高频问题
- 先解决触发次数最多的误报
- 再处理偶尔出现的边缘情况

---

## 📞 需要帮助？

### 文档清单
- `FIELD_TEST_QUICKSTART.md` - 快速开始
- `TUNING_GUIDE.md` - 详细调优指南
- `UNDETECTABLE_EVENTS.md` - API 限制说明

### 示例工作流
```bash
# 1. 基线验证
python field_test.py --duration 1800
python config_manager.py save baseline

# 2. 调优
# 编辑 core/constants.py, core/arbiter.py
python config_manager.py save tuned_v1

# 3. 再次验证
python field_test.py --duration 1800

# 4. 对比
python config_manager.py diff baseline tuned_v1

# 5. 导出对比表
python config_manager.py export baseline tuned_v1 tuned_v2
```

---

**准备好了吗？让我们开始实战验证！** 🚂✨

```bash
python plugin/plugins/openrails_copilot/field_test.py
```
