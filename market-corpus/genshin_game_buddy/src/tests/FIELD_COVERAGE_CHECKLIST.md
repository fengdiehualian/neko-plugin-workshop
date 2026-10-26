# genshin_game_buddy · 字段覆盖测试清单（L1 MVP）

> 状态：**实现已落地并通过**（设计稿仍是本文件；逐条对照见下方"实现状态与偏差"）
> 对应实现：`game_registry.py` / `term_store.py` / `session.py` / `context_pack.py` / `__init__.py`
> 运行：`cd plugin/plugins/genshin_game_buddy && python -m pytest tests -q`

---

## 实现状态与偏差（读本清单前先看这里）

| 项 | 落地情况 |
|---|---|
| A 节配置键（33 个） | 全部被生产代码读取；`tests/unit/test_game_registry.py` 与 `tests/integration/test_plugin_entries.py` 逐键断言"改了行为就变" |
| B 节维度与目录一致 | 直跑随包分发的真实 `terms/` 树；含"反向一致"（无孤儿目录）与"未知维度文件被忽略" |
| C 节字段与两层合并 | 覆盖层按 key **逐字段**合并；`notes` 刻意不读（保证不泄露）；`source` 只进查询返回体 |
| D 节验收与 push 契约 | 全部落地；`push_message` 的 kwargs 被断言为**精确等于** `{source, visibility, ai_behavior, parts, priority, metadata}` |
| E 节红线（22 条） | 全部落地为 `tests/static/test_redlines.py`（参数化）。扫描对象是**去掉注释与 docstring 的 AST**——否则"本插件不涉及 extension / [plugin.host]"这类说明文字会把红线自己点红 |
| I 节 i18n（18 条） | 全部落地；45 个键双向一致，`tr(default=)` 与 JSON 逐字相同 |
| F 节 marker | 已写入本插件 `pyproject.toml [tool.pytest.ini_options]`（含 `asyncio_mode = "auto"`，从插件目录直接跑即可） |

**刻意偏离本清单的三处（都是有据的修正）**

1. **`{key}` 占位符改为 `{term}`**。`PluginI18n.t(self, key, *, locale, default, **params)` 的第一个形参
   就叫 `key`，因此任何 `{key}` 占位符都拿不到实参、永远渲染成字面量（实测 TypeError）。
   `context.term_line` 与 `lookup.entry_line` 已改为 `{term}`，并有专项用例
   `test_i18n__no_reserved_placeholder_names` 守住这类坑。
2. **I-14 的期望与 SDK 现实不符**。`locale_candidates` 的顺序是 请求 locale → 其主语言 →
   `default_locale` → 其主语言 → `"en"`；本插件声明 `default_locale = "zh-CN"`，
   所以未知 locale **会**落到中文默认串。这是 SDK 契约（默认语言由插件自己声明），
   不是泄露。用例改为断言"落到声明的默认语言；完全没有 bundle 时落到 `default=` / 键名"。
3. **A-28 重推机制的计数源不是 `bus`**。E-22 要求完全不读 `self.bus`，而 `bus.messages`
   记录的是插件→宿主的推送、并非用户轮次。因此 P1 用
   `@message(id="on_chat_message", source="chat")` 计数（正是 A-28 所写的
   "读它的模块：`__init__.py`（P1 `@message`）"）。宿主若不派发该消息，行为是
   "永不重推"（fail-safe），不会刷屏。

**未决风险（无法由代码解决）**

- `terms/**.toml` 里仍有 `TODO(verify)`：术语内容需由熟悉该游戏的人校对（G-06 是盘点命令，不是用例）。
- `[plugin.author].name` 仍是 `TODO`，发布前必填（`test_manifest__author_is_filled_at_release` 已 xfail 待转绿）。
- git remote 未配置 —— `check` 的第 1 条 warning。

---

## 0. 使用方式与核心原则

### 0.1 唯一不许违反的原则

> **每一个配置键的测试，必须证明「改了它，行为就变」，而不是「它存在于文件里」。**

这条直接针对 sts2 的实际事故：它的 `plugin.toml` 声明了 30 个 `[sts2]` 键，其中 **14 个在生产代码里零命中**（`neko_auto_low_hp_threshold`、`neko_desperate_enabled`、`neko_maximize_enabled`、`neko_guidance_max_queue` 等只出现在 `tests/live_entry_smoke.py`），`catgirl_llm_min_interval_seconds` 更是全仓库零命中。声明了却没人读的配置，会让使用者以为功能存在。

**禁止的写法**（反例）：

- `test_manifest_has_key_default_game` —— 只断言 TOML 里有这个键 ❌
- 断言 `plugin.toml` 文本包含某个字符串 ❌

**要求的写法**（正例）：

- `test_default_game__empty_means_no_fallback` —— 把值改成 `"genshin"`，断言未声明时的当前游戏变成 `genshin` ✅
- `test_max_context_chars__shrinking_truncates_block` —— 把 `800` 改成 `100`，断言语境块长度随之下降 ✅

### 0.2 测试用例命名约定

```
test_<被测模块>__<键或主题>__<被断言的行为>
```

例：`test_session__auto_restore_last_game__false_ignores_stored_value`

### 0.3 产出目标

| 指标 | 目标 |
|---|---|
| `plugin.toml` 配置键总数 | **33 个**（见 A 节逐键清单） |
| 术语维度文件 | **8 个**（2 游戏 × 4 维度） |
| 术语条目字段 | **6 个** + `[game]` 表 2 个 |
| i18n 文案键 | **45 个**（见 I 节） |
| 验收标准用例 | **10 条**（见 D 节） |
| 红线回归用例 | **22 条**（E 节：18 条源码扫描 + 4 条隐私行为） |
| 隐私专项用例 | **6 条**（E-19~E-22 与 I-16~I-17，CI 必跑不可跳） |

---

## A. `plugin.toml` 配置键逐键覆盖（33 个键）

> 「读它的模块」一栏写的是**预期的**读取点；实现阶段若发现某键无人读，就是缺陷，不是测试问题。

### A-1 `[plugin]`（8 个键）

| # | 键 | 类型/约束 | 读它的模块 | 测试用例名 | 断言要点 |
|---|---|---|---|---|---|
| A-01 | `id` | `^[a-z][a-z0-9_]*$`，须等于目录名 | 平台（非本插件代码） | `test_manifest__id__matches_directory_and_lowercase_regex` | `id == "genshin_game_buddy"` 且等于父目录名；额外断言它同时满足 `init` 的 `^[a-z][a-z0-9_]*$` 与 schema 的 `^[A-Za-z0-9_-]+$`（KB C6 两套正则都要过） |
| A-02 | `name` | 非空字符串 | 平台 | `test_manifest__name__non_empty` | 非空、无前后空白 |
| A-03 | `type` | 只能 `plugin` / `adapter` | 平台 | `test_manifest__type__is_plugin_not_extension` | 值为 `"plugin"`；显式断言**不是** `"extension"`（KB B1） |
| A-04 | `description` | 非空字符串 | 平台 Agent 细筛 | `test_manifest__description__non_empty_and_mentions_scope` | 非空；且不宣称"检测游戏/自动操作"（防止描述与实际能力不符） |
| A-05 | `short_description` | 非空字符串 | 平台 Agent 粗筛 | `test_manifest__short_description__non_empty` | 非空 |
| A-06 | `keywords` | 非空字符串列表 | 平台 Agent Stage 1 | `test_manifest__keywords__non_empty_strings_and_valid_regex` | 每个元素非空；每个元素 `re.compile()` 不抛异常（KB：keywords 是正则片段） |
| A-07 | `version` | `^\d+\.\d+\.\d+.*$` | 平台 / 发布流程 | `test_manifest__version__semver_shaped` | 匹配 `^\d+\.\d+\.\d+`；且与 `pyproject.toml` 的 `project.version` 一致（防 sts2 式版本漂移） |
| A-08 | `entry` | `^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$` | 平台加载器 | `test_manifest__entry__resolves_to_decorated_neko_plugin_base` | 格式正确；导入该模块、取到该类；断言类带 `@neko_plugin`（`__neko_plugin__`）且是 `NekoPluginBase` 子类（KB B6） |

### A-2 `[plugin.author]`（1 个键）

| # | 键 | 类型/约束 | 读它的模块 | 测试用例名 | 断言要点 |
|---|---|---|---|---|---|
| A-09 | `name` | 非空字符串 | 平台（显示） | `test_manifest__author_name__non_empty_and_not_todo` | 非空，且**不是占位符 `"TODO"`**——发布前必须填（当前是 TODO，此用例应标记 `xfail` 或 `@pytest.mark.release`，`check --release` 时转必过） |

### A-3 `[plugin.sdk]`（2 个键）

| # | 键 | 类型/约束 | 读它的模块 | 测试用例名 | 断言要点 |
|---|---|---|---|---|---|
| A-10 | `recommended` | PEP 440 specifier | 平台加载器 | `test_manifest__sdk_recommended__valid_and_subset_of_supported` | 可被 `packaging.specifiers.SpecifierSet` 解析；语义上落在 `supported` 之内（**不是**"必须相等"，KB 语义是 recommended ⊆ supported） |
| A-11 | `supported` | PEP 440 specifier | 平台加载器 | `test_manifest__sdk_supported__valid_specifier` | 可解析；且当前 SDK 版本 `SDK_VERSION` 落在该区间内 |

### A-4 `[plugin.i18n]`（2 个键）

| # | 键 | 类型/约束 | 读它的模块 | 测试用例名 | 断言要点 |
|---|---|---|---|---|---|
| A-12 | `default_locale` | 非空字符串 | `__init__.py`（i18n 装载） | `test_i18n__default_locale__file_exists` | `i18n/<default_locale>.json` 存在；且 `self.i18n.t()` 对任一 key 返回非 key 本身 |
| A-13 | `locales_dir` | 非空字符串，目录须存在 | `__init__.py` | `test_i18n__locales_dir__missing_dir_degrades_loudly` | 把值改成不存在的目录 → 断言插件仍能加载（不崩）**且** `logger.warning` 被调用（KB：`[plugin.i18n].locales_dir` 不存在是 warning） |

### A-5 `[plugin.store]`（1 个键）

| # | 键 | 类型/约束 | 读它的模块 | 测试用例名 | 断言要点 |
|---|---|---|---|---|---|
| A-14 | `enabled` | 布尔 | `session.py`（持久化） | `test_session__store_disabled__write_returns_error_not_silent_success` | 改成 `false` → 断言 `set_current_game` 仍返回 `Ok` 但带 `persisted: false`，**且不谎报成功**（KB：不能对模型谎报已完成） |

### A-6 `[plugin_runtime]`（2 个键）

| # | 键 | 类型/约束 | 读它的模块 | 测试用例名 | 断言要点 |
|---|---|---|---|---|---|
| A-15 | `enabled` | 布尔 | 平台 | `test_manifest__runtime_enabled__is_true` | 值为 `true`（插件默认可用） |
| A-16 | `auto_start` | 布尔 | 平台 | `test_manifest__auto_start__is_false_and_module_import_is_side_effect_free` | 值为 `false`；**并且**断言 `import` 该模块不产生副作用（不建连接、不读大文件、不写盘）——KB 明确 `auto_start=false` 不阻止父进程 import（KB R8） |

### A-7 `[plugin.ui]` + `[[plugin.ui.guide]]`（5 个键）

| # | 键 | 类型/约束 | 读它的模块 | 测试用例名 | 断言要点 |
|---|---|---|---|---|---|
| A-17 | `plugin.ui.enabled` | 布尔 | 平台 UI 层 | `test_manifest__ui_enabled__true_when_surfaces_declared` | 声明了 `[[plugin.ui.guide]]` 时须为 `true`，否则 surface 不可用 |
| A-18 | `guide.id` | 非空字符串，同类内唯一 | 平台 UI 层 | `test_manifest__ui_guide__id_unique` | 非空；同类 surface 内不重复 |
| A-19 | `guide.title` | 非空字符串 | 平台 UI 层 | `test_manifest__ui_guide__title_non_empty` | 非空 |
| A-20 | `guide.entry` | 相对路径，文件须存在（`.md` → markdown 模式） | 平台 UI 层 | `test_manifest__ui_guide__entry_exists_and_is_markdown` | `plugin_dir / entry` 存在；后缀是 `.md`/`.mdx`；（KB：不存在只报 warning，所以必须由我们自己的测试兜住） |
| A-21 | `guide.permissions` | 字符串列表，元素在合法集内 | 平台 UI 层 | `test_manifest__ui_permissions__minimal_and_recognized` | 每个元素 ∈ `{state:read, config:read, config:write, action:call, document:parse, logs:read, runs:read}`；且**不包含**本插件用不到的 `config:*` / `action:call` / `document:parse`（KB W1/W5：权限必须最小） |

### A-8 `[genshin_game_buddy]`（7 个键）— 本插件真正的配置面

| # | 键 | 默认 | 读它的模块 | 测试用例名 | 断言要点 |
|---|---|---|---|---|---|
| A-22 | `default_game` | `""` | `session.py` | `test_session__default_game__empty_means_no_game` + `test_session__default_game__nonempty_is_used_when_unset` | 两个用例：`""` → 未声明时 `current_game is None`；改为 `"genshin"` → 未声明时 `current_game == "genshin"` |
| A-23 | `auto_restore_last_game` | `true` | `session.py`（startup） | `test_session__auto_restore__true_restores_and_false_ignores` | 预置 `store["current_game"]="genshin"`；`true` → startup 后当前游戏是原神；`false` → 仍是 `None`/`default_game` |
| A-24 | `context_inject_max_terms` | `5` | `context_pack.py` | `test_context_pack__max_terms__limits_term_lines` | 改为 `2` → 语境块里的术语行数 ≤ 2；改为 `0` → 无术语行但块仍生成（不崩） |
| A-25 | `max_context_chars` | `800` | `context_pack.py` | `test_context_pack__max_chars__hard_truncates` | 改为 `100` → `len(block) <= 100`；且断言截断是**在完整行边界**上做的（不切半个词） |
| A-26 | `lookup_max_matches` | `3` | `__init__.py`（工具） | `test_lookup__max_matches__caps_returned_entries` | 构造 5 条可命中的术语，`lookup_max_matches=3` → 返回 ≤ 3 条 |
| A-27 | `lookup_max_chars_per_term` | `200` | `context_pack.py` | `test_lookup__max_chars_per_term__truncates_each_entry` | 用一条超长 `brief` → 单条返回 ≤ 200 字符 |
| A-28 | `reinject_every_n_messages` | `0` | `__init__.py`（P1 `@message`） | `test_reinject__zero_is_off` + `test_reinject__n_repushes_after_n_messages` | 两个用例：`0` → 不注册重推逻辑/永不重推；`3` → 第 3 条用户消息后触发一次重推（P1 阶段实现，MVP 可标 `skip`） |

### A-9 `[games.<game_id>]`（5 个键，按每个注册游戏各测一遍）

| # | 键 | 必填 | 读它的模块 | 测试用例名 | 断言要点 |
|---|---|---|---|---|---|
| A-29 | `display_name` | 是 | `game_registry.py` | `test_registry__display_name__required_and_used_in_block` | 缺失 → 该游戏被判非法并记 warning（不崩）；存在 → 出现在语境块的标题行 |
| A-30 | `aliases` | 是 | `game_registry.py` | `test_registry__aliases__fuzzy_match_all_forms` | 对 `aliases` 每一项都能归一化到同一 `game_id`（含大小写差异，如 `"Genshin"` / `"genshin"`） |
| A-31 | `terms_dir` | 否 | `term_store.py` | `test_term_store__terms_dir__override_and_default` | 缺省 → 用 `terms/<game_id>`；显式改成别的目录 → 从该目录读；指向不存在的目录 → 加载失败但**不影响其他游戏** |
| A-32 | `enabled` | 否 | `game_registry.py` | `test_registry__enabled_false__excluded_from_list_and_declaration` | `false` → `list_games` 不含它；`set_current_game` 对它返回 `Err` |
| A-33 | `context_terms` | 否 | `context_pack.py` | `test_context_pack__context_terms__overrides_auto_selection` | 缺省 → 按 `context_inject_max_terms` 自动选取；显式给出 → **严格按给定 key 注入且忽略条数上限**；含不存在的 key → 跳过并记 warning |

---

## B. 术语库维度与文件级覆盖（8 个文件）

| # | 维度文件 | 必须存在 | 校验点 | 测试用例名 |
|---|---|---|---|---|
| B-01 | `terms/genshin/core.toml` | 是 | 含 `[game]` 表；`tone` 非空 | `test_terms__genshin_core__has_game_table_with_tone` |
| B-02 | `terms/genshin/characters.toml` | 是 | 只含 `[terms.*]`，**不得含 `[game]`** | `test_terms__genshin_characters__no_game_table` |
| B-03 | `terms/genshin/systems.toml` | 是 | 只含 `[terms.*]` | `test_terms__genshin_systems__no_game_table` |
| B-04 | `terms/genshin/slang.toml` | 是 | 只含 `[terms.*]` | `test_terms__genshin_slang__no_game_table` |
| B-05 | `terms/wuthering_waves/core.toml` | 是 | 含 `[game]` 表；`tone` 非空 | `test_terms__wuwa_core__has_game_table_with_tone` |
| B-06 | `terms/wuthering_waves/characters.toml` | 是 | 只含 `[terms.*]` | `test_terms__wuwa_characters__no_game_table` |
| B-07 | `terms/wuthering_waves/systems.toml` | 是 | 只含 `[terms.*]` | `test_terms__wuwa_systems__no_game_table` |
| B-08 | `terms/wuthering_waves/slang.toml` | 是 | **允许零条 `[terms.*]`**（当前是空模板） | `test_terms__wuwa_slang__empty_terms_list_is_valid` —— 加载该维度不得抛异常、不得使整个游戏不可用 |

**跨文件一致性用例**：

| # | 主题 | 测试用例名 | 断言要点 |
|---|---|---|---|
| B-09 | 注册表 ↔ 目录一致 | `test_registry__every_game_has_terms_dir` | 每个 `[games.*]` 的 `terms_dir` 目录存在且含 4 个维度文件 |
| B-10 | 维度齐全 | `test_term_store__all_four_dimensions_loaded` | 加载原神后，四个维度名都出现在 `loaded_dimensions` 里 |
| B-11 | 目录 ↔ 注册表一致（反向） | `test_registry__no_orphan_terms_dir` | `terms/` 下每个子目录都有对应的 `[games.*]` 条目（防"写了术语没人能用"） |
| B-12 | 白名单维度 | `test_term_store__unknown_dimension_file_is_ignored` | 往 `terms/genshin/` 放一个 `extra.toml` → 不报错、不加载（维度白名单固定为 core/characters/systems/slang） |

---

## C. 术语条目与 `[game]` 字段覆盖

### C-1 术语条目字段（6 个）

| # | 字段 | 必填 | 约束 | 测试用例名 | 断言要点 |
|---|---|---|---|---|---|
| C-01 | `kind` | 是 | 非空字符串 | `test_term__missing_kind__entry_rejected_with_warning` | 缺失 → 该条被跳过并记 warning，其他条不受影响 |
| C-02 | `brief` | 是 | 非空，**≤80 字** | `test_term__brief__missing_rejected_and_too_long_warned` | 缺失 → 跳过；>80 字 → 记 warning（内容质量问题要早暴露） |
| C-03 | `aliases` | 否 | 字符串列表，元素非空 | `test_term__aliases__participate_in_lookup` | 用别名查询能命中；单个 alias 也能命中 |
| C-04 | `slang` | 否 | 字符串列表，元素非空 | `test_term__slang__appears_in_lookup_output` | 命中后返回体含 slang 项 |
| C-05 | `avoid` | 否 | 字符串 | `test_term__avoid__surfaced_in_lookup_output` | 命中后返回体含 avoid 项（防猫娘说错术语的关键字段） |
| C-06 | `source` | 否 | 字符串 | `test_term__source__carried_but_not_injected` | 出现在查询返回体的可选字段中；**但不出现在自动注入的语境块里**（语境块要省字符） |

### C-2 `[game]` 表字段（2 个，仅 `core.toml`）

| # | 字段 | 必填 | 读它的模块 | 测试用例名 | 断言要点 |
|---|---|---|---|---|---|
| C-07 | `tone` | 是 | `context_pack.py` | `test_terms__game_tone__appears_in_context_block` | 改 `tone` 文本 → 语境块里对应文字随之变化（证明真被读到） |
| C-08 | `notes` | 否 | —（仅供维护者） | `test_terms__game_notes__never_injected` | 写入含独特标记的 `notes` → 断言语境块与查询返回体里**都不含**该标记 |

### C-3 合并与优先级（双层核心行为）

| # | 主题 | 测试用例名 | 断言要点 |
|---|---|---|---|
| C-09 | 覆盖层覆盖单条 | `test_merge__override_layer_replaces_default_term` | 默认层有术语 X；覆盖层只写 X 的 `brief` → 合并后 X 的 `brief` 来自覆盖层，其余字段继承默认层 |
| C-10 | 覆盖层新增 | `test_merge__override_layer_adds_new_term` | 覆盖层新增 Y → 合并后 X、Y 都在 |
| C-11 | 覆盖层不整体替换 | `test_merge__override_layer_does_not_wipe_defaults` | 覆盖层只有一个文件 → 其他三个维度仍来自默认层 |
| C-12 | 覆盖层坏文件 | `test_merge__malformed_override_is_skipped` | 覆盖层放一个语法错误的 TOML → 跳过并记 warning，默认层仍可用（**不崩**） |
| C-13 | 默认层坏文件 | `test_merge__malformed_default_skips_file_only` | 默认层某个维度坏掉 → 其余维度仍可查询 |
| C-14 | 路径必须走 data_path | `test_term_store__override_root_is_data_path` | `monkeypatch` `self.data_path()` → 覆盖层确实从该处读取（KB：不得自拼 `%LOCALAPPDATA%`） |

---

## D. 验收标准 → 测试用例映射（10 条）

| # | 验收标准（来自设计稿第十节） | 测试用例名 | 层级 |
|---|---|---|---|
| D-01 | `neko-plugin check --strict` 0 error | （由 `check` 直接验证，不进 pytest） | CI 命令 |
| D-02 | 说"我在玩原神"→ 登记成功 → 下一条消息用上原神术语 | `test_acceptance__declare_genshin_then_context_block_contains_genshin_terms` | 集成 |
| D-03 | 当前是原神时查"声骸"→ 返回未命中 + 正确的游戏提示 | `test_acceptance__lookup_cross_game_is_isolated` | 集成（**跨游戏隔离**） |
| D-04 | 切到鸣潮后：查"雷神"未命中、查"声骸"命中 | `test_acceptance__switch_game_unloads_old_and_loads_new` | 集成（**卸载/加载**） |
| D-05 | 重启插件后自动恢复上次游戏 | `test_acceptance__restart_restores_current_game` | 集成 |
| D-06 | 覆盖层只写一条 → 该条被覆盖、其余继承默认层 | `test_acceptance__partial_override_merges_correctly` | 集成（= C-09） |
| D-07 | 畸形 TOML → 跳过并记日志，其余维度可用 | `test_acceptance__malformed_file_degrades_gracefully` | 集成（= C-12/C-13） |
| D-08 | **每个配置键都有"改了行为就变"的断言** | A-01 ~ A-33 全部 | 单元 |
| D-09 | 语境块 ≤ `max_context_chars`；`submitted=False` 时诚实上报 | `test_acceptance__block_within_budget` + `test_acceptance__push_failure_reports_context_injected_false` | 单元 |
| D-10 | 全仓库 grep 无被禁字符串 | E 节全部 | 静态 |

**补充：`push_message` 相关必测行为**

| # | 主题 | 测试用例名 | 断言要点 |
|---|---|---|---|
| D-11 | 只用 v2 三件套 | `test_push__only_parts_visibility_ai_behavior` | 用 spy 捕获 `push_message` 的 kwargs，断言 key 集合 ⊆ `{source, visibility, ai_behavior, parts, priority, metadata, coalesce_key}` |
| D-12 | 注入必须用 `ai_behavior="read"` | `test_push__injection_uses_read_not_respond` | 断言**不是** `"respond"`——`respond` 会立即起一个回复 turn，造成自我触发风险 |
| D-13 | 注入不可见 | `test_push__injection_visibility_is_empty` | `visibility == []`（用户不应看到原始语境块） |
| D-14 | 失败不谎报 | `test_push__submitted_false_sets_context_injected_false` | 桩返回 `{"submitted": False, "reason": "backpressure"}` → entry 返回体 `context_injected is False`，且不抛异常 |

---

## E. 红线回归测试（防踩 KB 第三部分 / 第六部分）

> 形式：对源码做静态扫描。建议实现为**一个参数化用例**，把下表的 pattern 作为参数。

| # | 禁止出现 | 对应 KB 红线 | 测试用例名 |
|---|---|---|---|
| E-01 | `plugin.sdk.extension`、`NekoExtensionBase`、`extension_entry`、`type = "extension"`、`[plugin.host]` | **B1 / C1** | `test_redline__no_removed_extension_surface` |
| E-02 | `@hook`、`@before_entry`、`@after_entry`、`@around_entry`、`@replace_entry` | **B2 / C2**（Hook 当前不执行） | `test_redline__no_hook_decorators` |
| E-03 | `@plugin_entry` 挂在以 `_` 开头的方法上 | **B3** | `test_redline__no_underscore_entry_methods`（AST 级：解析装饰器与 `def` 名） |
| E-04 | `@plugin_entry` / `@llm_tool` 挂在同步 `def` 上 | **B4** | `test_redline__all_entry_and_tool_handlers_are_async`（AST 级） |
| E-05 | `@message(..., auto_start=...)` | **B5** | `test_redline__no_auto_start_on_message_decorator` |
| E-06 | 同一 `@plugin_entry` 同时给 `input_schema` 与 `params`；或同时给多个 `llm_result_*` | **B7** | `test_redline__no_mutually_exclusive_decorator_args` |
| E-07 | `self.memory`、`MemoryClient`、`ctx.query_memory` | **D1 / D2** | `test_redline__no_removed_memory_surface` |
| E-08 | `where_in`、`where_eq`、`where_contains`、`where_regex`、`where_gt`、`where_ge`、`where_lt`、`where_le`、`get_message_plane_all` | **D3 / D4** | `test_redline__no_removed_bus_helpers` |
| E-09 | `push_message` 出现 `message_type` / `content=` / `description=` / `delivery=` / `reply=` / `binary_data` / `binary_url` / `mime=` / `unsafe` / `fast_mode` | **D5**（sts2 唯一真正的 push 踩线点） | `test_redline__push_message_v2_only` |
| E-10 | `plugin._types.result`、`type = "script"`、`requirements*.txt` | **D6 / D7** | `test_redline__no_removed_result_module_or_requirements_txt` |
| E-11 | `finish(` 且带 `reply=` | **D9 / C7** | `test_redline__finish_uses_delivery_not_reply` |
| E-12 | `lifecycle(id="reload")` | **D10** | `test_redline__no_reload_lifecycle` |
| E-13 | `from plugin.sdk.shared`、`from plugin.core`、`from plugin.server`、`from utils.`（宿主主程序包） | **R19 同类**（sts2 的 `catgirl_llm.py:22-23` 越界） | `test_redline__no_sdk_internal_or_host_utils_imports` |
| E-14 | `%LOCALAPPDATA%`、`APPDATA`、`Path.home()`、`NEKO_PLUGIN_DATA_DIR`（**该环境变量在 N.E.K.O. 全仓库不存在**） | **KB 新增坑 N2**（sts2 的 `catgirl_memory.py:32` 死分支） | `test_redline__no_hand_rolled_storage_paths` |
| E-15 | `[plugin.database]`、`[plugin_state]`、`[plugin.install]`、`[plugin.host]` | **W2 / W3 / W4 / C4 / C5 / C8** | `test_redline__no_unused_platform_sections` |
| E-16 | `document:parse` 出现在 UI permissions | **W1 / C3**（本插件不需要） | `test_redline__no_document_parse_permission` |
| E-17 | `httpx`、`requests`、`aiohttp`、`yaml`、`psutil` 等第三方 import，而 `pyproject.toml [project].dependencies` 为空且无 `vendor/` | **D7 相关**（sts2 导入 httpx+yaml 却未声明未 vendor） | `test_redline__no_undeclared_third_party_imports` |
| E-18 | 模块顶层出现 `open(`、`Path(` 写操作、`Thread(`、`create_task(`、`http` 调用 | **R8**（KB：`auto_start=false` 不阻止 import） | `test_redline__no_import_time_side_effects`（AST 级扫描顶层语句） |

**隐私专项用例（不是 grep，是行为断言）**

| # | 主题 | 测试用例名 | 断言要点 |
|---|---|---|---|
| E-19 | 查询原文不进日志 | `test_privacy__lookup_query_text_never_logged` | 桩 logger 记录所有调用；用带独特标记的 query 调用 `lookup_game_term` → 断言所有日志文本里**不含该标记**（KB：query 来自用户对话，属隐私内容） |
| E-20 | 只记长度 | `test_privacy__lookup_logs_length_only` | 断言存在一条含 `len(query)` 的数字、但不含 query 内容的日志 |
| E-21 | 游戏名可安全记录 | `test_privacy__game_id_is_loggable` | 反向确认：`game_id` 可以进日志（它不是用户隐私） |
| E-22 | 不读 bus 隐私面 | `test_privacy__no_bus_reads` | 断言 `self.bus` 完全未被访问（KB：conversations/frames 是全插件可读的敏感面，不读即无披露义务） |

---

## F. 建议的 pytest 布局与 marker

```
tests/
├── FIELD_COVERAGE_CHECKLIST.md      ← 本文件
├── test_smoke.py                    ← init 生成，保留并加强（见下）
├── conftest.py                      ← 共享 fixture：临时术语目录、桩 logger、桩 push_message
├── unit/
│   ├── test_manifest_fields.py      ← A 节（A-01 ~ A-21）
│   ├── test_config_keys.py          ← A-22 ~ A-33（"改了行为就变"）
│   ├── test_term_schema.py          ← C-01 ~ C-08
│   ├── test_term_merge.py           ← C-09 ~ C-14
│   ├── test_game_registry.py        ← A-29 ~ A-32、B-09、B-11
│   ├── test_context_pack.py         ← A-24、A-25、A-33、C-07、C-08、D-09
│   └── test_lookup_tool.py          ← A-26、A-27、D-11 ~ D-14、E-19 ~ E-21
├── integration/
│   ├── test_acceptance_l1.py        ← D-02 ~ D-07
│   └── test_switch_game.py          ← 需求 ④ 的端到端（卸载→加载→旧术语失效）
└── static/
    └── test_redlines.py             ← E-01 ~ E-22（参数化）
```

**建议 marker**（写进 `pyproject.toml [tool.pytest.ini_options]` 或 `pytest.ini`，**不要**写进 `requirements.txt`）：

| marker | 用途 |
|---|---|
| `unit` | 纯函数级 |
| `integration` | 需要构造临时术语树 |
| `static` | 源码扫描 |
| `privacy` | 隐私专项，CI 必跑不可跳 |
| `release` | 发布前必过（如 A-09 作者名非 TODO） |
| `p1` | P1 功能（重推），MVP 阶段默认 skip |

**`test_smoke.py` 加强建议**（init 生成的版本只断言 manifest 存在，太弱）：

保留 `test_plugin_manifest_exists`，并新增 `test_entry_class_importable` —— 断言 `entry` 能真正导入到类（提前暴露拼写错误，这是 init 版漏掉的）。
**注意**：不要改用 `Get-Content` 之类的读取方式来解析 LF-only 文件——本机 `Get-Content` 对 LF-only UTF-8 文件会少算 2 行并把中文解成 ANSI（sts2 审计中已实测的坑）。

---

## G. 跑测试前的前置清单

| # | 检查项 | 命令 / 期望 |
|---|---|---|
| G-01 | 目录名与 `id` 一致 | 目录 `genshin_game_buddy` == `[plugin].id` |
| G-02 | `check` 基本门 | `uv run neko-plugin check genshin_game_buddy` → 0 error |
| G-03 | `check` 严格门 | `uv run neko-plugin check genshin_game_buddy --strict` → 0 error（`--strict` 会把缺失的 README.md / tests/test_smoke.py / .vscode/* / verify.yml / .gitignore 升级为 error，这些由 `init` 生成，勿删） |
| G-04 | 自有测试 | `cd plugin/plugins/genshin_game_buddy && uv run python -m pytest tests -q` |
| G-05 | 隐私专项单独跑 | `uv run python -m pytest tests -q -m privacy` |
| G-06 | 术语内容待办盘点 | `grep -rn "TODO(verify)" terms/ | wc -l` —— 上线前该数字应降到 0 或转为 issue |
| G-07 | 死配置自查 | 对 A-22 ~ A-33 的每个键，确认 `grep -rn "<key>" *.py` 有生产代码命中（**不是**只命中 tests/） |

---

## H. 交付到实现阶段的"开工条件"

进入 `term_store.py` / `session.py` / `context_pack.py` / `__init__.py` 之前，以下必须已经成立：

1. G-01 ~ G-03 全绿（脚手架与清单合法）；
2. 本清单的 A/B/C 三节已被逐行确认"字段无遗漏"；
3. E 节 22 条红线用例已写成骨架（可以先 fail，用来驱动实现）；
4. 术语内容的 `TODO(verify)` 已指派到具体的人（**这是唯一无法由代码解决的风险**）。

---

## I. i18n 键覆盖（45 个键）

> 本节产生于第 (a) 步补件（新增 `i18n/zh-CN.json`）之后。核心原则与 0.1 节一致：
> **JSON 里不许有代码未引用的键，代码里引用的键不许缺。**

### I-1 双向一致性（防死键 / 防缺键）

| # | 主题 | 测试用例名 | 断言要点 |
|---|---|---|---|
| I-01 | 无死键 | `test_i18n__no_dead_keys` | 收集所有 `tr("K")` 与 `self.i18n.t("K")` 的字面量第一参数构成集合 R；断言 `set(zh-CN.json) - R == 空`（**直接针对 sts2 那 14 个"声明了没人读"的同类问题**） |
| I-02 | 无缺键 | `test_i18n__no_missing_keys` | 断言 `R - set(zh-CN.json) == 空` |
| I-03 | 无动态键 | `test_i18n__key_arguments_are_literals` | 断言所有 `t(...)` 的第一参数是字面量字符串，不是变量或拼接——否则 I-01/I-02 不可靠 |

> **桩阶段的预期红区（重要）**：在业务代码落地前，`__init__.py` 只引用 **13 个**键（4 个 entry 的 `.name`/`.description` = 8，`set_game` 的 `.param.game` = 1，2 个 tool 的 `.description` = 2，2 个 tool 的参数描述 = 2）。因此 I-01 会报 **32 个死键**：`errors.*` 8 + `context.*` 7 + `lookup.*` 6 + `status.*` 8 + `terms.*` 3。这 32 个键由业务阶段引用，**属预期而非缺陷**——I-01 在业务阶段完成前应标记 `@pytest.mark.xfail(strict=False)`，不要为了让 I-01 变绿而删掉这些键。

### I-2 占位符一致性

| # | 主题 | 测试用例名 | 断言要点 |
|---|---|---|---|
| I-04 | 占位符与实参同名 | `test_i18n__placeholders_match_call_kwargs` | 对每个调用点，断言值里的 `{name}` 集合 == 调用时传入的 kwarg 名集合（多一个少一个都 fail） |
| I-05 | 渲染无残渣 | `test_i18n__rendered_values_have_no_braces` | 用假值渲染每个键，断言结果不含 `{` 或 `}` |
| I-06 | 渲染非空 | `test_i18n__rendered_values_non_empty` | 每个键渲染后非空且无前后空白 |

### I-3 命名空间与内容完整性

| # | 主题 | 测试用例名 | 断言要点 |
|---|---|---|---|
| I-07 | 命名空间白名单 | `test_i18n__namespace_whitelist` | 所有键的顶级前缀 ∈ `{entries, tools, errors, context, lookup, status, terms}` |
| I-08 | 键必须扁平 | `test_i18n__keys_are_flat_dotted` | 每个键匹配 `^[a-z_]+(\.[a-z_]+)+$`；**值不得是嵌套对象**（插件 i18n 是扁平 dotted 键） |
| I-09 | 语境块模板齐备 | `test_i18n__context_keys_complete` | `context.title` / `.title_switched` / `.tone` / `.terms_header` / `.term_line` / `.no_terms` / `.hint` 共 7 个全部存在且非空 |
| I-10 | 查询输出模板齐备 | `test_i18n__lookup_keys_complete` | `lookup.found_header` / `.entry_line` / `.slang_label` / `.avoid_label` / `.not_found` / `.no_game` 共 6 个齐备 |
| I-11 | 错误文案齐备 | `test_i18n__error_keys_complete` | `errors.*` 8 个键齐备 |
| I-12 | 状态文案齐备 | `test_i18n__status_keys_complete` | `status.*` 8 个键齐备 |
| I-13 | 入口与工具文案齐备 | `test_i18n__entry_and_tool_keys_complete` | 4 个 entry 各有 `.name` + `.description`（8 键），其中 `set_game` 额外有 `.param.game`（1 键）→ entries 共 9 键；2 个 tool 各有 `.description` + 1 个参数描述 → tools 共 4 键 |
| I-14 | 非中文 locale 不串中文 | `test_i18n__missing_locale_falls_back_to_key_or_default` | 用不存在的 locale 取任一键 → 返回 `default=` 实参或键名，**不返回中文文案**（KB：非中文 locale 默认不泄露中文） |
| I-15 | 无开发标记残留 | `test_i18n__no_placeholder_like_todo` | 断言任何值都不含 `TODO` / `FIXME` / `待补`（文案不该带开发标记） |

### I-4 隐私专项（与 E-19~E-22 呼应）

| # | 主题 | 测试用例名 | 断言要点 |
|---|---|---|---|
| I-16 | 用户原文不进任何生成串 | `test_privacy__user_text_never_appears_in_rendered_output` | 用带独特标记的 query / 游戏名走一遍 `set_game` 与 `lookup_game_term`，断言**所有返回体**与**所有日志**都不含该标记。`lookup.not_found` 刻意不回显 query，此用例守住这个设计 |
| I-17 | 路径不进模型可见文案 | `test_privacy__no_absolute_paths_in_model_visible_strings` | 断言所有渲染结果不含盘符（`C:\`）、`/Users/`、`/home/`、用户名片段。`errors.terms_dir_missing` 刻意只给维度名不给路径，此用例守住这个设计 |

| I-18 | `default=` 与 JSON 不漂移 | `test_i18n__default_args_match_zh_cn_values` | 对每个 `tr("K", default="V")` 调用点，断言 `V` 与 `zh-CN.json["K"]` **逐字相同**。理由：`default=` 是 locale 文件缺失/损坏时的兜底文案，与 JSON 是两份副本（12 处），必须防漂移 |

**计数校核**：I 节共 **18 条**（I-01~I-18），覆盖 **45 个** i18n 键。
