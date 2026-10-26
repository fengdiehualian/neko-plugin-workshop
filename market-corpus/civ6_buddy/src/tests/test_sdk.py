"""civ6_sdk 纯逻辑测试（不触发 Steam、不碰真实配置目录，CI 可跑）。

覆盖：候选 Steam 库探测、AppOptions 定位、Tuner 配置幂等修复（备份/创建/跳过）、
链路状态汇总结构。触发安装（steam://）不在此测试（会拉起 Steam）。
"""

import importlib

_sdk = importlib.import_module("civ6_sdk")


def test_candidate_steam_libs_is_list():
    libs = _sdk.candidate_steam_libs()
    assert isinstance(libs, list)
    assert all(isinstance(x, str) for x in libs)


def test_app_options_path_shape(tmp_path):
    p = _sdk.app_options_path(docs=tmp_path)
    assert str(p).endswith("AppOptions.txt")
    assert "Sid Meier's Civilization VI" in str(p)


def test_tuner_enabled_missing_file(tmp_path):
    p = _sdk.app_options_path(docs=tmp_path)
    assert _sdk.tuner_enabled(p) is False


def test_ensure_tuner_enabled_creates(tmp_path):
    p = _sdk.app_options_path(docs=tmp_path)
    r = _sdk.ensure_tuner_enabled(p)
    assert r["changed"] is True
    assert p.is_file()
    assert _sdk.tuner_enabled(p) is True
    # 幂等：再跑一次不应再改
    r2 = _sdk.ensure_tuner_enabled(p)
    assert r2["changed"] is False


def test_ensure_tuner_enabled_flips_zero(tmp_path):
    p = _sdk.app_options_path(docs=tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[Debug]\nEnableTuner 0\n", encoding="utf-8")
    r = _sdk.ensure_tuner_enabled(p)
    assert r["changed"] is True
    assert r["backup"] is not None
    import os
    assert os.path.exists(r["backup"])
    assert _sdk.tuner_enabled(p) is True


def test_ensure_tuner_enabled_keeps_one(tmp_path):
    p = _sdk.app_options_path(docs=tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[Debug]\nEnableTuner 1\n", encoding="utf-8")
    r = _sdk.ensure_tuner_enabled(p)
    assert r["changed"] is False
    assert r["detail"] is not None


def test_ensure_tuner_enabled_appends_missing_line(tmp_path):
    p = _sdk.app_options_path(docs=tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[UserSettings]\nSomeOption 1\n", encoding="utf-8")
    r = _sdk.ensure_tuner_enabled(p)
    assert r["changed"] is True
    assert _sdk.tuner_enabled(p) is True
    text = p.read_text(encoding="utf-8")
    assert "EnableTuner 1" in text
    assert "SomeOption 1" in text  # 原有内容保留


def test_sdk_status_shape():
    st = _sdk.sdk_status()
    for key in ("sdk_installed", "sdk_dir", "game_dir", "app_options_path",
                "app_options_exists", "tuner_enabled", "port_4318_listening",
                "game_running"):
        assert key in st, f"缺 {key}"
    assert isinstance(st["sdk_installed"], bool)


def test_sdk_constants():
    assert _sdk.SDK_APPID == 597260
    assert _sdk.TUNER_PORT == 4318


def test_sdk_dir_matches_assets_variant(tmp_path):
    """实际下载的目录名是 'Sid Meier's Civilization VI SDK Assets'（非精确名），必须能识别。"""
    lib = tmp_path / "SteamLibrary"
    common = lib / "steamapps" / "common"
    sdk = common / "Sid Meier's Civilization VI SDK Assets"
    sdk.mkdir(parents=True)
    (sdk / "Civ6").mkdir()
    (sdk / "civ6-asset-deps.json").write_text("{}", encoding="utf-8")
    got = _sdk.sdk_dir(steam_libs=[str(lib)])
    assert got is not None
    assert "SDK Assets" in got


def test_sdk_dir_none_when_absent(tmp_path):
    assert _sdk.sdk_dir(steam_libs=[str(tmp_path)]) is None


def test_sdk_dir_skips_empty_dir(tmp_path):
    """目录存在但为空（下载未完成）不算已安装。"""
    lib = tmp_path / "Lib"
    sdk = lib / "steamapps" / "common" / "Sid Meier's Civilization VI SDK"
    sdk.mkdir(parents=True)
    assert _sdk.sdk_dir(steam_libs=[str(lib)]) is None
