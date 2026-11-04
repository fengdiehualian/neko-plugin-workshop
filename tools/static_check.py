"""静态交叉检查:页面元素 id / 前端 fetch 路径 / 服务端路由 / HTML 标签配对。

专门抓「语法没错、运行时静默失效」的那类问题:
  · JS 用 getElementById('x') 但 HTML 没有 id="x" → 取到 null,按钮点了没反应
  · fetch('/api/y') 但服务端没有对应路由 → 404,界面悄悄失败
  · 同一 id 写两次 → querySelector 拿到错的元素
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ["wb-studio.mjs", "wb-agent-lib.mjs"]


def load(name):
    p = os.path.join(ROOT, name)
    return open(p, encoding="utf-8").read() if os.path.isfile(p) else ""


def pages(src):
    """引导页、主页各算一份,不能混着查"""
    docs = re.findall(r"<!doctype html>.*?</html>", src, re.S | re.I)
    out = []
    for i, html in enumerate(docs):
        js = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))
        name = {0: "引导页", 1: "主页"}.get(i, f"页面{i + 1}")
        out.append((name, html, js))
    return out


def check_ids(html, js):
    ids = re.findall(r'\bid="([^"]+)"', html)
    dup = sorted({i for i in ids if ids.count(i) > 1})
    used = set()
    for pat in (r"getElementById\(\s*['\"]([^'\"]+)['\"]",
                r"\$\(\s*['\"]#([A-Za-z0-9_-]+)['\"]",
                r"querySelector(?:All)?\(\s*['\"]#([A-Za-z0-9_-]+)['\"]"):
        used |= set(re.findall(pat, js))
    missing = sorted(u for u in used if u not in set(ids))
    return missing, dup, len(ids), len(used)


def check_routes(src, js):
    routes = set(re.findall(r'url\.pathname\s*===\s*"([^"]+)"', src))
    prefixes = set(re.findall(r'url\.pathname\.startsWith\(\s*"([^"]+)"', src))
    calls = set(re.findall(r"fetch\(\s*['\"`](/api[^'\"`$?\s]*)", js))
    unknown = []
    for c in sorted(calls):
        base = c.rstrip("/")
        ok = base in routes or any(base.startswith(p) or base == p.rstrip("/") for p in prefixes)
        if not ok:
            stem = re.sub(r"/[^/]+$", "", base)
            ok = stem in routes or any(base.startswith(p) for p in prefixes)
        if not ok:
            unknown.append(c)
    return unknown, len(routes), len(prefixes), len(calls)


def check_html_balance(html):
    void = {"br", "hr", "img", "input", "meta", "link", "source", "path", "circle", "rect",
            "line", "polyline", "stop", "area", "base", "col", "embed", "param", "track", "wbr"}
    stack = []
    bad = []
    for m in re.finditer(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*?)(/?)>", html):
        close, tag, selfclose = m.group(1), m.group(2).lower(), m.group(4)
        if tag in void or selfclose == "/":
            continue
        if close:
            if stack and stack[-1][0] == tag:
                stack.pop()
            else:
                bad.append((tag, m.start()))
                if any(t == tag for t, _ in stack):
                    while stack and stack[-1][0] != tag:
                        stack.pop()
                    if stack:
                        stack.pop()
        else:
            stack.append((tag, m.start()))
    unclosed = [(t, p) for t, p in stack if t not in ("html", "body", "head")]
    return bad, unclosed



def check_panel_structure(html):
    """设置面板的每个页签面板(.sp-body#tab-*)必须是 #setpanel 的直接子级。
    专抓「总数配对平衡但嵌套错位」:v1.2.44 搬页签时丢了 tab-look 的闭合,
    tab-update 被吞进 tab-look,切到更新页签时整个面板 0x0 隐形(总数查不出)。"""
    stack = []  # 元素为 div 的 id(None=无 id)
    bad = []
    # 先剥离 <script> 块:JS 字符串里拼接的 HTML 会污染 div 配对(与 check_ids 的 html/js 分离同理)
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S)
    for m in re.finditer(r"<(/?)(div)([^>]*?)>", html):
        close, attrs = m.group(1), m.group(3)
        idm = re.search(r'\bid="([^"]+)"', attrs)
        clsm = re.search(r'\bclass="([^"]+)"', attrs)
        did = idm.group(1) if idm else None
        if close:
            # 闭合标签不带属性:按位置出栈即可(嵌套错位检测看的是打开时栈顶父级)
            if stack:
                stack.pop()
        else:
            cls = clsm.group(1) if clsm else ""
            if "sp-body" in cls and did and did.startswith("tab-"):
                parent = stack[-1] if stack else None
                if parent != "setpanel":
                    bad.append(f"{did} 的父级是 {parent or '未知'}(应为 setpanel)")
            stack.append(did)
    return bad


def main():
    src_all = "\n".join(load(f) for f in FILES)
    rc = 0
    all_js = []
    for name, html, js in pages(load("wb-studio.mjs")):
        all_js.append(js)
        print(f"\n===== {name}:HTML {len(html)} 字节 / JS {len(js)} 字节 =====")
        missing, dup, n_ids, n_used = check_ids(html, js)
        print(f"[id] 定义 {n_ids} 个,JS 引用 {n_used} 个")
        for m in missing:
            print(f"  ✗ JS 引用了不存在的 id: {m}")
            rc = 1
        for d in dup:
            print(f"  ✗ 重复 id: {d}")
            rc = 1
        bad, unclosed = check_html_balance(html)
        if bad or unclosed:
            print("[html] 标签问题:")
            for tag, pos in bad[:10]:
                print(f"  ✗ 多余或错配的闭合 </{tag}> @ {pos}")
            for tag, pos in unclosed[:10]:
                print(f"  ✗ 未闭合 <{tag}> @ {pos}")
            rc = 1
        else:
            print("[html] 标签配对 OK")
        pbad = check_panel_structure(html)
        if pbad:
            print("[panel] 设置面板嵌套错位:")
            for b in pbad:
                print(f"  \u2717 {b}")
            rc = 1
        else:
            print("[panel] 六个页签面板均为 setpanel 直接子级")

    js_all = "\n".join(all_js)
    unknown, n_exact, n_prefix, n_calls = check_routes(src_all, js_all)
    print(f"\n[route] 服务端精确路由 {n_exact} / 前缀 {n_prefix};前端 fetch {n_calls} 个")
    for u in unknown:
        print(f"  ✗ 前端调用但服务端无匹配路由: {u}")
        rc = 1
    if not unknown:
        print("[route] 全部有对应服务端路由")

    print("\n结论:" + (" 发现问题(见上)" if rc else " 全部通过"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
