from __future__ import annotations

from types import SimpleNamespace

import pytest
from market_plugins.galgame_plugin.ocr_capture_backends import pyautogui as backend_module

PyAutoGuiCaptureBackend = backend_module.PyAutoGuiCaptureBackend


pytestmark = pytest.mark.plugin_unit


def test_pyautogui_availability_requires_importable_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import_module = backend_module.importlib.import_module

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "pyautogui":
            raise RuntimeError("headless display")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(backend_module.importlib, "import_module", guarded_import)
    logger = SimpleNamespace(debug_calls=[])
    logger.debug = lambda *args: logger.debug_calls.append(args)
    backend = PyAutoGuiCaptureBackend(logger=logger)

    assert backend.is_available() is False
    assert backend.availability_error == "headless display"
    assert len(logger.debug_calls) == 1

    assert backend.is_available() is False
    assert len(logger.debug_calls) == 1


def test_pyautogui_availability_accepts_importable_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported: list[str] = []
    monkeypatch.setattr(
        backend_module.importlib,
        "import_module",
        lambda name: imported.append(name) or SimpleNamespace(),
    )

    backend = PyAutoGuiCaptureBackend()

    assert backend.is_available() is True
    assert imported == ["pyautogui", "PIL.ImageGrab"]
    assert backend.availability_error == ""
