# 🚂 Open Rails Copilot - 安装和使用指南

## 📦 插件已安装

插件文件位于：`E:\NEKOdm\N.E.K.O\plugin\plugins\openrails_copilot\`

## ⚡ 快速开始

### 1️⃣ 安装依赖

```bash
pip install httpx
```

### 2️⃣ 启动 Open Rails 游戏

确保游戏正在运行，且 REST API 已启用（默认端口 2150）

### 3️⃣ 测试 API 连接

```bash
# 快速状态查询
python E:\n.e.k.o_plugin_or\openrails_copilot\quick_status.py
```

应该看到类似输出：
```
============================================================
Open Rails 当前状态快照
============================================================

🕐 游戏时间:    06:03:53
🚂 方向:        前进
📊 速度:        0.0 km/h
🚥 限速:        45 km/h
⚡ 油门:        7.8%
🛑 制动:        0.0%
📈 加速度:      -0.00
⚠️  超速警告:    否
```

### 4️⃣ 在 N.E.K.O 中使用插件

与 AI 对话时使用以下命令：

#### 🟢 启动监控
```
请启动 Open Rails 监控
```
或
```
开始监控 Open Rails
```

#### 📊 查询状态
```
Open Rails 现在的状态如何？
```
或
```
告诉我列车现在的情况
```
或
```
报告火车状态
```

#### 🔴 停止监控
```
停止 Open Rails 监控
```
或
```
关闭监控
```

## 🎮 游戏内操作测试

启动监控后，在游戏中进行以下操作来测试插件功能：

### 测试 1: 正常行驶
1. 将方向切换到**前进档**
2. 增加油门到 20-30%
3. 观察速度逐渐上升
4. AI 会报告："列车运行正常。速度 X km/h..."

### 测试 2: 超速警报
1. 继续加速，超过当前路段的限速
2. AI 会警告："⚠️ 当前速度 X km/h 超过限速 Y km/h"

### 测试 3: 紧急制动
1. 快速将制动拉到 80% 以上
2. AI 会立即报警："🚨 检测到紧急制动！制动力：X%"

### 测试 4: 速度剧变
1. 从高速突然减速（变化超过 20 km/h）
2. AI 会提醒："⚠️ 速度急剧变化：从 X km/h 到 Y km/h"

## 🧪 独立测试脚本

在开发目录提供了额外的测试工具：

### 完整 API 测试
```bash
python E:\n.e.k.o_plugin_or\openrails_copilot\test_real_api.py
```
测试所有 API 端点和数据解析

### 实时监控演示
```bash
python E:\n.e.k.o_plugin_or\openrails_copilot\monitor_demo.py
```
模拟插件的实时监控功能（按 Ctrl+C 停止）

### 快速状态查询
```bash
python E:\n.e.k.o_plugin_or\openrails_copilot\quick_status.py
```
获取一次性状态快照

## 📋 监控的数据

插件会实时追踪以下参数：

| 参数 | 说明 | 单位 |
|------|------|------|
| 速度 | 列车当前速度 | km/h |
| 限速 | 当前路段限速 | km/h |
| 油门 | 油门位置 | % |
| 制动 | 制动力度 | % |
| 方向 | 前进/空档/倒车 | - |
| 加速度 | 当前加速度 | m/s² |
| 游戏时间 | 游戏内时间 | HH:MM:SS |
| 超速警告 | 游戏内置警告 | 是/否 |

## ⚠️ 异常警报说明

### 🚨 高优先级（立即通知）
- **紧急制动**: 制动力 > 80%
- **超速警告激活**: 游戏内置超速标志

### ⚠️ 中优先级（重要提醒）
- **超速**: 速度超过限速
- **速度剧变**: 短时间内速度变化 > 20 km/h

### ✅ 正常状态（定期报告）
- 每 60 秒报告一次正常运行状态

## 🔧 故障排除

### ❌ "Open Rails 未运行或 API 不可用"

**原因**:
- 游戏未启动
- REST API 未启用
- 端口 2150 被占用

**解决**:
1. 确认游戏正在运行
2. 检查游戏设置中的 API 选项
3. 手动测试：`curl http://localhost:2150/API/CABCONTROLS`

### ❌ "ModuleNotFoundError: No module named 'httpx'"

**原因**: 缺少 httpx 依赖

**解决**:
```bash
pip install httpx
```

### ❌ 监控启动但无数据

**原因**:
- 游戏处于暂停状态
- 不在驾驶舱视图中

**解决**:
1. 恢复游戏运行
2. 切换到驾驶舱视角

## 📚 文档位置

- **使用文档**: `E:\NEKOdm\N.E.K.O\plugin\plugins\openrails_copilot\README.md`
- **完成总结**: `E:\n.e.k.o_plugin_or\openrails_copilot\SUMMARY.md`
- **本指南**: `E:\n.e.k.o_plugin_or\openrails_copilot\GUIDE.md`

## 🎯 下一步

1. ✅ 依赖已安装
2. ✅ 游戏已运行
3. ✅ API 连接正常
4. 🎮 开始在 N.E.K.O 中使用插件！

---

**享受你的智能副驾驶！** 🚂✨
