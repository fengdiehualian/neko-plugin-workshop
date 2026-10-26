"""持久化层（第一层专用）。

* :mod:`storage.repo` —— SQLite 读写与 schema 管理。
* :mod:`storage.session` —— 会话重建与引擎配置装配。

这一层是**第一层**（N.E.K.O 插件层）的一部分：它把宿主提供的 ``self.db`` /
``self.state`` 翻译成引擎层能用的对象，**不含任何游戏规则**。
"""
