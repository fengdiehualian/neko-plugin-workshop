"""战斗系统工具: 依赖注入容器 + 数值提取。

容器行为: ``container[Cls]`` 已注册返回单例, 未注册按构造签名自动注入
已注册的依赖后实例化(如 ``Buff(logger)`` 自动拿容器里的 Logger)。
(移植自原战斗系统依赖, 独立实现, 不引用任何外部项目)
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Dict


class _Container(dict):
    """依赖注入容器: 显式赋值过的类返回单例, 未注册的每次 new 新实例。"""

    def __missing__(self, key):
        try:
            sig = inspect.signature(key.__init__)
        except (TypeError, ValueError):
            sig = None
        kwargs = {}
        if sig is not None:
            for pname, param in sig.parameters.items():
                if pname == "self":
                    continue
                if param.annotation is not inspect.Parameter.empty:
                    for reg_cls in list(self.keys()):
                        try:
                            if issubclass(reg_cls, param.annotation) or param.annotation is reg_cls:
                                kwargs[pname] = self[reg_cls]
                                break
                        except TypeError:
                            continue
                if pname not in kwargs and param.default is inspect.Parameter.empty:
                    for reg_cls, obj in self.items():
                        if getattr(reg_cls, "__name__", "").lower() == pname.lower():
                            kwargs[pname] = obj
                            break
        try:
            obj = key(**kwargs)
        except Exception:
            try:
                obj = key()
            except Exception:
                raise KeyError(key) from None
        # 注意: 不缓存 —— 未显式注册的类每次 new(如 Pokemon 每场战斗新建)
        return obj


_container: _Container = _Container()


def get_container() -> Dict[type, Any]:
    """返回进程级依赖注入容器(战斗系统各组件按类存取, 未注册自动注入实例化)。"""
    return _container


def get_num(text: str) -> int:
    """从字符串中提取数字(如「连击50」→ 50), 无数字返回 0。"""
    m = re.search(r"-?\d+", str(text or ""))
    if m:
        return int(m.group(0))
    return 0


def get_count(text: str) -> int:
    """提取技能/效果次数(默认 1)。"""
    n = get_num(text)
    return n if n else 1
