"""测试夹具：注册最小 plugin.sdk.plugin stub。

插件根目录的 ``__init__.py`` 只惰性导出 MonetaFxPlugin，裸 import 不会
拉入 SDK；但本目录的测试会直接 import 插件模块，因此需要一个可用的
``plugin.sdk.plugin``。这里安装一个最小 stub，绝不随插件发布。

路径注入与 stub 注册都封装在 ``stub_plugin_sdk.install()`` 里，本模块只
负责在收集期调用一次。
"""

from __future__ import annotations

from stub_plugin_sdk import install

install()
