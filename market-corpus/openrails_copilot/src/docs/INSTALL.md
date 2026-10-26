# Open Rails Copilot Plugin 安装指南

## 快速开始

### 1. 安装依赖

确保已安装 Python 3.10 或更高版本，然后安装 httpx：

```bash
pip install httpx
```

### 2. 安装插件

#### 方法 A：复制到插件目录（推荐）

将整个 `openrails_copilot` 文件夹复制到 N.E.K.O 的插件目录：

```
E:\NEKOdm\N.E.K.O\plugin\plugins\openrails_copilot\
```

#### 方法 B：符号链接（开发模式）

在开发时，可以创建符号链接：

```powershell
# Windows PowerShell (管理员权限)
New-Item -ItemType SymbolicLink -Path "E:\NEKOdm\N.E.K.O\plugin\plugins\openrails_copilot" -Target "E:\n.e.k.o_plugin_or\openrails_copilot"
```

```bash
# Linux/Mac
ln -s /path/to/n.e.k.o_plugin_or/openrails_copilot /path/to/NEKO/plugin/plugins/openrails_copilot
```

### 3. 配置 Open Rails

1. 启动 Open Rails 游戏
2. 打开 **Options** → **Experimental**
3. 勾选 **Web Server** 选项
4. 重启游戏使配置生效

### 4. 验证安装

运行测试脚本：

```bash
cd E:\n.e.k.o_plugin_or\openrails_copilot
python test_plugin.py
```

预期输出：
```
============================================================
Open Rails Copilot Plugin 测试
============================================================
测试 1: 导入插件...
✓ 插件导入成功

测试 2: 实例化插件...
✓ 插件实例化成功
  - API URL: http://localhost:2150
  - 监控间隔: 5.0s

测试 3: API 连接检查...
⚠ Open Rails API 不可用
  这是正常的，如果游戏未运行

测试 4: 数据分析...
✓ 数据分析成功
  - 速度: 108.0 km/h
  - 油门: 80.0%
  - 制动: 0.0%
  - 告警数: 1
  - overspeed: 当前速度 108.0 km/h 可能超速

============================================================
测试总结
============================================================
通过: 4/4

✓ 所有测试通过！插件可以正常使用。
```

### 5. 启动 N.E.K.O

重启 N.E.K.O 应用以加载插件：

```bash
cd E:\NEKOdm\N.E.K.O
python main.py
```

### 6. 使用插件

通过 AI 对话使用插件：

```
用户: 检查一下 Open Rails API 是否可用
AI: [调用 check_api 插件入口]

用户: 请启动 Open Rails 监控
AI: [调用 start_monitoring 插件入口]

用户: Open Rails 现在的状态如何？
AI: [调用 get_status 插件入口]

用户: 停止 Open Rails 监控
AI: [调用 stop_monitoring 插件入口]
```

## 高级配置

### 自定义配置

1. 复制配置示例文件：
   ```bash
   cp E:\n.e.k.o_plugin_or\openrails_copilot\config.example.toml E:\NEKOdm\N.E.K.O\data\plugins\openrails_copilot\config.toml
   ```

2. 编辑配置文件：
   ```toml
   [monitoring]
   api_base_url = "http://localhost:2150"
   api_timeout = 5.0
   status_report_interval = 30.0
   
   [thresholds]
   speed_change_threshold = 15.0
   emergency_brake_threshold = 0.9
   overspeed_threshold = 120.0
   ```

### 端口修改

如果 Open Rails 使用了其他端口，需要修改：

1. 在 Open Rails 设置中查看端口号
2. 修改插件配置中的 `api_base_url`
3. 重启 N.E.K.O

## 故障排查

### 问题 1：插件未加载

**症状**：N.E.K.O 启动时没有看到插件日志

**排查步骤**：
1. 检查插件目录路径是否正确
2. 检查 `plugin.toml` 文件是否存在
3. 查看 N.E.K.O 日志是否有错误信息

### 问题 2：httpx 导入失败

**症状**：`缺少依赖: httpx`

**解决方案**：
```bash
pip install httpx
```

### 问题 3：API 连接失败

**症状**：`Open Rails 未运行或 API 不可用`

**排查步骤**：
1. 确认 Open Rails 游戏已启动
2. 检查游戏是否在活动场景中（非主菜单）
3. 确认 Web Server 已启用（Options → Experimental）
4. 手动访问 `http://localhost:2150/api/apisample` 测试
5. 检查防火墙设置

### 问题 4：监控无数据

**症状**：监控启动但没有收到数据

**排查步骤**：
1. 检查游戏是否暂停
2. 确认列车是否正在运行
3. 查看插件日志中的错误信息

## 卸载

### 完全卸载插件

1. 停止 N.E.K.O
2. 删除插件目录：
   ```bash
   Remove-Item -Path "E:\NEKOdm\N.E.K.O\plugin\plugins\openrails_copilot" -Recurse -Force
   ```
3. 删除用户数据（可选）：
   ```bash
   Remove-Item -Path "E:\NEKOdm\N.E.K.O\data\plugins\openrails_copilot" -Recurse -Force
   ```

### 临时禁用插件

在 `plugin.toml` 中设置：
```toml
[plugin_runtime]
enabled = false
```

## 开发环境设置

### 开发模式安装

使用符号链接以便在开发时实时测试：

```powershell
# 创建符号链接
New-Item -ItemType SymbolicLink -Path "E:\NEKOdm\N.E.K.O\plugin\plugins\openrails_copilot" -Target "E:\n.e.k.o_plugin_or\openrails_copilot"
```

### 热重载

修改插件代码后：
1. 重启 N.E.K.O 应用
2. 或使用插件热重载功能（如果支持）

### 日志调试

在插件代码中使用 logger：

```python
self.logger.debug("调试信息")
self.logger.info("普通信息")
self.logger.warning("警告信息")
self.logger.error("错误信息")
```

## 性能优化

### 调整监控频率

如果 CPU 占用过高，可以降低监控频率：

在 `__init__.py` 中修改：
```python
@timer_interval(
    id="monitor_openrails",
    seconds=5,  # 从 2 秒改为 5 秒
    name="监控 Open Rails 状态",
    auto_start=True,
)
```

### 减少状态报告

减少正常状态报告频率：

```python
self.status_report_interval = 300.0  # 从 60 秒改为 5 分钟
```

## 更新日志

### v0.1.0 (2024-01-XX)
- 初始版本发布
- 基础监控功能
- 异常检测和告警
- AI 集成

## 贡献

欢迎贡献代码和报告问题！

项目地址：`E:\n.e.k.o_plugin_or\openrails_copilot`

## 许可证

本插件遵循 N.E.K.O 项目许可证。
