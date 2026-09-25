"""
检查插件市场页面结构 - 获取前面部分
"""
import json
import urllib.request

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

# 获取页面快照
print("获取页面快照...")
result = send("snapshot")

# 保存完整快照到文件
with open("snapshot_full.json", "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)

# 提取所有按钮和链接
js_extract = """
(() => {
    const buttons = Array.from(document.querySelectorAll('button, a'));
    return buttons.map(btn => ({
        tag: btn.tagName,
        text: btn.textContent.trim().substring(0, 50),
        href: btn.href || '',
        class: btn.className
    }));
})()
"""
result = send("evaluate", {"code": js_extract})
print("页面按钮和链接:")
print(json.dumps(result, indent=2, ensure_ascii=False))
