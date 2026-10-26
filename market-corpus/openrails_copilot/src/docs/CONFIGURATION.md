# 配置说明

Open Rails Copilot 的所有参数都可以通过 `config.toml` 文件进行配置。

## 配置文件位置

配置文件应放在插件根目录：

```
plugin/plugins/openrails_copilot/config.toml
```

如果没有此文件，插件将使用内置的默认值。你可以复制 `config.example.toml` 作为起点。

## 配置结构

### [openrails] - Open Rails API 配置

```toml
[openrails]
api_url = "http://localhost:2150/API"  # Open Rails API 地址
poll_interval = 2.0                    # 轮询间隔（秒）
```

- `api_url`: Open Rails Web API 的完整 URL
- `poll_interval`: 每次采集状态的间隔时间，建议 1-5 秒

### [detector] - 检测器参数

#### 速度检测

```toml
[detector.speed]
overspeed_threshold = 1.1              # 超速阈值（1.1 = 超过限速 10%）
speed_increase_threshold = 20.0        # 速度快速增加阈值（km/h/s）
speed_decrease_threshold = -30.0       # 速度快速下降阈值（km/h/s）
```

#### 制动检测

```toml
[detector.brake]
brake_pressure_low_threshold = 0.3     # 制动压力低阈值（0.3 = 30%）
brake_force_drop_threshold = 0.5       # 制动力下降阈值（0.5 = 50%）
emergency_brake_speed_threshold = 10.0 # 紧急制动速度阈值（km/h）
```

#### 信号检测

```toml
[detector.signal]
red_signal_speed_threshold = 5.0       # 红灯速度阈值（km/h）
yellow_signal_speed_threshold = 40.0   # 黄灯速度阈值（km/h）
```

#### 停站检测

```toml
[detector.station]
station_stop_tolerance = 20.0          # 停站位置容差（米）
station_stop_time_max = 300.0          # 停站最长时间（秒）
```

### [arbiter] - 告警仲裁器参数

```toml
[arbiter]
global_max_per_minute = 5              # 全局限流：每分钟最多告警数
cooldown_seconds = 60                  # 冷却时间：同类事件最小间隔（秒）
preemption_enabled = true              # 是否启用抢占机制
```

- `global_max_per_minute`: 防止刷屏的最后一道防线
- `cooldown_seconds`: 同一类事件（如超速）的最小间隔
- `preemption_enabled`: 高优先级事件是否可以打断冷却期

### [circuit_breaker] - 断路器参数

```toml
[circuit_breaker]
failure_threshold = 5                  # 失败阈值：连续失败多少次后熔断
reset_timeout = 60.0                   # 重置超时：熔断后多久尝试恢复（秒）
```

## 场景配置

不同驾驶场景可以使用不同的参数配置。插件会根据当前状态自动识别场景：

- **停站场景**：速度 < 5 km/h 且附近有车站
- **调车场景**：速度 < 20 km/h
- **干线场景**：其他情况

你可以为不同场景创建不同的配置文件：

```
config.toml           # 默认配置
config.station.toml   # 停站场景配置
config.shunting.toml  # 调车场景配置
config.mainline.toml  # 干线场景配置
```

## 推荐配置

### 保守模式（适合新手）

```toml
[detector.speed]
overspeed_threshold = 1.05  # 超速 5% 就告警

[arbiter]
global_max_per_minute = 8   # 允许更多告警
cooldown_seconds = 30       # 更短的冷却时间
```

### 平衡模式（推荐）

```toml
[detector.speed]
overspeed_threshold = 1.1   # 超速 10% 告警

[arbiter]
global_max_per_minute = 5   # 适中的告警频率
cooldown_seconds = 60       # 适中的冷却时间
```

### 精简模式（适合老手）

```toml
[detector.speed]
overspeed_threshold = 1.15  # 超速 15% 才告警

[arbiter]
global_max_per_minute = 3   # 只保留最重要的告警
cooldown_seconds = 90       # 更长的冷却时间
```

## 配置管理工具

使用 `config_manager.py` 管理配置：

```bash
# 保存当前配置
python config_manager.py save my_config

# 加载配置
python config_manager.py load my_config

# 对比两个配置
python config_manager.py diff config1 config2

# 导出配置对比表
python config_manager.py export config1 config2
```

## 参数调优建议

详见 [参数调优指南](TUNING_GUIDE.md)。

## 故障排除

### 配置不生效

1. 检查配置文件语法是否正确（TOML 格式）
2. 检查文件名是否为 `config.toml`
3. 重启插件（重启 N.E.K.O）

### 告警太多

- 增加 `global_max_per_minute`
- 增加 `cooldown_seconds`
- 提高各检测器的阈值

### 告警太少

- 降低 `global_max_per_minute`
- 降低 `cooldown_seconds`
- 降低各检测器的阈值

## 参考

- [实战验证快速开始](FIELD_TEST_QUICKSTART.md)
- [参数调优指南](TUNING_GUIDE.md)
- [使用手册](GUIDE.md)
