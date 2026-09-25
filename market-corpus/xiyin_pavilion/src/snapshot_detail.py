"""
获取插件详情页快照，分析页面结构
"""
import json
import time
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

# 导航到插件详情页
print("导航到插件详情页...")
send("navigate", {
    "url": "https://market.project-neko.cn/#/plugin/xiyin_pavilion",
    "newTab": False,
    "group_title": "插件商城"
})
time.sleep(3)

# 获取当前URL
url_check = send("evaluate", {"code": "window.location.href"})
print(f"当前URL: {url_check}")

# 获取页面快照
print("\n获取页面快照...")
result = send("snapshot")
print(json.dumps(result, indent=2, ensure_ascii=False)[:5000])

# 提取所有按钮和链接
js_extract = """
(() => {
    const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]'));
    return buttons.map(btn => ({
        tag: btn.tagName,
        text: btn.textContent.trim().substring(0, 60),
        href: btn.href || '',
        class: btn.className.substring(0, 80)
    }));
})()
"""
result = send("evaluate", {"code": js_extract})
print("\n页面按钮和链接:")
print(json.dumps(result, indent=2, ensure_ascii=False))
