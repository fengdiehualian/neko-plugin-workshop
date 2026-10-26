# 事件目录

Open Rails Copilot 支持检测 36 种安全事件，分为 5 大类。

## 🚄 速度与信号（12 项）

### 超速类

| 事件 ID | 名称 | 描述 | 优先级 |
|---------|------|------|--------|
| `overspeed_relative` | 相对超速 | 超过当前限速 X% | 高 |
| `overspeed_absolute` | 绝对超速 | 超过任何合理限速（如 200 km/h） | 严重 |
| `overspeed_at_station` | 站内超速 | 接近车站时速度过快 | 高 |
| `overspeed_at_signal` | 信号前超速 | 接近停车信号时速度过快 | 严重 |

### 信号违规

| 事件 ID | 名称 | 描述 | 优先级 |
|---------|------|------|--------|
| `signal_violation_red` | 闯红灯 | 红灯时仍保持较高速度 | 严重 |
| `signal_violation_yellow` | 黄灯未减速 | 黄灯时未减速 | 高 |
| `signal_violation_approach` | 接近信号未准备 | 距离信号过近但未开始制动 | 中 |

### 速度异常

| 事件 ID | 名称 | 描述 | 优先级 |
|---------|------|------|--------|
| `speed_rapid_increase` | 速度快速增加 | 加速度过大（如 > 20 km/h/s） | 中 |
| `speed_rapid_decrease` | 速度快速下降 | 减速度过大（如 < -30 km/h/s） | 高 |
| `speed_oscillation` | 速度振荡 | 速度频繁波动 | 低 |
| `speed_zero_with_throttle` | 静止时加油门 | 速度为 0 但油门不为 0（可能溜车） | 中 |

## 🛑 制动系统（8 项）

### 制动失效

| 事件 ID | 名称 | 描述 | 优先级 |
|---------|------|------|--------|
| `brake_pressure_low` | 制动压力不足 | 制动管压力低于安全值 | 严重 |
| `brake_force_drop` | 制动力下降异常 | 制动力突然大幅下降 | 严重 |
| `brake_response_delayed` | 制动响应延迟 | 施加制动后压力上升缓慢 | 高 |

### 制动不当

| 事件 ID | 名称 | 描述 | 优先级 |
|---------|------|------|--------|
| `emergency_brake_misuse` | 误用紧急制动 | 非紧急情况下使用紧急制动 | 中 |
| `brake_excessive` | 过度制动 | 制动力过大导致不适 | 低 |
| `brake_at_high_speed` | 高速制动过猛 | 高速时制动过猛可能导致危险 | 高 |

### 驻车制动

| 事件 ID | 名称 | 描述 | 优先级 |
|---------|------|------|--------|
| `parking_brake_moving` | 行驶时拉手刹 | 速度 > 0 时使用驻车制动 | 严重 |
| `parking_brake_not_released` | 未松手刹行驶 | 启动时忘记松开驻车制动 | 高 |

## ⚡ 牵引与动力（6 项）

### 牵引异常

| 事件 ID | 名称 | 描述 | 优先级 |
|---------|------|------|--------|
| `traction_uphill_low_power` | 上坡动力不足 | 上坡时功率过低可能溜车 | 高 |
| `traction_downhill_acceleration` | 下坡仍加速 | 下坡时不应加油门 | 中 |
| `traction_slip` | 牵引打滑 | 车轮打滑（需要撒砂） | 中 |

### 动力系统

| 事件 ID | 名称 | 描述 | 优先级 |
|---------|------|------|--------|
| `power_loss` | 动力丢失 | 牵引力突然消失 | 严重 |
| `power_overload` | 动力过载 | 功率超过额定值 | 高 |
| `sanders_misuse` | 撒砂不当 | 不需要时使用撒砂装置 | 低 |

## 🚉 运行状态（6 项）

### 停站问题

| 事件 ID | 名称 | 描述 | 优先级 |
|---------|------|------|--------|
| `station_overrun` | 超站 | 超过停车标志/站台 | 严重 |
| `station_stop_short` | 停车位置不准 | 停车位置距离标准位置过远 | 中 |
| `station_dwell_too_long` | 停站时间过长 | 超过计划停站时间 | 低 |

### 进路安全

| 事件 ID | 名称 | 描述 | 优先级 |
|---------|------|------|--------|
| `route_turnout_not_locked` | 道岔未锁定 | 进入未锁定的道岔 | 严重 |
| `route_atp_restriction` | ATP 限制 | 违反 ATP 系统限制 | 严重 |
| `route_atc_restriction` | ATC 限制 | 违反 ATC 系统限制 | 严重 |

## 🌍 环境与系统（4 项）

### 环境条件

| 事件 ID | 名称 | 描述 | 优先级 |
|---------|------|------|--------|
| `weather_high_speed` | 恶劣天气高速 | 雨/雪/雾天气高速行驶 | 高 |
| `door_open_moving` | 车门未关闭 | 行驶时车门开启 | 严重 |

### 系统状态

| 事件 ID | 名称 | 描述 | 优先级 |
|---------|------|------|--------|
| `fuel_low` | 燃油不足 | 燃油/电量低于安全值 | 中 |
| `odometer_anomaly` | 里程异常 | 里程数据异常（可能传感器故障） | 中 |

## 优先级说明

- **严重（Critical）**: 可能导致事故的危险情况，必须立即处理
- **高（High）**: 违反安全规则或不当操作，需要尽快纠正
- **中（Medium）**: 不规范操作或潜在风险，应该注意
- **低（Low）**: 次要问题或操作建议

## 场景感知

插件会根据当前场景调整检测策略：

### 停站场景（速度 < 5 km/h 且附近有车站）

- **高优先级**: 停车位置、车门状态
- **降低优先级**: 速度相关告警

### 调车场景（速度 < 20 km/h）

- **高优先级**: 道岔状态、进路安全
- **降低优先级**: 超速告警

### 干线场景（正常行驶）

- **高优先级**: 信号遵守、速度控制、制动系统
- **正常优先级**: 其他所有检测

## API 限制

由于 Open Rails Web API 的限制，以下 41 个事件**无法检测**：

- 司机注意力相关（12 项）
- 铁路规章相关（8 项）
- 环境与协作相关（11 项）
- 设备与维护相关（10 项）

详见 [无法检测的事件](UNDETECTABLE_EVENTS.md)。

## 自定义事件

你可以通过修改 `core/detector.py` 添加自定义检测逻辑。每个检测函数应：

1. 接收 `Snapshot` 和 `PreviousSnapshot` 作为参数
2. 返回 `Optional[DetectedEvent]`
3. 设置合适的优先级和场景类型

示例：

```python
def detect_custom_event(
    snap: Snapshot,
    prev: Optional[Snapshot]
) -> Optional[DetectedEvent]:
    if snap.speed > 100 and snap.throttle > 0.8:
        return DetectedEvent(
            event_id="custom_high_speed_throttle",
            message="高速状态下油门过大",
            priority=EventPriority.MEDIUM,
            scene_type=SceneType.MAINLINE
        )
    return None
```

## 参考

- [参数调优指南](TUNING_GUIDE.md)
- [配置说明](CONFIGURATION.md)
- [API 限制说明](UNDETECTABLE_EVENTS.md)
