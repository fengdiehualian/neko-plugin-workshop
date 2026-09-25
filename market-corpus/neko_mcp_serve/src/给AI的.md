# 如何让 NEKO 说话（外部 → NEKO）

> 本文档告诉 AI / 外部程序：怎么主动驱动 NEKO（猫娘）开口说话。
> 方向：**外部程序 → NEKO**（反向主动通道）。NEKO 只负责把收到的内容用形象 + TTS 说出来。

## 1. 入口在哪

NEKO 侧跑了一个插件 **neko_mcp_serve**，在本机开一个 HTTP 服务：

- **地址**：`http://127.0.0.1:48930`
- 插件用 NEKO 官方 SDK 的 `push_message`，把外部发来的文本推给猫的会话。

## 2. 两个端点

| 端点 | 用途 | 行为 |
|---|---|---|
| `POST /v1/say`（或 `/say`） | **让猫说话（走模型）** | `push_message(ai_behavior="respond")`：触发猫按对话模型生成回应，再 TTS 发声 |
| `POST /v1/speak`（或 `/speak`） | **让猫直接照读** | `push_message(ai_behavior="blind")`：直接显示/说出给定文本，**不做模型生成**（外部已定好内容时用） |

## 3. 请求格式（JSON）

```json
POST /v1/say
{
  "text": "要猫说的话",
  "source": "谁让说的（可选，默认 neko_mcp_serve）"
}
```

- `text`：**必填**，猫要说的内容，去掉首尾空白。
- `source`：可选，来源标记（如 `"dsh-brain"`）。
- `/v1/speak` 的 body 格式相同。

## 4. 鉴权（可选）

如果插件配置了 `token`，请求头必须带：

```
X-NEKO-Token: <配置里设置的 token>
```

- 没配置 token 时鉴权跳过（默认 `_DEFAULT_TOKEN = ""`）。
- "鉴权失败"返回 `403 {"ok":false,"error":"unauthorized"}`。

## 5. 返回

```json
200: { "ok": true, "pushed": "queued", "mode": "respond" | "blind" }
```

- `pushed=queued`：**已排队**，不等猫真说完。说话是 NEKO 自己的管线（模型+TTS）**异步**完成的。
- 常见状态码：
  - `200` 成功入队
  - `400` 空 text / 非法 JSON
  - `403` 鉴权失败
  - `404` 路径不在 /say /speak /v1/say /v1/speak /health 里
  - `500` push_message 抛异常

## 6. 示例（curl）

```bash
# 让猫按模型生成并说话
curl -s -X POST http://127.0.0.1:48930/v1/say \
  -H "Content-Type: application/json" \
  -H "X-NEKO-Token: 你的token" \
  -d '{"text":"你回来了呀","source":"dsh-brain"}'

# 让猫直接照读这段（不触发生成）
curl -s -X POST http://127.0.0.1:48930/v1/speak \
  -H "Content-Type: application/json" \
  -d '{"text":"主人，该休息了。"}'
```

```python
import requests
r = requests.post("http://127.0.0.1:48930/v1/say",
                  json={"text": "你好呀", "source": "dsh-brain"},
                  headers={"X-NEKO-Token": "你的token"}, timeout=5)
print(r.json())
```

## 7. 健康检查

```bash
curl http://127.0.0.1:48930/health
# -> {"ok":true,"service":"neko_mcp_serve","port":48930}
```

## 8. 常见坑（AI 必读）

1. **选了 `/say` 却想让猫"复述我给的原文"** — `/say` 会走模型再生成，可能改写你的话。要**一字不差**用 `/v1/speak`（blind）。
2. **`text` 别传空**，会 400。
3. **别把 `text` 写进日志/审计**（防暴露用户隐私）。
4. **这是本机端口**，只在 `127.0.0.1` 生效，外部网络访问不到。
5. 说话是**异步**的：`200 queued` 不代表猫已经说完，只是已接收。

## 9. 和"挂载会话"的区别（重要，别混）

- **挂载（DSH 3080 /v1/chat/completions）**：是 **NEKO 主动来问** DSH 时才生效——NEKO 说话前自己来 DSH 取答案。DSH 只回答，**不会主动推**。
- **本接口（48930）**：是**外部主动叫猫说话**的方向。要让 DSH（或其他程序）**主动**让 NEKO 开口，必须走这里。
- 两者不冲突：挂载决定"NEKO 来问时怎么生成"；48930 决定"外部主动叫猫说话"。
