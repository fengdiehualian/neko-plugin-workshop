"""
获取插件商城页面快照
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
print(json.dumps(result, indent=2, ensure_ascii=False))
