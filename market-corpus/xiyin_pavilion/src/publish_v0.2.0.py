"""
发布汐音阁插件 v0.3.0 到插件商城
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

# 1. 导航到汐音阁插件详情页
print("导航到汐音阁插件详情页...")
send("navigate", {
    "url": "https://market.project-neko.cn/#/plugin/xiyin_pavilion",
    "newTab": False,
    "group_title": "插件商城"
})
time.sleep(3)

# 2. 检查页面URL，确认导航成功
url_check = send("evaluate", {"code": "window.location.href"})
print(f"当前URL: {url_check}")

# 3. 查找并点击"发布新版本"按钮
print("查找发布新版本按钮...")
js_find_button = """
(() => {
    const buttons = Array.from(document.querySelectorAll('button, a'));
    const publishBtn = buttons.find(btn => 
        btn.textContent.includes('发布新版本') || 
        btn.textContent.includes('发布版本')
    );
    if (publishBtn) {
        publishBtn.click();
        return 'clicked';
    }
    return 'not found';
})()
"""
result = send("evaluate", {"code": js_find_button})
print(f"点击结果: {result}")
time.sleep(2)

# 4. 填写版本信息
print("填写版本信息...")
js_fill_version = """
(() => {
    // 查找版本输入框
    const inputs = Array.from(document.querySelectorAll('input'));
    const versionInput = inputs.find(input => 
        input.placeholder && (
            input.placeholder.includes('版本') || 
            input.placeholder.includes('version') ||
            input.name === 'version'
        )
    );
    
    if (versionInput) {
        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        ).set;
        nativeInputValueSetter.call(versionInput, '0.3.0');
        versionInput.dispatchEvent(new Event('input', { bubbles: true }));
        versionInput.dispatchEvent(new Event('change', { bubbles: true }));
        return 'version set';
    }
    return 'version input not found';
})()
"""
result = send("evaluate", {"code": js_fill_version})
print(f"版本填写结果: {result}")
time.sleep(1)

# 5. 填写 Release URL
print("填写 Release URL...")
js_fill_release_url = """
(() => {
    const inputs = Array.from(document.querySelectorAll('input'));
    const releaseUrlInput = inputs.find(input => 
        input.placeholder && (
            input.placeholder.includes('Release') || 
            input.placeholder.includes('release') ||
            input.placeholder.includes('GitHub')
        )
    );
    
    if (releaseUrlInput) {
        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        ).set;
        nativeInputValueSetter.call(releaseUrlInput, 'https://github.com/StarrySerendipity/n.e.k.o_plugin_xiyin_pavilion/releases/tag/v0.3.0');
        releaseUrlInput.dispatchEvent(new Event('input', { bubbles: true }));
        releaseUrlInput.dispatchEvent(new Event('change', { bubbles: true }));
        return 'release url set';
    }
    return 'release url input not found';
})()
"""
result = send("evaluate", {"code": js_fill_release_url})
print(f"Release URL填写结果: {result}")
time.sleep(1)

# 6. 点击提交按钮
print("提交发布...")
js_submit = """
(() => {
    const buttons = Array.from(document.querySelectorAll('button'));
    const submitBtn = buttons.find(btn => 
        btn.textContent.includes('提交') || 
        btn.textContent.includes('发布') ||
        btn.textContent.includes('确认')
    );
    if (submitBtn) {
        submitBtn.click();
        return 'submitted';
    }
    return 'submit button not found';
})()
"""
result = send("evaluate", {"code": js_submit})
print(f"提交结果: {result}")
time.sleep(3)

# 7. 检查发布结果
print("检查发布结果...")
final_url = send("evaluate", {"code": "window.location.href"})
print(f"最终URL: {final_url}")

print("发布流程完成！")
