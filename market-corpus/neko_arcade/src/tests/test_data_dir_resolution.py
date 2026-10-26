"""回归测试: 插件数据目录解析（N.E.K.O 0.9.0.2 适配）。

背景: 0.9.0.2 把插件代码装进不可变目录(.neko-plugin-installations/plugins/<id>),
安装时会剥掉包内 data/; 用户数据在 SDK storage dir
(%LOCALAPPDATA%\\N.E.K.O\\plugins\\<id>\\data, 可用 NEKO_STORAGE_SELECTED_ROOT 改根)。

若插件仍用 <代码目录>/data 取配置，则 config/help/keywords/emotion 全空 ——
面板表现: 配置框空 + 「无帮助数据」，指令路由关键词丢失(「没听懂」)。
本测试锁定: SDK data_path() 优先、老宿主回退、空目录自动播种默认配置。
"""

from __future__ import annotations

from pathlib import Path

from plugin.plugins.neko_arcade.core.config_manager import ConfigManager, resolve_data_dir

ROOT = Path(__file__).resolve().parent.parent


class _NewHost:
    """0.9.0.2 宿主: SDK 提供 data_path()。"""

    def __init__(self, data_dir: Path) -> None:
        self._data = data_dir
        # 代码目录(不可变安装目录), 故意不带 data/
        self.config_dir = data_dir.parent / "installations" / "plugins" / "neko_arcade"

    def data_path(self, *parts: str) -> Path:
        base = self._data
        return base.joinpath(*parts) if parts else base


class _OldHost:
    """老宿主 / 本地开发: 无 data_path(), 只有 config_dir。"""

    def __init__(self, plugin_root: Path) -> None:
        self.config_dir = plugin_root


def test_sdk_data_path_takes_priority(tmp_path: Path) -> None:
    data_dir = tmp_path / "storage" / "data"
    data_dir.mkdir(parents=True)
    assert Path(resolve_data_dir(_NewHost(data_dir))) == data_dir


def test_falls_back_to_code_dir_without_sdk_api() -> None:
    assert Path(resolve_data_dir(_OldHost(ROOT))) == ROOT / "data"


def test_fresh_data_dir_gets_seeded_defaults(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    cm = ConfigManager(str(data_dir))
    assert (data_dir / "config" / "xiuxian" / "help.json").is_file()
    assert len(cm.get_game_ids()) >= 10


def test_config_help_keywords_available_from_sdk_data_dir(tmp_path: Path) -> None:
    """核心回归: 面板配置值 / 帮助指令 / 路由关键词都必须取得到。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    cm = ConfigManager(str(data_dir))
    gc = cm.load("xiuxian")
    assert gc.config, "配置为空 → 面板配置框会显示空白"
    assert gc.help.get("commands"), "帮助为空 → 面板显示「无帮助数据」"
    assert gc.get_keywords(), "关键词为空 → 指令路由失效(没听懂)"
    assert gc.emotion_templates, "情感模板为空 → 猫娘语气降级"


def test_existing_user_data_is_not_overwritten(tmp_path: Path) -> None:
    """已有用户数据不能被播种覆盖(用户改过的值必须保留)。"""
    data_dir = tmp_path / "data"
    game_dir = data_dir / "config" / "xiuxian"
    game_dir.mkdir(parents=True)
    (game_dir / "config.json").write_text('{"breakthrough": {"base_rate": 0.33}}', encoding="utf-8")
    cm = ConfigManager(str(data_dir))
    gc = cm.load("xiuxian")
    assert gc.config["breakthrough"]["base_rate"] == 0.33


def test_partial_user_data_gets_missing_content_seeded(tmp_path: Path) -> None:
    """用户数据目录已存在、但缺 help/keywords → 升级时必须补齐(修「帮助不渲染」)。"""
    data_dir = tmp_path / "data"
    game_dir = data_dir / "config" / "xiuxian"
    game_dir.mkdir(parents=True)
    (game_dir / "config.json").write_text('{"mine": 1}', encoding="utf-8")
    cm = ConfigManager(str(data_dir))
    gc = cm.load("xiuxian")
    assert gc.config["mine"] == 1, "用户 config.json 必须保留"
    assert gc.help.get("commands"), "缺失的 help.json 必须被补上"
    assert gc.get_keywords(), "缺失的 keywords.json 必须被补上"
    assert gc.emotion_templates, "缺失的 emotion.json 必须被补上"


def test_stale_shipped_metadata_is_refreshed_config_kept(tmp_path: Path) -> None:
    """出厂内容(help/keywords/emotion)随升级刷新；用户 config.json 不动。"""
    data_dir = tmp_path / "data"
    game_dir = data_dir / "config" / "xiuxian"
    game_dir.mkdir(parents=True)
    (game_dir / "config.json").write_text('{"mine": 1}', encoding="utf-8")
    (game_dir / "help.json").write_text('{"text": "old", "commands": []}', encoding="utf-8")
    cm = ConfigManager(str(data_dir))
    gc = cm.load("xiuxian")
    assert gc.config["mine"] == 1, "config.json 不能被出厂内容覆盖"
    assert len(gc.help.get("commands") or []) > 5, "旧 help.json 必须被出厂版刷新"


def test_empty_local_help_does_not_blank_the_game(tmp_path: Path) -> None:
    """即使本地 help/keywords 是空文件, 也要回读出厂版, 不能给用户空白帮助。"""
    data_dir = tmp_path / "data"
    game_dir = data_dir / "config" / "xiuxian"
    game_dir.mkdir(parents=True)
    (game_dir / "config.json").write_text('{"mine": 1}', encoding="utf-8")
    (game_dir / "help.json").write_text('{}', encoding="utf-8")
    (game_dir / "keywords.json").write_text('[]', encoding="utf-8")
    cm = ConfigManager(str(data_dir))
    gc = cm.load("xiuxian")
    assert gc.config["mine"] == 1
    assert gc.help.get("commands"), "空 help.json 不能导致帮助为空"
    assert gc.get_keywords(), "空 keywords.json 不能导致路由关键词为空"
