"""Root conftest -- mock N.E.K.O SDK and prevent root __init__.py collection."""
import sys
from unittest.mock import MagicMock


class _FakeNekoPluginBase:
    def __init__(self, ctx=None, **_kwargs) -> None:
        self.ctx = ctx
    def enable_file_logging(self, log_level: str = "INFO"):
        return MagicMock()

collect_ignore = [
    "__init__.py",
    "_commit_msg.txt",
    "check_market.py",
    "check_market2.py",
    "check_my_plugins.py",
    "pack_neko_plugin.py",
    "publish_v0.2.0.py",
    "snapshot_detail.py",
    "snapshot_page.py",
]

if "plugin" not in sys.modules:
    mock_plugin_module = MagicMock()
    mock_plugin_module.NekoPluginBase = _FakeNekoPluginBase
    mock_plugin_module.Err = MagicMock()

    sys.modules["plugin"] = MagicMock()
    sys.modules["plugin.sdk"] = MagicMock()
    sys.modules["plugin.sdk.plugin"] = mock_plugin_module
