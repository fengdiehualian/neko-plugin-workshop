"""
检查我的插件页面
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

# 导航到"我的插件"页面
print("导航到我的插件页面...")
send("navigate", {
    "url": "https://market.project-neko.cn/#/my/plugins",
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

# 提取所有按钮和链接
js_extract = """
(() => {
    const buttons = Array.from(document.querySelectorAll('button, a'));
    return buttons.map(btn => ({
        tag: btn.tagName,
        text: btn.textContent.trim().substring(0, 50),
        href: btn.href || '',
        class: btn.className.substring(0, 80)
    }));
})()
"""
result = send("evaluate", {"code": js_extract})
print("页面按钮和链接:")
print(json.dumps(result, indent=2, ensure_ascii=False))
