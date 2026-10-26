"""插件统一配置中心：所有路径、默认值、读写操作的唯一来源。"""

from __future__ import annotations

import json
import logging
import os
import zipfile
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


#  路径常量（模块加载时计算一次，全插件 import 即共享）

PLUGIN_ROOT: Path = Path(__file__).resolve().parent.parent
RESOURCE_DIR: Path = PLUGIN_ROOT / "mod"
DATA_DIR: Path = PLUGIN_ROOT / "data"

# AI 客户端专用（-savedirectory 指向 RESOURCE_DIR 后 tML 自动生成的子目录）
AI_TML_DIR: Path       = RESOURCE_DIR / "tModLoader"
AI_PLAYERS_DIR: Path   = AI_TML_DIR / "Players"
AI_MODS_DIR: Path      = AI_TML_DIR / "Mods"
AI_CONFIG_JSON: Path   = AI_TML_DIR / "config.json"

# 用户配置文件路径
USER_CONFIG_JSON: Path = DATA_DIR / "config" / "user_config.json"

# 主人电脑上 tModLoader 的文档目录（清理 Mod 冲突用）
USER_TML_DIR: str = os.path.join(
    os.path.expandvars(r"%USERPROFILE%"),
    "Documents", "My Games", "Terraria", "tModLoader",
)

# Mod 文件标识
_MOD_FILE: str = "NekoTerrariaLink.tmod"

#  用户可配置项 & 默认值

_USER_KEYS: tuple[str, ...] = (
    "mod_host", "mod_port",
    "server_host", "server_port", "server_password",
    "game_path", "character_name",
    # ── LLM 配置（双 LLM 架构） ──
    "llm_main_provider", "llm_main_model", "llm_main_api_key", "llm_main_base_url",
    "llm_intent_provider", "llm_intent_model", "llm_intent_api_key", "llm_intent_base_url",
    "llm_max_calls_per_minute", "llm_emergency_reserve",
    "context_push_interval_seconds", "context_deep_push_interval_seconds",
    "llm_autonomous_enabled", "llm_think_min_seconds", "llm_think_max_seconds",
    # ── 死亡复活 + 迟滞带跟随 ──
    "follow_trigger_dist", "follow_stop_dist",
    "follow_stick_trigger_dist", "follow_stick_stop_dist",
    "auto_return_after_respawn",
    # ── 运行时调参 ──
    "state_tick_interval_seconds", "state_fallback_poll_seconds",
    "fast_think_interval_seconds",
    "deep_think_min_seconds", "deep_think_max_seconds",
    "system_prompt_interval_seconds",
    # ── 入服后一次性全量同步（enum_items/get_recipes）延迟 ──
    "auto_register_delay_seconds",
)

DEFAULTS: Dict[str, Any] = {
    "mod_host": "127.0.0.1",
    "mod_port": 9877,
    "server_host": "127.0.0.1",
    "server_port": 7777,
    "server_password": "",
    "game_path": "",
    "character_name": "Neko",
    # ── 双 LLM 配置（主 LLM + 意图 LLM） ──
    "llm_main_provider": "",
    "llm_main_model": "",
    "llm_main_api_key": "",
    "llm_main_base_url": "",
    "llm_intent_provider": "",
    "llm_intent_model": "",
    "llm_intent_api_key": "",
    "llm_intent_base_url": "",
    "llm_max_calls_per_minute": 15,
    "llm_emergency_reserve": 3,
    "context_push_interval_seconds": 15.0,
    "context_deep_push_interval_seconds": 180.0,
    "llm_autonomous_enabled": True,
    "llm_think_min_seconds": 120,
    "llm_think_max_seconds": 240,
    # ── 死亡复活 + 迟滞带跟随 ──
    "follow_trigger_dist": 60,
    "follow_stop_dist": 15,
    "follow_stick_trigger_dist": 8,
    "follow_stick_stop_dist": 3,
    "auto_return_after_respawn": True,
    # ── 运行时调参 ──
    "state_tick_interval_seconds": 1.0,
    # 状态兜底轮询间隔（秒）：状态由 mod 推送驱动（game_state 2s），
    # 这里只在推送断/漏时低频补拉，减轻 C# 主线程 200 NPC 扫描负担
    "state_fallback_poll_seconds": 5.0,
    "fast_think_interval_seconds": 5.0,
    "deep_think_min_seconds": 30,
    "deep_think_max_seconds": 90,
    "system_prompt_interval_seconds": 15.0,
    "auto_register_delay_seconds": 60.0,
}


#  角色文件重命名（角色名变更时自动同步）


def _player_dirs() -> List[str]:
    """返回角色文件存放路径（tML 实际读取位置）。"""
    return [str(AI_PLAYERS_DIR)]


def _rename_player_files(old_name: str, new_name: str) -> None:
    """角色名变更时，同步重命名所有可能的 Players 目录下的角色文件。文件可能被运行中的 tML 锁定，重命名失败不影响配置保存。"""
    if old_name == new_name or not old_name or not new_name:
        return

    for pdir in _player_dirs():
        if not os.path.isdir(pdir):
            continue

        for fname in os.listdir(pdir):
            full_src = os.path.join(pdir, fname)

            # 文件：OldName.plr / OldName.tplr / OldName_xxx
            if os.path.isfile(full_src) and (fname == f"{old_name}.plr"
                                              or fname == f"{old_name}.tplr"
                                              or fname.startswith(f"{old_name}_")):
                new_fname = fname.replace(old_name, new_name, 1)
                full_dst = os.path.join(pdir, new_fname)
                if not os.path.exists(full_dst):
                    try:
                        os.rename(full_src, full_dst)
                    except (OSError, PermissionError):
                        pass

            # 目录：OldName/（角色地图数据等）
            if os.path.isdir(full_src) and fname == old_name:
                full_dst = os.path.join(pdir, new_name)
                if not os.path.exists(full_dst):
                    try:
                        os.rename(full_src, full_dst)
                    except (OSError, PermissionError):
                        pass


def _restore_from_backup(name: str) -> bool:
    """从 Backups/ 目录恢复角色文件。支持两种命名格式："""
    backups_dir = AI_PLAYERS_DIR / "Backups"
    if not backups_dir.is_dir():
        return False

    all_zips = sorted(
        backups_dir.glob("*.zip"),
        key=lambda p: os.path.getmtime(str(p)),
        reverse=True,
    )
    if not all_zips:
        return False

    # 优先精确匹配角色名，兜底取最新备份（不管名字）
    target: Path | None = None
    for p in all_zips:
        stem = p.stem
        if stem == name or stem.endswith(f"-{name}"):
            target = p
            break

    if target is None:
        target = all_zips[0]
        logger.info(f"restore_backup: 无精确匹配，兜底使用最新备份 '{target.name}'")
    else:
        logger.info(f"restore_backup: 从 '{target.name}' 恢复角色")

    try:
        with zipfile.ZipFile(target, "r") as zf:
            zf.extractall(str(AI_PLAYERS_DIR))
        return True
    except Exception as e:
        logger.warning(f"restore_backup: 解压失败 {e}")
        return False


def get_character_name() -> str:
    """读取用户配置中的角色名称。"""
    cfg = load_user_config()
    return cfg.get("character_name", "")


def ensure_player_name_matches() -> str:
    """启动前确保 AI 角色目录中有一个与配置 character_name 匹配的 .plr 文件。"""
    name = get_character_name()
    if not name:
        return ""

    target_plr = os.path.join(AI_PLAYERS_DIR, name + ".plr")
    if os.path.isfile(target_plr):
        logger.info(f"ensure_player: 已有匹配角色文件 '{name}.plr'")
        return name

    if not os.path.isdir(AI_PLAYERS_DIR):
        logger.warning(f"ensure_player: AI 角色目录不存在 {AI_PLAYERS_DIR}")
        return ""

    plr_files = sorted(
        f for f in os.listdir(AI_PLAYERS_DIR)
        if f.endswith(".plr") and not f.endswith(".tplr")
    )
    if not plr_files:
        logger.warning("ensure_player: 没有任何 .plr 文件，尝试从 Backups 恢复")
        if not _restore_from_backup(name):
            logger.warning("ensure_player: Backups 中无匹配备份，无法自动选角色")
            return ""
        # 恢复成功，重新扫描
        plr_files = sorted(
            f for f in os.listdir(AI_PLAYERS_DIR)
            if f.endswith(".plr") and not f.endswith(".tplr")
        )
        if not plr_files:
            logger.warning("ensure_player: Backups 恢复后仍未找到 .plr 文件")
            return ""
        # 检查恢复的是否就是目标角色
        if os.path.isfile(target_plr):
            logger.info(f"ensure_player: 从 Backups 恢复了匹配角色 '{name}.plr'")
            return name

    old_name = os.path.splitext(plr_files[0])[0]
    logger.info(
        f"ensure_player: .plr='{plr_files[0]}' 与配置名 '{name}' 不匹配，自动重命名"
    )
    _rename_player_files(old_name, name)
    return name


#  读 / 写 / 工具

def load_user_config() -> Dict[str, Any]:
    """读取用户独立配置文件，缺失项用 DEFAULTS 补齐。"""
    path = USER_CONFIG_JSON
    if not path.exists():
        return dict(DEFAULTS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return dict(DEFAULTS)
        cfg = dict(DEFAULTS)
        for k in _USER_KEYS:
            if k in raw and raw[k] not in (None, ""):
                cfg[k] = raw[k]
        return cfg
    except Exception:
        return dict(DEFAULTS)


def save_user_config(patch: Dict[str, Any]) -> Dict[str, Any]:

    path = USER_CONFIG_JSON
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: Dict[str, Any] = {}
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                existing = parsed
        except Exception:
            existing = {}

    old_name = existing.get("character_name", "")

    merged = dict(existing)
    for k in _USER_KEYS:
        if k in patch:
            merged[k] = patch[k]

    path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    new_name = merged.get("character_name", "")
    _rename_player_files(old_name, new_name)

    return {k: merged.get(k, DEFAULTS[k]) for k in _USER_KEYS}


def config_file_path() -> str:
    return str(USER_CONFIG_JSON)


def clean_human_mods() -> None:

    human_mod = os.path.join(USER_TML_DIR, "Mods", _MOD_FILE)
    if os.path.exists(human_mod):
        os.remove(human_mod)





def mute_ai_client() -> bool:
    """把 AI 客户端 config.json 的音量全设 0，保留主客户端声音。返回 True 表示做了修改。"""
    path = str(AI_CONFIG_JSON)
    if not os.path.isfile(path):
        return False

    try:
        data = json.loads(open(path, encoding="utf-8").read())
        changed = False
        for key in ("VolumeSound", "VolumeAmbient", "VolumeMusic"):
            if data.get(key, 1.0) != 0.0:
                data[key] = 0.0
                changed = True
        if changed:
            open(path, "w", encoding="utf-8").write(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            )
        return changed
    except (OSError, json.JSONDecodeError):
        return False


def set_unfocused_keep_running() -> bool:
    """AI 客户端失焦不暂停/不节流：AutoPause=False + ThrottleWhenInactive=False。

    失焦暂停是"窗口状态影响移动"的官方开关（tML UI 无此设置，启动时直接写
    config.json，与 mute_ai_client 同模式）。返回 True 表示做了修改。
    """
    path = str(AI_CONFIG_JSON)
    if not os.path.isfile(path):
        return False
    try:
        data = json.loads(open(path, encoding="utf-8").read())
        changed = False
        for key, want in (("AutoPause", False), ("ThrottleWhenInactive", False)):
            if data.get(key, True) != want:
                data[key] = want
                changed = True
        if changed:
            open(path, "w", encoding="utf-8").write(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            )
        return changed
    except (OSError, json.JSONDecodeError):
        return False
