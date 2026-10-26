# 泰拉瑞亚猫娘 - LLM 配置指南

## 双 LLM 架构说明

插件支持双 LLM 架构，分别处理不同任务：

### 主 LLM（对话交互）
- **职责**：对话、情感表达、角色一致性、任务汇报
- **推荐模型**：GPT-4、Claude Opus/Sonnet、Gemini Pro
- **调用频率**：5-10次/分钟

### 意图 LLM（任务推理）
- **职责**：指令解析、任务规划、结构化推理
- **推荐模型**：GPT-4o-mini、Claude Haiku、Gemini Flash
- **调用频率**：10-15次/分钟
- **优势**：速度快（0.5-1秒）、成本低（省70%）

---

## 支持的 Provider

### 1. OpenAI
```
Provider: openai
Model: gpt-4, gpt-4o-mini, gpt-3.5-turbo
API Key: sk-...
Base URL: （留空使用官方 API，或填写代理地址）
```

### 2. Anthropic (Claude)
```
Provider: anthropic
Model: claude-3-5-sonnet-20241022, claude-3-5-haiku-20241022
API Key: sk-ant-...
Base URL: （留空使用官方 API）
```

### 3. Google Gemini
```
Provider: gemini
Model: gemini-1.5-pro, gemini-1.5-flash
API Key: AI...
Base URL: （留空使用官方 API）
```

### 4. 兼容 OpenAI API（本地模型）
```
Provider: openai_compatible
Model: 本地模型名称（如 qwen2.5:7b）
API Key: （本地模型通常留空）
Base URL: http://localhost:11434（Ollama）或其他本地地址
```

支持的本地框架：
- Ollama
- LM Studio
- vLLM
- FastChat
- LocalAI

---

## 配置方式

### 方式 1：前端 UI 配置（推荐）

1. 打开插件控制面板
2. 点击右上角 ⚙️ 设置
3. 滚动到 **🤖 LLM 配置** 区域
4. 配置主模型和意图模型
5. 点击 **💾 保存配置**

### 方式 2：配置文件
编辑 `data/config/user_config.json`：

```json
{
  "llm_main_provider": "anthropic",
  "llm_main_model": "claude-3-5-sonnet-20241022",
  "llm_main_api_key": "sk-ant-...",
  "llm_main_base_url": "",
  
  "llm_intent_provider": "anthropic",
  "llm_intent_model": "claude-3-5-haiku-20241022",
  "llm_intent_api_key": "sk-ant-...",
  "llm_intent_base_url": "",
  
  "llm_max_calls_per_minute": 15
}
```

### 方式 3：宿主注入（高级）
宿主程序可动态注入 LLM：

```python
# 主 LLM
plugin.__call_llm = async (prompt: str) -> str

# 意图 LLM（可选，未设置时使用主 LLM）
plugin.__call_intent_llm = async (prompt: str) -> str
```

---

## 推荐配置方案

### 方案 1：经济实惠
```
主 LLM: Claude Sonnet (对话质量好)
意图 LLM: Claude Haiku (快速便宜)
成本: ~$0.25/小时
```

### 方案 2：极致性价比
```
主 LLM: GPT-4o-mini (便宜)
意图 LLM: GPT-4o-mini (共用)
成本: ~$0.05/小时
```

### 方案 3：完全本地
```
主 LLM: qwen2.5:14b (Ollama)
意图 LLM: qwen2.5:7b (Ollama)
成本: 免费（需要 GPU）
```

### 方案 4：最佳体验
```
主 LLM: GPT-4 (最强对话)
意图 LLM: GPT-4o-mini (快速解析)
成本: ~$0.8/小时
```

---

## 单 LLM 模式

如果只配置主 LLM，意图解析会自动使用主 LLM：

```json
{
  "llm_main_provider": "openai",
  "llm_main_model": "gpt-4o-mini",
  "llm_main_api_key": "sk-...",
  
  "llm_intent_provider": "",
  "llm_intent_model": ""
}
```

---

## 限流配置

- `llm_max_calls_per_minute`: 每分钟最大调用次数（默认 15）
- 优先级：意图解析 > 紧急事件 > 交互说话 > 自主思考
- 超限后自动降级到正则 fallback，不会阻塞

---

## 常见问题

### Q: 如何判断 LLM 是否正常工作？
A: 插件启动日志会显示：
```
[boot] 已注入意图解析 LLM（独立意图模型）
[boot] 已注入紧急评估 LLM
```

### Q: 可以只配置意图 LLM 吗？
A: 不建议。意图 LLM 专注结构化，对话质量差。建议至少配置主 LLM。

### Q: 本地模型如何配置？
A: 
1. 启动 Ollama: `ollama serve`
2. Provider 选择 `openai_compatible`
3. Base URL 填 `http://localhost:11434/v1`
4. Model 填模型名称（如 `qwen2.5:7b`）

### Q: API Key 会被明文保存吗？
A: 是的。建议设置文件权限，或使用环境变量（高级用法）。

### Q: 配置错误会怎样？
A: 自动降级到正则 fallback，不影响游戏操作，只是指令解析准确率下降。
