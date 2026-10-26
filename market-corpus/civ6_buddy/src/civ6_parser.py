"""civ6_buddy 存档解析：扫描存档目录、调用 node 解析器、整理为中文局面。

解析器为插件自带 civ6_save_parser（pydt/civ6-save-parser 的 JSON wrapper），
依赖本机 node（>=16）。调用失败时如实返回错误，不编造局面。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

try:
    from . import civ6_data
except ImportError:  # 包外直接加载（测试/独立脚本）
    import civ6_data  # type: ignore

_logger = logging.getLogger("N.E.K.O.Plugin_civ6_buddy.civ6_parser")

# 默认存档目录（用户实测路径；可在 config 覆盖）
DEFAULT_SAVES_DIR = os.path.join(
    os.path.expanduser("~"), "Documents", "My Games", "Sid Meier's Civilization VI", "Saves"
)

# 插件内嵌解析器位置（相对本文件）
_PARSER_DIR = Path(__file__).resolve().parent / "civ6_save_parser"
_WRAPPER = _PARSER_DIR / "wrapper.js"

_PARSE_TIMEOUT = 60.0  # 解析单存档最长等待（秒）


class Civ6Error(Exception):
    """插件统一错误。code 供工具输出错误码。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _resolve_saves_dir(custom: str = "") -> str:
    if custom and custom.strip():
        return custom.strip()
    return DEFAULT_SAVES_DIR


def list_saves(saves_dir: str = "") -> list[dict]:
    """递归扫描存档目录，返回按修改时间倒序的存档列表。"""
    root = _resolve_saves_dir(saves_dir)
    if not os.path.isdir(root):
        raise Civ6Error("SAVES_DIR_NOT_FOUND", f"存档目录不存在：{root}")
    out: list[dict] = []
    for p in sorted(Path(root).rglob("*.Civ6Save"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            st = p.stat()
        except OSError:
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        out.append({
            "path": str(p),
            "rel": rel,
            "size": st.st_size,
            "mtime": st.st_mtime,
            "mtime_text": _fmt_time(st.st_mtime),
            "kind": "auto" if "/auto/" in rel or rel.startswith("auto") else "manual",
        })
    return out


def _fmt_time(ts: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def find_save(target: str = "", saves_dir: str = "") -> dict:
    """按名字/相对路径/自动最新 定位存档。target 支持：文件名、含 'AutoSave' 的序号、关键字。"""
    saves = list_saves(saves_dir)
    if not saves:
        raise Civ6Error("NO_SAVES", "未找到任何 .Civ6Save 存档。请先打开文明6新建游戏并保存/触发自动存档。")
    t = (target or "").strip()
    if not t:
        return saves[0]  # 默认最新
    # 精确相对路径
    for s in saves:
        if s["rel"] == t or s["path"] == t or os.path.basename(s["path"]).lower() == t.lower():
            return s
    # 关键字匹配（不区分大小写）
    tl = t.lower()
    for s in saves:
        if tl in s["rel"].lower():
            return s
    raise Civ6Error("SAVE_NOT_FOUND", f"未找到匹配的存档「{t}」。可用存档：\n" + "\n".join(f"- {s['rel']}（{s['mtime_text']}）" for s in saves[:15]))


def parse_save(target: str = "", saves_dir: str = "") -> dict:
    """定位并解析存档，返回整理后的局面 dict。"""
    found = find_save(target, saves_dir)
    raw = _run_parser(found["path"])
    return _build_situation(raw, found)


def self_check(saves_dir: str = "") -> dict:
    """启动自检（只读）：解析器与 node 是否可用、存档目录是否可达。"""
    if not _WRAPPER.is_file():
        raise Civ6Error("PARSER_MISSING", f"插件内嵌解析器缺失：{_WRAPPER}")
    try:
        subprocess.run(["node", "-v"], capture_output=True, text=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        raise Civ6Error("NODE_MISSING", "未找到 node。请先安装 Node.js（>=16）后重启插件。")
    return {"status": "ok", "saves_dir": _resolve_saves_dir(saves_dir)}


def _run_parser(save_path: str) -> dict:
    """调用 node wrapper 解析存档 → 原始 JSON dict。

    实现说明（v0.4.8）：插件进程在打包环境（resources\\bin）下偶发 stdout=None，
    故改用 Popen + stdout 写入临时文件，完全不依赖父进程的 stdout 管道；
    失败时把 returncode / stderr 写入日志，便于定位。
    """
    if not _WRAPPER.is_file():
        raise Civ6Error("PARSER_MISSING", f"插件内嵌解析器缺失：{_WRAPPER}")
    import tempfile
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".civ6parse.json", prefix="civ6_")
        out_fh = os.fdopen(fd, "w", encoding="utf-8", newline="")
        try:
            proc = subprocess.Popen(
                ["node", str(_WRAPPER), save_path, "--simple"],
                stdout=out_fh,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            _, err_text = proc.communicate(timeout=_PARSE_TIMEOUT)
        finally:
            out_fh.close()
    except FileNotFoundError:
        raise Civ6Error("NODE_MISSING", "未找到 node。请先安装 Node.js（>=16）后重启插件。")
    except subprocess.TimeoutExpired:
        raise Civ6Error("PARSE_TIMEOUT", f"解析存档超时（>{int(_PARSE_TIMEOUT)}s）：{save_path}")
    if proc.returncode != 0:
        err = (err_text or "").strip() or f"exit {proc.returncode}"
        raise Civ6Error("PARSE_FAILED", f"解析存档失败：{err[:400]}")
    try:
        with open(tmp_path, encoding="utf-8") as fh:
            raw_text = fh.read()
    except OSError as exc:
        raise Civ6Error("PARSE_OUTPUT_UNREADABLE", f"读取解析结果失败：{exc}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    raw_text = (raw_text or "").strip()
    if not raw_text:
        raise Civ6Error(
            "PARSE_EMPTY_OUTPUT",
            f"解析器无输出（exit 0）：{(err_text or '')[:400]}",
        )
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise Civ6Error("PARSE_OUTPUT_INVALID", f"解析器输出非 JSON（{exc}），前 200 字符：{raw_text[:200]}")


def _build_situation(raw: dict, found: dict) -> dict:
    """原始解析 JSON → 中文局面摘要数据结构。"""
    civs = raw.get("CIVS") or []
    actors = raw.get("ACTORS") or []

    humans: list[dict] = []
    ais: list[dict] = []
    for c in civs:
        entry = {
            "id": c.get("ACTOR_NAME", ""),
            "leader_id": c.get("LEADER_NAME", ""),
            "name": civ6_data.civ_name(c.get("ACTOR_NAME", "")),
            "leader": civ6_data.leader_name(c.get("ACTOR_NAME", ""), c.get("LEADER_NAME", "")),
            "alive": bool(c.get("PLAYER_ALIVE", False)),
            "is_current_turn": bool(c.get("IS_CURRENT_TURN", False)),
            "ai_human": c.get("ACTOR_AI_HUMAN", 1),
            "player_name": c.get("PLAYER_NAME", ""),
        }
        if entry["ai_human"] == 3:
            humans.append(entry)
        else:
            ais.append(entry)

    city_states = [
        {
            "id": a.get("ACTOR_NAME", ""),
            "name": civ6_data.civ_name(a.get("ACTOR_NAME", "")),
            "leader": civ6_data.leader_name(a.get("ACTOR_NAME", ""), a.get("LEADER_NAME", "")),
            "ai_human": a.get("ACTOR_AI_HUMAN", 1),
        }
        for a in actors
        if a.get("ACTOR_TYPE") == "CIVILIZATION_LEVEL_CITY_STATE"
    ]
    barbarians = sum(1 for a in actors if a.get("ACTOR_TYPE") == "CIVILIZATION_LEVEL_TRIBE")

    # MOD 标题提取（含 zh_Hans_CN 本地化或 en_US 兜底）
    mods: list[str] = []
    seen_mod_ids: set[str] = set()
    for key in sorted(raw.keys()):
        if not key.startswith("MOD_BLOCK_"):
            continue
        block = raw[key]
        if isinstance(block, list):
            for item in block:
                if not isinstance(item, dict):
                    continue
                mid = item.get("MOD_ID", "")
                if mid in seen_mod_ids:
                    continue
                seen_mod_ids.add(mid)
                title = _extract_mod_title(item.get("MOD_TITLE", ""))
                if title:
                    mods.append(title)

    game_turn = raw.get("GAME_TURN")
    return {
        "save": found,
        "game_turn": int(game_turn) if isinstance(game_turn, (int, float)) else None,
        "game_speed_id": raw.get("GAME_SPEED", ""),
        "game_speed": civ6_data.GAME_SPEEDS.get(raw.get("GAME_SPEED", ""), raw.get("GAME_SPEED", "")),
        "map_size_id": raw.get("MAP_SIZE", ""),
        "map_size": civ6_data.MAP_SIZES.get(raw.get("MAP_SIZE", ""), raw.get("MAP_SIZE", "")),
        "map_file": raw.get("MAP_FILE", ""),
        "map_name": civ6_data.MAP_FILES.get(raw.get("MAP_FILE", ""), raw.get("MAP_FILE", "")),
        "humans": humans,
        "ais": ais,
        "city_states": city_states,
        "barbarian_count": barbarians,
        "mods": mods,
    }


def _extract_mod_title(raw_title) -> str:
    """MOD_TITLE 可能为 LOC 引用 JSON 或纯字符串，提取可读标题。"""
    if not raw_title:
        return ""
    s = raw_title if isinstance(raw_title, str) else str(raw_title)
    if not s.strip().startswith("{"):
        return s.strip()
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s.strip()
    if isinstance(obj, dict):
        # {LOC_KEY: [{locale, text}, ...]}
        for key, val in obj.items():
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict) and item.get("locale") in ("zh_Hans_CN", "zh_CN", "en_US"):
                        return item.get("text", "")
                for item in val:
                    if isinstance(item, dict) and item.get("text"):
                        return item["text"]
            # 无任何文本时把 LOC key 可读化兜底
            readable = key.replace("LOC_", "").replace("_TITLE", "").replace("_NAME", "").replace("_", " ").strip()
            if readable:
                return f"{readable}（MOD）"
    return s.strip()


def situation_from_live(live: dict) -> dict:
    """Tuner 实时数据 → 局面 dict（兼容 format_situation；无存档信息）。"""
    civ = live.get("civ") or ""
    leader = civ6_data.leader_name("", "")
    humans: list[dict] = []
    if civ:
        humans.append({
            "id": "",
            "leader_id": "",
            "name": civ,
            "leader": leader,
            "alive": True,
            "is_current_turn": True,
            "ai_human": 3,
            "player_name": "",
        })
    return {
        "save": None,
        "via": "tuner",
        "game_turn": live.get("turn"),
        "game_speed_id": "",
        "game_speed": "实时",
        "map_size_id": "",
        "map_size": "实时",
        "map_file": "",
        "map_name": "实时",
        "humans": humans,
        "ais": [],
        "city_states": [],
        "barbarian_count": 0,
        "mods": [],
        "cities": live.get("cities", 0),
        "current_research": live.get("tech", ""),
        "player": live.get("player", -1),
    }


def format_situation(sit: dict) -> str:
    """局面 dict → 中文可读文本（工具 output 用）。"""
    if not sit:
        return "暂无局面数据。"
    lines: list[str] = []
    turn = sit.get("game_turn")
    speed = sit.get("game_speed") or sit.get("game_speed_id") or "未知"
    msize = sit.get("map_size") or sit.get("map_size_id") or "未知"
    mname = sit.get("map_name") or sit.get("map_file") or "未知"
    lines.append(f"📊 当前局面：第 {turn if turn is not None else '?'} 回合（{speed}速度，{msize}，地图：{mname}）")
    save = sit.get("save")
    if save:
        lines.append(f"存档：{save.get('rel', '')}（{save.get('mtime_text', '')}，{save.get('size', 0)//1024}KB）")
    elif sit.get("via") == "tuner":
        lines.append("数据来源：FireTuner 实时读取（游戏内即时状态）")
    else:
        lines.append("数据来源：存档解析")

    humans = sit.get("humans") or []
    if humans:
        for h in humans:
            lines.append(f"👤 你的文明：{h['name']}（{h['leader']}）")
            ua = civ6_data.civ_ua(h.get("id", ""))
            if ua:
                lines.append(f"　特性：{ua}")
    else:
        lines.append("👤 你的文明：（存档中未识别到人类玩家）")

    ais = sit.get("ais") or []
    if ais:
        lines.append(f"🤖 敌对文明（{len(ais)}）：" + "、".join(f"{a['name']}（{a['leader']}）" for a in ais))

    css = sit.get("city_states") or []
    if css:
        lines.append(f"🏙️ 城邦（{len(css)}）：" + "、".join(c["name"] for c in css))
    if sit.get("barbarian_count"):
        lines.append(f"⚠️ 蛮族营地：{sit['barbarian_count']} 处")

    mods = sit.get("mods") or []
    if mods:
        lines.append("🧩 MOD：" + "、".join(mods))

    # 玩家文明特性建议提示
    if humans:
        h0 = humans[0]
        ua = civ6_data.civ_ua(h0.get("id", ""))
        if ua:
            lines.append(f"💡 陪玩提示：{h0['name']}的{ua}")
    return "\n".join(lines)
