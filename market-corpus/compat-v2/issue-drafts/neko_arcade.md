# [草稿,未发布] 猫娘小游戏 与猫爪重写 #3683 的兼容点

> 本文件由 tools/gen_compat_report.py 自动生成,尚未提交。
> 发布前必须由维护者逐条人工核实下面每一行属实,再发到 https://github.com/miaoluoxue/n.e.k.o_plugin_neko_arcade 的 Issues。批量自动发=骚扰,禁止。

目标仓库: https://github.com/miaoluoxue/n.e.k.o_plugin_neko_arcade
依据: RFC https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683 第 4.2「会删除」/ 第 5 节自查
扫描源码版本: commit f077eee

## 建议的 issue 正文

标题: [兼容性] 猫爪重写 #3683:本插件命中以下会删面,请自查

参考: https://github.com/Project-N-E-K-O/N.E.K.O/issues/3683

以下位置使用了将删除的内部模块/接口/随包依赖(扫描日期 2026-11-14,均为运行时代码):

- `adapters/image_renderer.py:457` (bundled_playwright): `from playwright.async_api import async_playwright`

## 处理建议

- bundled_playwright: 不要再依赖宿主随包的 playwright/Chromium;需要浏览器自动化就自带依赖(vendor/ 或 pyproject/requirements 声明并自行安装)。

行为项(触发时机/入口被使魔调用/取消中断)静态扫描证不了,请一并按 RFC 第 5 节自查。

## 证据(全部命中;待核类不要求改动,仅备查)

- [review/runtime] playwright_mention :: `adapters/image_renderer.py:10` :: `3. 宿主 Launcher 设置的 ``PLAYWRIGHT_BROWSERS_PATH`` 与应用自带的 playwright_browsers`
- [strong/runtime] bundled_playwright :: `adapters/image_renderer.py:457` :: `from playwright.async_api import async_playwright`
- [review/runtime] playwright_mention :: `adapters/image_renderer.py:466` :: `#    按 PLAYWRIGHT_BROWSERS_PATH(Launcher 冻结时注入)找浏览器`
- [review/runtime] playwright_mention :: `adapters/image_renderer.py:514` :: `应用自带的 playwright_browsers 可能只有资源文件、缺 chrome.exe`
- [review/runtime] playwright_mention :: `adapters/image_renderer.py:519` :: `env_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")`
- [review/runtime] playwright_mention :: `adapters/image_renderer.py:526` :: `for _ in range(5):  # 应用布局: <app>/resources/bin/playwright_browsers`
- [review/runtime] playwright_mention :: `adapters/image_renderer.py:530` :: `roots.append(os.path.join(cur, "playwright_browsers"))`
- [review/runtime] host_internal_import :: `adapters/image_renderer.py:589` :: `mod = importlib.import_module("brain.browser_use_adapter")`
- [review/runtime] playwright_mention :: `adapters/image_renderer.py:615` :: `env_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")`
- [review/runtime] playwright_mention :: `adapters/image_renderer.py:626` :: `log.info("帮助图使用宿主 PLAYWRIGHT_BROWSERS_PATH: %s", matches[-1])`
- [review/runtime] playwright_mention :: `adapters/image_renderer.py:635` :: `1. 宿主自己的查找函数 / browser_use 依赖 / 宿主注入的 PLAYWRIGHT_BROWSERS_PATH`
- [review/runtime] playwright_mention :: `adapters/image_renderer.py:636` :: `2. Playwright 浏览器目录(应用自带 playwright_browsers / 系统 ms-playwright 缓存)`
- [review/runtime] playwright_mention :: `adapters/image_renderer.py:638` :: `应用自带的 playwright_browsers 可能只有资源文件、缺 chrome.exe`
- [review/dev] playwright_mention :: `docs/CHANGELOG.md:306` :: ``PLAYWRIGHT_BROWSERS_PATH`，启动链先"不指定 exe"，失败才退显式路径；全链失败再降级。`
- [review/dev] playwright_mention :: `docs/help-config.md:9` :: ``PLAYWRIGHT_BROWSERS_PATH`；失败才退到显式路径与系统浏览器）`
- [review/dev] playwright_mention :: `tests/test_help_render.py:84` :: `assert any("playwright_browsers" in r for r in roots), roots`
- [review/dev] playwright_mention :: `tests/test_help_render.py:150` :: `monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)`
- [review/dev] playwright_mention :: `tests/test_help_render.py:165` :: `"""渲染尽量走宿主: 首选"不指定 exe"(宿主 PLAYWRIGHT_BROWSERS_PATH), 显式路径兜底。"""`