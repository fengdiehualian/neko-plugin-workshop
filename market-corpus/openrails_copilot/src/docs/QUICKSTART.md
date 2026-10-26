# 🚂 Open Rails Copilot - 快速开始

## 5 分钟快速上手

### Step 1: 安装依赖（1 分钟）

```bash
pip install httpx
```

### Step 2: 部署插件（1 分钟）

插件已安装在测试环境：
```
E:\NEKOdm\N.E.K.O\plugin\plugins\openrails_copilot\
```

如需重新安装，复制整个文件夹到上述路径。

### Step 3: 配置 Open Rails（2 分钟）

1. 启动 Open Rails 游戏
2. 点击 **Options** 按钮
3. 进入 **Experimental** 选项卡
4. 勾选 **☑ Web Server** 复选框
5. 点击 **OK** 保存设置
6. 进入任意活动场景（开始游戏）

### Step 4: 启动监控（1 分钟）

在 N.E.K.O 对话中输入：

```
请启动 Open Rails 监控
```

或：

```
检查一下 Open Rails API 是否可用
```

### Step 5: 享受监控 🎉

监控启动后，插件会：
- ✅ 每 2 秒检查游戏状态
- ✅ 检测到异常时自动告警
- ✅ 每分钟报告正常状态

## 测试插件是否工作

### 方法 1: 运行测试脚本

```bash
python E:\n.e.k.o_plugin_or\openrails_copilot\simple_test.py
```

### 方法 2: 手动测试 API

在浏览器访问：
```
http://localhost:2150/api/apisample
```

如果看到 JSON 数据，说明 Open Rails API 正常工作。

### 方法 3: 在 N.E.K.O 中测试

```
用户: 检查 Open Rails API 是否可用
AI: [调用插件检查]

用户: 请启动 Open Rails 监控
AI: [开始监控]

用户: 现在的状态如何？
AI: [报告当前速度、油门、制动等数据]
```

## 常见对话示例

### 启动监控
```
用户: 帮我监控一下 Open Rails
AI: 好的，正在启动监控...
    Open Rails 监控已启动
    当前速度 45.3 km/h，油门 60%，制动 0%
```

### 查询状态
```
用户: 列车现在跑多快？
AI: [查询插件数据]
    当前速度 85.2 km/h，油门 75%，制动 5%
    运行状态正常
```

### 异常告警
```
AI: [Open Rails 监控] 检测到紧急制动！制动力：95.0%
    当前状态：速度 62.3 km/h，油门 0%，制动 95%
    
用户: 怎么了？
AI: 刚才检测到紧急制动，可能遇到了紧急情况或信号灯。
    现在速度正在快速下降。
```

### 超速提醒
```
AI: [Open Rails 监控] 当前速度 112.5 km/h 可能超速
    当前状态：速度 112.5 km/h，油门 85%，制动 0%
    
用户: 知道了，我减速
```

### 停止监控
```
用户: 停止监控 Open Rails
AI: 好的，Open Rails 监控已停止
```

## 告警类型说明

| 告警类型 | 触发条件 | 优先级 | 描述 |
|---------|---------|--------|------|
| 紧急制动 | 制动力 > 80% | 高 | 检测到紧急制动操作 |
| 速度剧变 | 速度变化 > 20 km/h | 中 | 速度短时间内大幅变化 |
| 超速告警 | 速度 > 100 km/h | 中 | 可能超过限速 |

## 配置调整

如需自定义告警阈值，创建配置文件：

```bash
# 复制配置示例
cp E:\n.e.k.o_plugin_or\openrails_copilot\config.example.toml E:\NEKOdm\N.E.K.O\data\plugins\openrails_copilot\config.toml

# 编辑配置
notepad E:\NEKOdm\N.E.K.O\data\plugins\openrails_copilot\config.toml
```

修改阈值：
```toml
[thresholds]
speed_change_threshold = 15.0    # 降低到 15 km/h
emergency_brake_threshold = 0.9   # 提高到 90%
overspeed_threshold = 120.0       # 提高到 120 km/h
```

## 故障排查速查表

| 问题 | 可能原因 | 解决方案 |
|------|---------|---------|
| "API 不可用" | 游戏未启动 | 启动 Open Rails 游戏 |
| | Web Server 未启用 | 在设置中启用 Web Server |
| | 在主菜单 | 进入活动场景 |
| "缺少依赖: httpx" | httpx 未安装 | `pip install httpx` |
| 监控无数据 | 游戏暂停 | 恢复游戏 |
| | 列车未运行 | 开始驾驶列车 |

## 性能影响

- **CPU 占用**: < 1%
- **内存占用**: < 10 MB
- **网络流量**: 本地回环，无外网流量
- **游戏影响**: 无明显影响

## 安全说明

- ✅ 仅读取游戏数据，不修改游戏
- ✅ 仅本地通信 (localhost)
- ✅ 不收集或上传任何数据
- ✅ 开源代码，可审查

## 卸载插件

如需卸载：

```bash
# 停止 N.E.K.O
# 然后删除插件目录
Remove-Item -Path "E:\NEKOdm\N.E.K.O\plugin\plugins\openrails_copilot" -Recurse -Force
```

## 需要帮助？

- 📖 查看 [完整文档](README.md)
- 🔧 查看 [安装指南](INSTALL.md)
- 📊 查看 [项目总结](../PROJECT_SUMMARY.md)
- 🐛 [报告问题](https://github.com/your-repo/issues)

## 开始监控吧！🚀

现在你已经准备好了：

1. ✅ 依赖已安装
2. ✅ 插件已部署
3. ✅ Open Rails 配置完成
4. ✅ 知道如何使用

**下一步**: 启动游戏，开始监控！

```
用户: 请启动 Open Rails 监控
```

祝驾驶愉快！🚂💨
