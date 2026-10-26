"""N.E.K.O Mental Omega companion plugin package.

The plugin class is loaded lazily so protocol and policy modules can be tested
without importing the N.E.K.O SDK outside the host application.
"""

from __future__ import annotations


def __getattr__(name: str):
    if name == "MOCompanionPlugin":
        from .plugin import MOCompanionPlugin

        return MOCompanionPlugin
    raise AttributeError(name)


__all__ = ["MOCompanionPlugin"]

