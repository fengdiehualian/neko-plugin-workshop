"""插件内置的第三方/第一方依赖副本。目录名有意带下划线前缀。

`connection_onebot` 是宿主第一方包 ``utils.connection.onebot``（PR #2996）的副本，
作为"宿主还没带这个包"时的回退。解析入口在 :mod:`plugin.plugins.qq_auto_reply.connector_seam`。

**不要把它挪到 `lib/` 或 `vendor/`** —— 两个更显眼的位置都试过，都会坏：

- ``lib/``：``_lib_bootstrap.py`` 与 ``tests/conftest.py`` 都把 ``lib/`` 插到 ``sys.path[0]``。
  宿主 ``utils/`` 是常规包（有 ``__init__.py``），所以 ``lib/utils/connection/...`` 要么
  整体遮蔽宿主 ``utils``（打断 ``utils.tts.*`` / ``utils.voice_clone`` /
  ``utils.internal_http_client`` 等既有 import），要么根本解析不到。
- ``vendor/``：那是插件市场 CLI 管的目录 —— ``sync --clean`` 会 ``shutil.rmtree(vendor/)``
  （``plugin/neko_plugin_cli/commands/deps_cmd.py``），而这步在 CI 的 release check 之前跑，
  放进去的副本会被删掉；且 ``.gitignore`` 忽略 ``vendor/``，根本提交不了。

副本的出处与本地改动见 ``connection_onebot/PROVENANCE.md``。
"""
