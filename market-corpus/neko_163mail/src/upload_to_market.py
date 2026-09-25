"""自动化上传插件到 N.E.K.O 插件商城"""
import json
import time
import urllib.request

SESSION = "neko-163mail-upload"
BASE = "http://127.0.0.1:10086/command"

def send(action, args=None):
    payload = json.dumps({"action": action, "args": args or {}, "session": SESSION}).encode("utf-8")
    req = urllib.request.Request(BASE, data=payload, headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}

# 插件信息
PLUGIN_NAME = "网易邮箱助手"
PLUGIN_DESC = "让猫娘帮你管理网易163邮箱,生成邮件摘要、判断优先级、标记已读、发送邮件。清新诗意的前端界面,支持附件发送。"
REPO_URL = "https://github.com/StarrySerendipity/n.e.k.o_plugin_neko_163mail"

print("=== 步骤 1: 导航到插件商城上传页面 ===")
result = send("navigate", {
    "url": "https://market.project-neko.cn/#/upload",
    "newTab": True,
    "group_title": "neko_163mail 插件上传"
})
print(f"导航结果: {result}")
time.sleep(3)

print("\n=== 步骤 2: 探查页面元素 ===")
# 获取所有 input 元素
inputs_result = send("evaluate", {"code": """
(() => {
    const inputs = document.querySelectorAll('input');
    const results = [];
    for (let i = 0; i < inputs.length; i++) {
        results.push({
            idx: i,
            type: inputs[i].type,
            placeholder: inputs[i].placeholder,
            name: inputs[i].name,
            id: inputs[i].id,
            value: inputs[i].value
        });
    }
    return JSON.stringify(results, null, 2);
})()
"""})
print(f"Input 元素: {inputs_result}")

# 获取所有 textarea 元素
textareas_result = send("evaluate", {"code": """
(() => {
    const textareas = document.querySelectorAll('textarea');
    const results = [];
    for (let i = 0; i < textareas.length; i++) {
        results.push({
            idx: i,
            placeholder: textareas[i].placeholder,
            name: textareas[i].name,
            id: textareas[i].id,
            value: textareas[i].value
        });
    }
    return JSON.stringify(results, null, 2);
})()
"""})
print(f"Textarea 元素: {textareas_result}")

print("\n=== 步骤 3: 填写插件名称 ===")
js_set_name = f"""
(() => {{
    const inputs = document.querySelectorAll('input');
    for (let i = 0; i < inputs.length; i++) {{
        if (inputs[i].placeholder && (inputs[i].placeholder.includes('名称') || inputs[i].placeholder.includes('name'))) {{
            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(inputs[i], {json.dumps(PLUGIN_NAME)});
            inputs[i].dispatchEvent(new Event('input', {{ bubbles: true }}));
            inputs[i].dispatchEvent(new Event('change', {{ bubbles: true }}));
            return 'name set: ' + inputs[i].value;
        }}
    }}
    return 'name input not found';
}})()
"""
name_result = send("evaluate", {"code": js_set_name})
print(f"名称设置结果: {name_result}")

print("\n=== 步骤 4: 填写仓库 URL ===")
js_set_url = f"""
(() => {{
    const inputs = document.querySelectorAll('input');
    for (let i = 0; i < inputs.length; i++) {{
        if (inputs[i].placeholder && (inputs[i].placeholder.includes('仓库') || inputs[i].placeholder.includes('GitHub') || inputs[i].placeholder.includes('url'))) {{
            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(inputs[i], {json.dumps(REPO_URL)});
            inputs[i].dispatchEvent(new Event('input', {{ bubbles: true }}));
            inputs[i].dispatchEvent(new Event('change', {{ bubbles: true }}));
            return 'url set: ' + inputs[i].value;
        }}
    }}
    return 'url input not found';
}})()
"""
url_result = send("evaluate", {"code": js_set_url})
print(f"URL 设置结果: {url_result}")

print("\n=== 步骤 5: 填写插件简介 ===")
js_set_desc = f"""
(() => {{
    const ta = document.querySelector('textarea');
    if (ta) {{
        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
            window.HTMLTextAreaElement.prototype, 'value').set;
        nativeInputValueSetter.call(ta, {json.dumps(PLUGIN_DESC)});
        ta.dispatchEvent(new Event('input', {{ bubbles: true }}));
        ta.dispatchEvent(new Event('change', {{ bubbles: true }}));
        return 'desc set: ' + ta.value.substring(0, 50) + '...';
    }}
    return 'textarea not found';
}})()
"""
desc_result = send("evaluate", {"code": js_set_desc})
print(f"简介设置结果: {desc_result}")

print("\n=== 步骤 6: 获取页面快照验证 ===")
snapshot_result = send("snapshot", {})
print(f"快照结果: {snapshot_result}")

print("\n=== 步骤 7: 等待用户确认并提交 ===")
print("请在浏览器中检查表单填写是否正确。")
print("如果正确，请手动点击'提交'按钮。")
print("如果需要修改，请手动修改后告诉我。")
print("\n脚本执行完成！")
