# [草稿,未发布] 猫娘小游戏 与猫爪重写 #3683 的兼容点

> 本文件由 tools/gen_compat_report.py 自动生成,尚未提交.
> 发布前必须由维护者人工核实下面每一行确实中招,再发到 https://github.com/miaoluoxue/n.e.k.o_plugin_neko_arcade 的 Issues.

目标仓库: https://github.com/miaoluoxue/n.e.k.o_plugin_neko_arcade
依据: RFC https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683 第 4.2「会删除」/ 第 5 节自查

## 建议的 issue 正文

标题: [兼容性] 猫爪重写 #3683:本插件命中以下静态特征,请自查

参考: https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683

以下位置使用了将删除的内部模块/接口/随包依赖(扫描日期 2026-11-14):

- `adapters/image_renderer.py:10` (bundled_playwright): `3. 宿主 Launcher 设置的 ``PLAYWRIGHT_BROWSERS_PATH`` 与应用自带的 playwright_browsers`
- `adapters/image_renderer.py:457` (bundled_playwright): `from playwright.async_api import async_playwright`
- `adapters/image_renderer.py:514` (bundled_playwright): `应用自带的 playwright_browsers 可能只有资源文件、缺 chrome.exe`
- `adapters/image_renderer.py:526` (bundled_playwright): `for _ in range(5):  # 应用布局: <app>/resources/bin/playwright_browsers`
- `adapters/image_renderer.py:530` (bundled_playwright): `roots.append(os.path.join(cur, "playwright_browsers"))`
- `adapters/image_renderer.py:636` (bundled_playwright): `2. Playwright 浏览器目录(应用自带 playwright_browsers / 系统 ms-playwright 缓存)`
- `adapters/image_renderer.py:638` (bundled_playwright): `应用自带的 playwright_browsers 可能只有资源文件、缺 chrome.exe`

## 处理建议

- bundled_playwright: 不要再依赖宿主随包的 playwright/playwright_browsers;需要浏览器自动化就自带依赖(vendor/ 或 pyproject 声明).

行为项(触发时机/入口被使魔调用/取消中断)静态扫描证不了,请一并按 RFC 第 5 节自查.

## 证据(全部命中,含仅文档/测试面)

- [runtime] bundled_playwright :: `adapters/image_renderer.py:10` :: `3. 宿主 Launcher 设置的 ``PLAYWRIGHT_BROWSERS_PATH`` 与应用自带的 playwright_browsers`
- [runtime] bundled_playwright :: `adapters/image_renderer.py:457` :: `from playwright.async_api import async_playwright`
- [runtime] bundled_playwright :: `adapters/image_renderer.py:514` :: `应用自带的 playwright_browsers 可能只有资源文件、缺 chrome.exe`
- [runtime] bundled_playwright :: `adapters/image_renderer.py:526` :: `for _ in range(5):  # 应用布局: <app>/resources/bin/playwright_browsers`
- [runtime] bundled_playwright :: `adapters/image_renderer.py:530` :: `roots.append(os.path.join(cur, "playwright_browsers"))`
- [runtime] bundled_playwright :: `adapters/image_renderer.py:636` :: `2. Playwright 浏览器目录(应用自带 playwright_browsers / 系统 ms-playwright 缓存)`
- [runtime] bundled_playwright :: `adapters/image_renderer.py:638` :: `应用自带的 playwright_browsers 可能只有资源文件、缺 chrome.exe`
- [dev] bundled_playwright :: `tests/test_help_render.py:84` :: `assert any("playwright_browsers" in r for r in roots), roots`