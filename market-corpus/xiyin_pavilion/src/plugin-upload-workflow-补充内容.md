# plugin-upload-workflow 技能文档补充内容

**添加位置**：在"阶段五：在插件商城发布版本"章节的"5.3 发布失败的常见原因与解决"表格之后

---

## ⚠️ 重要：避免在"我的插件"列表页误操作其他插件

### 失败案例（2026-08-10）

在为汐音阁插件发布版本时，AI 在"我的插件申请"页面通过文本匹配"发布新版本"来定位按钮，结果误点了 Codex Adapter 的"发布新版本"按钮，导致为错误的插件发布了版本。用户不得不手动撤回并重新上传。

### 错误原因

1. "我的插件申请"页面显示多个插件卡片，每个卡片都有"发布新版本"按钮
2. 使用全局文本匹配（如 `button:has-text("发布新版本")`）会匹配到第一个出现的按钮，而非目标插件的按钮
3. 点击后没有验证页面 URL 是否导航到了正确的插件详情页

### 正确做法

#### 方法 1：通过插件详情页发布版本（推荐）

```python
import json
import urllib.request
import time

SESSION = "neko-plugin-market"
BASE = "http://127.0.0.1:10086/command"

def send(action, args=None):
    payload = json.dumps({
        "action": action,
        "args": args or {},
        "session": SESSION
    }).encode("utf-8")
    req = urllib.request.Request(
        BASE,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"}
    )
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read().decode("utf-8"))

# 先导航到目标插件的详情页
plugin_id = "xiyin_pavilion"  # 替换为目标插件 ID
send("navigate", {
    "url": f"https://market.project-neko.cn/#/plugin/{plugin_id}?tab=versions",
    "newTab": False,
    "group_title": "插件版本管理"
})
time.sleep(3)

# 在详情页点击"发布新版本"
send("click", {"selector": "button:has-text('发布新版本')"})
```

#### 方法 2：在列表页精确定位目标插件卡片

```python
# 先定位目标插件卡片，再在该卡片内查找按钮
js_click_correct = """
(() => {
    const cards = document.querySelectorAll('[class*="card"], article, section');
    for (const card of cards) {
        if (card.textContent.includes('汐音阁') || card.textContent.includes('xiyin_pavilion')) {
            const btn = card.querySelector('a:has-text("发布新版本"), button:has-text("发布新版本")');
            if (btn) {
                btn.click();
                return 'clicked';
            }
        }
    }
    return 'not found';
})()
"""
send("evaluate", {"code": js_click_correct})
```

#### 点击后必须验证页面 URL

```python
time.sleep(2)
url_check = send("evaluate", {"code": "window.location.href"})
print(f"Current URL: {url_check}")

# 验证 URL 包含正确的插件 ID 或数字 ID
# 例如：汐音阁的 URL 应该包含 'xiyin_pavilion' 或 '/plugin/8'
if 'xiyin_pavilion' not in url_check and '/plugin/8' not in url_check:
    print("❌ 误操作了其他插件，需要撤回并重新操作")
    # 可以选择自动撤回或提示用户手动处理
else:
    print("✅ 成功导航到目标插件的发布页面")
```

### 误操作后的补救措施

如果已经误操作了其他插件：

1. **在错误插件的版本页面点击"撤回"按钮**
   ```python
   send("click", {"selector": "button:has-text('撤回')"})
   time.sleep(2)
   ```

2. **返回"我的插件"页面**
   ```python
   send("navigate", {
       "url": "https://market.project-neko.cn/#/my/plugins",
       "newTab": False,
       "group_title": "我的插件"
   })
   time.sleep(3)
   ```

3. **使用正确的方法重新点击目标插件的"发布新版本"按钮**
   - 使用方法 1（推荐）：先导航到目标插件详情页，再点击发布按钮
   - 或使用方法 2：在列表页精确定位目标卡片后再点击

### 核心原则

| 原则 | 说明 |
|------|------|
| ✅ **优先通过插件详情页发布版本** | 避免在列表页操作，减少误操作风险 |
| ✅ **如果在列表页操作，必须使用作用域选择器** | 先定位目标卡片，再在该卡片内查找按钮 |
| ✅ **点击后立即验证页面 URL** | 检查 URL 是否包含正确的插件 ID 或数字 ID |
| ❌ **禁止使用全局文本匹配在列表页点击"发布新版本"** | 会匹配到第一个出现的按钮，导致误操作 |

### 验证清单

在点击"发布新版本"按钮后，执行以下验证：

```python
# 1. 检查页面 URL
url_check = send("evaluate", {"code": "window.location.href"})
print(f"URL: {url_check}")

# 2. 检查页面标题或插件名称
page_check = send("evaluate", {"code": """
(() => {
    const bodyText = document.body.innerText;
    return {
        hasPluginName: bodyText.includes('汐音阁'),
        hasVersionTab: bodyText.includes('版本') || bodyText.includes('versions'),
        url: window.location.href
    };
})()
"""})
print(f"Page check: {page_check}")

# 3. 确认无误后再填写 Release URL 并提交
```

---

## 总结

**错误**：在"我的插件申请"列表页使用全局选择器点击"发布新版本"，误操作了 Codex Adapter

**原因**：
- 没有验证点击后的页面 URL
- 没有使用作用域选择器定位目标插件卡片
- 使用了全局文本匹配，匹配到第一个出现的按钮

**正确做法**：
- 通过插件详情页发布版本（推荐）
- 或在列表页先定位目标卡片再点击
- 点击后立即验证页面 URL 是否包含正确的插件标识

**教训**：在多卡片列表页操作时，必须使用作用域选择器，并在操作后验证结果，避免误操作其他对象。
