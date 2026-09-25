"""Vendor path injection.

Import this module FIRST to ensure vendored pywin32 binaries are on sys.path
before any import of win32com / pythoncom.

Design note: this module intentionally performs its work at import time
(module-level code) so that the importer's ``from . import _vendor_path``
acts both as an import statement (E402 clean) AND triggers the path setup.
"""
import os as _os
import sys as _sys


def _setup() -> None:
    """Inject version-specific vendor directories into sys.path."""
    _root = _os.path.dirname(_os.path.abspath(__file__))
    _cp_tag = "cp{0}{1}".format(_sys.version_info.major, _sys.version_info.minor)
    _cp_dir = _os.path.join(_root, "vendor_" + _cp_tag)
    if _os.path.isdir(_cp_dir) and _cp_dir not in _sys.path:
        _sys.path.insert(0, _cp_dir)
        _dll_dir = _os.path.join(_cp_dir, "pywin32_system32")
        if _os.path.isdir(_dll_dir) and hasattr(_os, "add_dll_directory"):
            try:
                _os.add_dll_directory(_dll_dir)
            except (OSError, FileNotFoundError):
                pass
    _vendor_dir = _os.path.join(_root, "vendor")
    if _os.path.isdir(_vendor_dir) and _vendor_dir not in _sys.path:
        try:
            _sys.path.insert(1, _vendor_dir)
        except IndexError:
            _sys.path.append(_vendor_dir)


_setup()
