<p align="center"><img src="icon.png" width="160" alt="米家智能家居插件图标"></p>

# 米家智能家居插件 (Mijia Plugin)

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

基于小米 MiOT 协议，通过 **N.E.K.O AI 伙伴**用**自然语言**控制米家智能设备。无需记忆设备 ID 或技术参数，像聊天一样下达指令即可完成开关灯、调空调、切换模式、查状态、执行场景。

## 功能概览

| 能力 | 说明 |
|------|------|
| 账号登录 | 扫码登录小米账号，凭据本地安全存储（0600 文件权限，Windows icacls） |
| 设备发现 | 获取家庭下全部设备，自动缓存规格（properties / actions，含 value_list 描述） |
| 智能控制 | 一句话开关 / 调属性 / 切模式 / 相对调整 / 极值 / 颜色 / 动作动词 / 场景 |
| 状态查询 | 按名称批量读取设备所有可读属性，中文名 + 单位本地化 |
| 别名 | `set_device_alias` 给设备设自定义别名，语音控制更方便 |
| 场景联动 | 执行米家 App 中预设的智能场景（`执行回家模式`） |

## 代码结构

```
./
├── plugin.toml          # 插件清单（id=mijia，Hosted UI dashboard + 快速开始）
├── __init__.py          # MijiaPlugin：入口 + 执行分支 + 缓存/凭据管理
├── nlp/                 # 自然语言规则引擎（纯函数，无 IO/API 依赖）
│   ├── router.py            # 五步短路：场景→开关标记→查询→动作→属性控制
│   ├── control_parser.py    # 分界 + 意图解析 → ParseResult(含 unit)
│   ├── device_matcher.py    # 匹配：精确别名>精确名>区域拆分>模糊 → MatchResult
│   ├── intent_terms.py      # 词表 + detect_scene（口语场景别名）
│   ├── action_verbs.py      # 动作动词 + VERB_TO_ACTION 映射
│   ├── value_resolver.py    # 数值抽取 / 单位推断 / 相对量 / 钳值+步长对齐
│   ├── normalizer.py        # 把/请/帮我 前缀剥离（不碰实体内部文本）
│   ├── chinese_number.py    # 中文数字 → 阿拉伯（两=2、二十三=23）
│   └── capability.py        # 设备能力校验（单位/范围）+ 薄 CommandIR
├── mijia_api/           # 自包含的米家 API 客户端（分层：domain/infrastructure/
│                        #   services/repositories，同步 + 异步双实现）
├── i18n/                # 8 语言（en/ja/ko/zh-CN/zh-TW/ru/es/pt）
├── static/              # Hosted UI 控制面板
├── docs/                # 快速开始（8 语言）
├── tests/               # 44 个单元测试（能力层/路由/匹配/数值）
├── .github/workflows/   # verify.yml + release.yml（官方插件仓库工作流）
└── pyproject.toml
```

## 快速开始

1. 把本仓库克隆/解压后，放到 N.E.K.O 的插件目录（`plugin/plugins/mijia/`）。
2. 在插件 UI 扫码登录米家账号（凭据存 `data/credential.json`）。
3. 让 AI 伙伴用自然语言控制，例如：

```text
用户：打开卧室灯，把空调调到26度
用户：加湿器切换到睡眠模式
用户：执行回家模式
```

## 入口（plugin_entry）

| 入口 ID | 说明 |
|---------|------|
| `smart_control` | **核心**：自然语言统一控制（开关/属性/模式/动作/场景） |
| `query_device_state` | 按名称查询设备所有可读属性 |
| `list_devices` / `get_cached_devices` | 设备列表（带缓存 + user_id 归属校验） |
| `list_homes` / `list_scenes` | 家庭列表 / 智能场景列表 |
| `set_device_alias` / `get_device_aliases` | 设置 / 读取设备别名 |
| `start_qrcode_login` / `check_login_status` / `logout` | 扫码登录 / 轮询状态 / 登出 |
| `reload_credential` | 重新加载凭据（前端刷新状态前调用） |
| `open_ui` | 打开配置页面 |

## 测试

```bash
python -m pytest -q          # 依赖 pytest + pytest-asyncio（见 pyproject.toml dev 组）
uv run neko-plugin check .   # 插件契约 + 仓库支撑文件校验（在 N.E.K.O 本体目录运行）
```

覆盖：路由五步短路、设备匹配优先级（含房间+部分别名）、数值解析与钳值、模式词前缀分界、能力层单位/范围校验等，共 145 个用例（含 100 条端到端 NLP 路由用例）。

## 已知限制

- 部分第三方网关（如 `api.mijia.tech`）的 `/miotspec/action` 动作端点不可用（全线 `-6`），扫地机 start/pause 等**动作类**控制会回落失败；属性通道（`/miotspec/prop/set`）正常。
- 电视类设备（`xiaomi.tv.rmh1` 等）云端无电源属性，`Turn On` 仅在 BLE 服务——云端开机可能不可用，`Turn Off` 依赖动作端点。
- 只写（access="write"）开关属性读不回来，无法回读确认，会返回"已发送但无法确认"。
- 暂不支持多指令（一条命令控制多设备）；设备名以动作动词/模式词开头等长尾句式仍在持续完善。

## 更新日志

### v2.0.1 (NLP 查询增强)
- **[FIX]** 开关/控制分支剥离尾部询问词后缀（"关闭卧室灯怎么样"→设备名"卧室灯"），修复 device_hint 被询问词污染。
- **[FEAT]** 查询分支新增 `query_prop` 字段：从设备名尾部属性词（"卧室空调温度多少度"→温度）与查询关键词（"多少度/多亮/多冷"）推断具体查询属性，便于服务端直接返回对应属性值。

### v2.0.0 (NLP 引擎重构 + 能力校验)
- **[REFACTOR]** 2356 行 `__init__.py` 拆出 `nlp/` 子包，`smart_control` 改为路由分发 + 执行分支。
- **[FEAT]** 新增 Normalizer（把/请/帮我前缀剥离）、ChineseNumberParser（中文数字）、Capability 层（单位/范围校验，如"亮度调到三档"→INVALID_UNIT）。
- **[FEAT]** 开关执行三级回落：电源动作 → 开关属性（bool 过滤）+ 写后回读校验，杜绝"属性合法但非电源"的假成功。
- **[FEAT]** 场景口语别名（我回家了/晚安/打开回家模式）、句末开关动词、设备名尾部属性词剥离。
- **[FIX]** spec 解析器保留 value_list 描述；`validate_value` 兼容 dict 枚举；动作调用校验 per-item 码；设备缓存 schema 版本化 + user_id 归属校验。

### v1.1.0
- 多设备消歧、区域+设备名解析、匹配优先级重构。

### v1.0.0
- 基础设备控制、场景执行、扫码登录。

## 反馈

- **仓库**: https://github.com/wangjunyu200708/n.e.k.o_plugin_mijia
- 遇到无法识别的方言指令欢迎提交 Issue / PR。
