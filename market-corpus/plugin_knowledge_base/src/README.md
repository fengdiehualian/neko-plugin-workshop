# plugin_knowledge_base

N.E.K.O 知识库插件（应用层）。文件导入 → 分块 → 向量化 → 索引存储 → 语义搜索
→ 滑动窗口热度追踪 → 触发提升。

## 核心流程

```
kb_import                 kb_search                    plugin_database:hot_promote
┌─────────┐  ┌──────────────────────┐  ┌──────────────────────────────┐
│ .md/.txt│→ │ 分块 512/64 → 嵌入   │→ │ 窗口内命中 ≥ threshold 时     │
│ /.pdf   │  │ (all-MiniLM-L6-v2    │  │ INSERT 到 hot_knowledge，    │
│ 扫描+哈希│  │  ONNX 384 维 CPU)    │  │ 并清空窗口避免重复提升        │
└─────────┘  │  vec0 + FTS5 RRF 融合│  └──────────────────────────────┘
             └──────────────────────┘
```

## 入口（跨插件调用）

| 入口 | 说明 |
|------|------|
| `kb_import` | 扫描目录（默认 `<插件数据>/knowledge/`）或单文件，导入 .md/.txt/.pdf |
| `kb_search` | 向量 + BM25 混合检索 Top-K；命中计入滑动窗口热度 |
| `kb_hot_list` / `kb_hot_export` | 热知识查询 / 导出为 Markdown 或 JSON |
| `kb_stats` / `kb_demote_sweep` | 索引统计 / 立即执行降级扫描 |
| `kb_model_status` / `kb_model_download` | 检查嵌入模型 / 从镜像下载（默认 hf-mirror.com） |
| `kb_record_skill_stat` / `kb_query_skill_stats` | Skill 调用统计转发（供 skill 插件） |

## 依赖关系

- **plugin_database**（必须启用）：索引表、向量检索、热数据表全部经其入口读写；
  启动时由本插件调用 `register_hot_schema` 注册热数据表（任务书约定）
- 索引要求 sqlite 驱动（vec0 / FTS5 是 sqlite 能力）；vec0 缺失自动退化为
  numpy 余弦扫描，FTS5 缺失时跳过 BM25 通道（能力探测见 `db_capabilities`）

## 测试

```bash
python -m pytest tests -q
python tests/stub_run.py      # 桩链路检查（12 项）
```

> 测试需要 **`plugin_database` 作为同级目录**（`../plugin_database`）：
> `tests/conftest.py` 会跨目录加载它的 `adapters/` 与 `hot_schema.py`。
> 因此单独克隆本仓库跑测试会报
> `ModuleNotFoundError: No module named 'aiosqlite'` —— 那是
> `plugin_database/vendor/` 未补齐的连带结果。把两个仓库克隆到同一父目录、
> 并按 plugin_database 的 README 补齐其 `vendor/` 即可：
>
> ```bash
> git clone https://github.com/addsas222/plugin_database
> git clone https://github.com/addsas222/plugin_knowledge_base
> ```

## 模型

all-MiniLM-L6-v2（384 维）ONNX 导出，推理用宿主自带的 onnxruntime + tokenizers，
无需 torch。模型目录解析顺序：

1. `config.knowledge.models_dir`（显式配置）
2. `<插件目录>/models/all-MiniLM-L6-v2`（随插件分发）
3. `<插件数据>/models/all-MiniLM-L6-v2`（首次运行 `kb_model_download` 下载，约 90MB）

> **克隆本仓库后 `models/` 是空的**（`.gitignore` 排除，`model.onnx` 单个文件就有 86 MB）。
> 三种补齐方式，任选其一：
>
> 1. **让插件自己下**（推荐）：宿主里跑 `kb_model_download`，从 hf-mirror.com 拉到
>    插件数据目录（第 3 条路径），不动仓库工作区。
> 2. 手动放到 `<插件目录>/models/all-MiniLM-L6-v2/`（第 2 条路径），
>    需含 `model.onnx` / `tokenizer.json` / `config.json` / `special_tokens_map.json` /
>    `tokenizer_config.json` 这 5 个文件。
> 3. 在 `config.knowledge.models_dir` 指向你已有的目录。
>
> `kb_model_status` 会报当前解析到的实际路径，用来确认拿到的是哪一份。

## 分块与检索约束

- **块大小默认 240 tokens（重叠 64）**，不是任务书原值的 512：MiniLM 的推理
  窗口是 256，512 的块尾会被截断、丢失向量覆盖。计数用不截断的独立 tokenizer，
  超长段落按滑窗切块。可用 `[knowledge] chunk_tokens / overlap_tokens` 调整。
- **BM25 用 trigram 分词**：unicode61 把连续汉字当一个 token，中文 MATCH 永远
  不命中。trigram 要求查询 ≥3 字；更短的查询只剩向量通道（能力探测里
  `fts5` 为 false 时同样只有向量通道）。
- **嵌入模型向量空间守卫**：索引元数据记录 `(embedder_model, embedder_dim)`，
  换模型/维度不一致时报结构化错误，必须 `kb_rebuild` 重建后重新导入；
  `hot_knowledge.embedder` 记录每条热知识的来源模型。不同模型的向量绝不混算。

## 滑动窗口热度（config）

```toml
[hot_promotion]
window_days = 7      # 窗口天数：过期命中时间戳被剔除
threshold = 5        # 窗口内命中次数阈值，达到即提升
demotion_days = 30   # hot_knowledge.last_hit_at 超过该天数标记 demoted
```

## 测试

```bash
uv run pytest plugin/plugins/plugin_knowledge_base/tests -q   # 引擎级（真实 sqlite+vec0+ONNX）
python plugin/plugins/plugin_knowledge_base/tests/stub_run.py # 桩 SDK 全链路（KB -> DB）
```

任务书验收链路（导入 3 个 .md → 5 次检索 → hot_knowledge 出现记录）在两套测试中
都有覆盖。
