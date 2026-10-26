# SP2-Bridge 接口协议 v1

> 面向**模组作者**。本文定义《简单飞机2 SimplePlanes 2》侧模组（下文简称「模组」）
> 与 N.E.K.O. `sp2_copilot` 插件之间的 HTTP 契约。
> 只要按这份文档提供接口，任何程序都能读 —— 模组不依赖 N.E.K.O.，可以独立发布。

## 0. 总览

- 传输：HTTP/1.1，仅本机回环
- 默认监听：`http://127.0.0.1:21580`
- 方法：**只读**。全部为 `GET`，模组**不应**提供任何会改变游戏状态的接口
- 编码：UTF-8，`Content-Type: application/json`
- 版本：本协议为 `protocol = 1`

端点清单：

| 路径 | 用途 | 必需 |
| --- | --- | --- |
| `GET /ping` | 连通性探测 | ✅ |
| `GET /state` | 飞行状态、姿态、操纵输入 | ✅ |
| `GET /aircraft` | 机体、发动机、油量、构型、限速 | ✅ |
| `GET /navigation` | 位置、航向、目标、机场、风 | ⭕ 建议 |
| `GET /environment` | 时间、天气、气温气压、风 | ⭕ 建议 |

插件对每个接口都做了容错：某个接口 404 或超时，只影响依赖它的那一项功能，其余照常工作。
`/state` 是最核心的一个，**至少要实现它**。

## 1. 通用约定

### 1.1 成功响应

每个响应都是 JSON 对象，至少带：

```json
{ "ok": true, "protocol": 1 }
```

HTTP 状态码 `200`。插件把 `>= 400` 一律当作失败。

### 1.2 失败响应

```json
{ "ok": false, "error": "当前不在飞行场景" }
```

建议同时返回 HTTP `404` 或 `503`。插件会把 `error` 原样展示给用户。

### 1.3 数值字段的三条铁律

1. **取不到就省略该字段，或者给 `null`** —— 不要给 `0`、不要给 `-1`、不要给空字符串。
   插件宁可显示「未知」，也不要一个假数字。
2. **不要给 `NaN` / `Infinity`** —— JSON 里它们不是合法字面量。物理计算溢出时请转成 `null`。
3. **不要给字符串形式的数字** —— `"82.4"` 不行，必须是 `82.4`。
   （插件会尝试转换，但请不要依赖这一点。）

### 1.4 单位

一律使用下面的单位，插件不做任何隐式换算：

| 量 | 单位 | 说明 |
| --- | --- | --- |
| 速度 | m/s | 空速、真空速、地速、风速 |
| 高度 | m | 离地高度、海拔、地形高度 |
| 升降率 | m/s | 正=爬升，负=下降 |
| 角度 | 度 | 航向 0-360；俯仰/滚转 正方向见下 |
| 迎角、侧滑 | 度 | |
| 过载 | G | 重力加速度倍数 |
| 油门、操纵输入 | 0-1 | `throttle` 0=慢车 1=满油门 |
| 配平 | 度 或 -1~1 | 用哪个请保持一致并在 README 写明 |
| 油量 | kg 与 0-100 | `current_kg` / `capacity_kg` / `percent` |
| 质量 | kg | |
| 时间 | 秒 | |

角度正方向：

- `pitch`：正 = 机头向上
- `roll`：正 = 右倾（右翼下沉）
- `heading`：0-360，顺时针增大（0=北）
- `aoa`：正 = 机头相对气流上仰

布尔字段必须是 JSON 布尔（`true` / `false`），不要用 `0/1` 或字符串。
（插件对 `0/1` 与 `"true"/"yes"/"on"` 做了兼容，但请不要依赖。）

## 2. `GET /ping`

面板每秒级轮询用，必须**极快返回**（不要在这上面做游戏逻辑查询）。

```json
{
  "ok": true,
  "protocol": 1,
  "service": "SP2-Bridge",
  "game": "SimplePlanes 2",
  "game_version": "0.1.23456",
  "in_world": true
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `service` | string | 固定 `SP2-Bridge` |
| `game` | string | 固定 `SimplePlanes 2` |
| `game_version` | string | 可选 |
| `in_world` | bool | 是否已在世界场景里 |

## 3. `GET /state`

飞行状态。插件用它算飞行阶段与大部分告警。

```json
{
  "ok": true,
  "protocol": 1,
  "scene": "flight",
  "in_world": true,
  "paused": false,
  "mission_time": 245.6,
  "aircraft": { "name": "My Jet", "type": "plane" },
  "flight": {
    "airspeed": 82.4,
    "true_airspeed": 88.0,
    "ground_speed": 84.1,
    "vertical_speed": 4.2,
    "altitude_msl": 1240.5,
    "altitude_agl": 1010.3,
    "terrain_height": 230.2,
    "heading": 271.5,
    "pitch": 3.1,
    "roll": -1.4,
    "aoa": 4.6,
    "sideslip": 0.4,
    "g_force": 1.05,
    "mach": 0.26,
    "stalled": false,
    "stall_warning": false,
    "on_ground": false,
    "crashed": false
  },
  "controls": {
    "throttle": 0.82,
    "pitch_input": 0.0,
    "roll_input": 0.0,
    "yaw_input": 0.0,
    "brake": 0.0,
    "trim": 0.0,
    "flaps": 0.0,
    "airbrake": 0.0,
    "spoiler": 0.0,
    "gear_down": false,
    "autopilot": false,
    "autopilot_mode": ""
  }
}
```

### `scene`

`flight` | `designer` | `menu` | `loading` | `other`

插件只在 `scene == "flight"` 且 `in_world == true` 时判定飞行阶段；
其它场景一律显示「未进入飞行」。

### `flight` 字段

| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `airspeed` | number | ✅ | 指示空速 IAS，m/s |
| `true_airspeed` | number | ⭕ | 真空速 TAS，m/s |
| `ground_speed` | number | ✅ | 地速，m/s |
| `vertical_speed` | number | ✅ | 升降率，m/s，正=爬升 |
| `altitude_msl` | number | ✅ | 海拔，m |
| `altitude_agl` | number | ✅ | 离地高度，m |
| `terrain_height` | number | ⭕ | 正下方地形高度，m |
| `heading` | number | ✅ | 航向，度 |
| `pitch` | number | ✅ | 俯仰，度，正=抬头 |
| `roll` | number | ✅ | 滚转，度，正=右倾 |
| `aoa` | number | ⭕ | 迎角，度 |
| `sideslip` | number | ⭕ | 侧滑角，度 |
| `g_force` | number | ⭕ | 过载，G |
| `mach` | number | ⭕ | 马赫数 |
| `stalled` | bool | ⭕ | 是否已失速 |
| `stall_warning` | bool | ⭕ | 失速警告是否触发 |
| `on_ground` | bool | ✅ | 是否接地 |
| `crashed` | bool | ⭕ | 是否已坠毁 |

> `on_ground` 缺失时，插件用 `altitude_agl <= 5` 兜底；两个都没有就显示「阶段未知」。
> 提供准确值可以显著提高阶段判定的可靠性。

### `controls` 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `throttle` | number 0-1 | 油门 |
| `pitch_input` / `roll_input` / `yaw_input` | number -1~1 | 操纵输入 |
| `brake` | number 0-1 | 刹车 |
| `trim` | number | 配平 |
| `flaps` | number 0-1 | 襟翼放出比例 |
| `airbrake` / `spoiler` | number 0-1 | 减速板 / 扰流板 |
| `gear_down` | bool | 起落架是否放下 |
| `autopilot` | bool | 自动驾驶是否接通 |
| `autopilot_mode` | string | 模式名，可选 |

## 4. `GET /aircraft`

机体与系统。插件用它判油量、发动机、构型告警，以及**所有限速相关的告警**。

```json
{
  "ok": true,
  "protocol": 1,
  "name": "My Jet",
  "type": "plane",
  "part_count": 86,
  "mass_kg": 12450.0,
  "fuel": { "current_kg": 900.0, "capacity_kg": 2400.0, "percent": 37.5 },
  "engines": [
    {
      "index": 0,
      "name": "J-15",
      "type": "jet",
      "running": true,
      "throttle": 0.82,
      "rpm": 12000,
      "power_percent": 88,
      "egt_c": 620,
      "damaged": false,
      "on_fire": false
    }
  ],
  "gear": { "state": "up", "down": false },
  "surfaces": { "flaps_percent": 0, "airbrake_percent": 0, "spoiler_percent": 0 },
  "damage": { "integrity_percent": 92.0, "broken_parts": 1, "on_fire": false },
  "limits": {
    "vne_mps": 220.0,
    "vc_mps": 180.0,
    "stall_speed_mps": 42.0,
    "rotation_speed_mps": 55.0,
    "approach_speed_mps": 60.0,
    "max_g": 9.0,
    "min_g": -4.0,
    "max_mach": 0.95,
    "flap_limit_mps": 130.0,
    "gear_limit_mps": 120.0
  }
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` / `type` | string | 机体名、类型（plane / car / boat / other） |
| `part_count` | int | 零件数 |
| `mass_kg` | number | 总质量 |
| `fuel.percent` | number 0-100 | 剩余油量百分比 |
| `fuel.current_kg` / `capacity_kg` | number | 油量（有 `percent` 时可不填） |
| `engines[]` | array | 见下 |
| `gear.state` | string | `up` / `down` / `moving` |
| `damage.integrity_percent` | number 0-100 | 机体完整度 |
| `damage.broken_parts` | int | 损坏零件数 |
| `damage.on_fire` | bool | 是否起火 |
| `limits.*` | number | **限速数据，能给多少给多少** |

### `limits` 的重要性

插件的所有超速 / 过载告警**只以这里的数值为准**。
机体没有给出某个值时，插件会**跳过对应告警**并在界面上显示「机体未提供」，
绝不会拿现实航空的数字去套 —— 《简单飞机2》是游戏物理，现实数字没有意义。

所以：能算出来就给，算不出来就**不要给**，不要给一个猜的值。

### `engines[]`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | 发动机名 |
| `type` | string | `jet` / `prop` / `piston` / `electric` / `rocket` / `other` |
| `running` | bool | 是否在运转 |
| `throttle` | number 0-1 | 该发动机的油门 |
| `rpm` | number | 转速 |
| `power_percent` | number 0-100 | 输出功率百分比 |
| `damaged` | bool | 是否受损 |
| `on_fire` | bool | 是否着火 |

全部 `running=false` 且在空中时，插件会报「全部发动机停车」。
多台中部分停车时报「发动机停车」并提示推力不对称。

## 5. `GET /navigation`

```json
{
  "ok": true,
  "protocol": 1,
  "position": { "x": 1234.5, "y": 800.0, "z": -4200.0 },
  "heading": 271.5,
  "track": 268.0,
  "target": {
    "name": "Vetusta Airport",
    "x": 0.0, "y": 120.0, "z": 0.0,
    "distance": 12400.0,
    "bearing": 92.0,
    "elevation": 120.0
  },
  "nearest_airport": { "name": "Hillside Strip", "distance": 3200.0, "bearing": 15.0 },
  "wind": { "direction_deg": 210.0, "speed_mps": 6.4 }
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `position` | object | 世界坐标（SP2 用的是世界坐标，经纬度可选） |
| `heading` | number | 当前航向 |
| `track` | number | 航迹角（可选） |
| `target` | object | 当前航路点 / 目标；没有就省略或给 `null` |
| `target.distance` | number | 到目标的水平距离，m |
| `target.bearing` | number | 目标方位，度 |
| `nearest_airport` | object | 最近机场；没有就省略 |
| `wind` | object | 风向（来向）与风速 |

插件会自己算「该往哪边转多少度」和「按当前地速多久到」，模组不用提供。

## 6. `GET /environment`

```json
{
  "ok": true,
  "protocol": 1,
  "time_of_day": "14:32",
  "weather": "clear",
  "temperature_c": 15.2,
  "pressure_hpa": 1013.2,
  "wind": { "direction_deg": 210.0, "speed_mps": 6.4, "gust_mps": 8.1 },
  "visibility_m": 10000.0,
  "sea_level_m": 0.0
}
```

`weather`：`clear` / `cloudy` / `rain` / `storm` / `fog` / `snow`，未知就省略。

## 7. 模组实现建议

- **监听地址可配置**，默认 `127.0.0.1:21580`。端口改动必须在模组侧和插件侧同时改。
- **只监听回环**，不要绑 `0.0.0.0`，这是本机调试接口。
- **不要用游戏主线程做 HTTP 响应**：把遥测快照缓存在主线程按帧更新，HTTP 线程只读缓存。
  否则联机或大机体时会把游戏卡住。
- **字段名照抄本文档**，插件不做别名映射；未来新增字段请用新名字，插件会忽略不认识的字段。
- **不要提供写接口**。插件是只读副驾驶，协议 v1 也只有 `GET`。
  如果将来需要控制能力，会另开 v2 并明确区分读写端点。

## 8. 版本演进

`protocol` 字段用于兼容：

- 插件读到未知但**更大**的 `protocol` 时，仍然按 v1 解析已知字段
- 新增字段视为可选；删除字段视为「读不到」
- 破坏性变更必须递增 `protocol` 并同步更新本文档
