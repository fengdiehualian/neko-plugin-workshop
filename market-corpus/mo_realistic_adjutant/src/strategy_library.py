from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from .strategy_blocks import BlockCompileError, compile_program
from .strategy_atomic_migration import (
    FORCE_RATIO_ZH, TERRAIN_ZH, clean_atomic_payload, doctrine_from_atomic_program,
    repair_atomic_selectors,
)


SCHEMA_VERSION = 1
CATEGORY_REVISION = 5
ACTIVE_STATUSES = {"draft", "experimental", "stable", "recommended"}
PERMANENT_STATUSES = {"stable", "recommended", "deprecated"}
TEMPORARY_STATUSES = {"draft", "experimental", "paused"}

DEFAULT_CATEGORIES = (
    "战役", "战役/官方任务", "战役/官方任务/盟军", "战役/官方任务/苏联",
    "战役/官方任务/厄普西隆", "战役/官方任务/焚风",
    "战役/官方任务/具体任务", "战役/官方任务/具体任务/开局",
    "战役/官方任务/具体任务/阶段目标", "战役/官方任务/具体任务/特殊限制",
    "战役/官方任务/具体任务/通关策略", "战役/合作战役", "战役/自定义战役",
    "战役/未识别任务",
    "遭遇战", "遭遇战/盟军", "遭遇战/盟军/美国", "遭遇战/盟军/欧洲联盟",
    "遭遇战/盟军/太平洋阵线", "遭遇战/苏联", "遭遇战/苏联/苏俄",
    "遭遇战/苏联/拉丁同盟", "遭遇战/苏联/中国", "遭遇战/厄普西隆",
    "遭遇战/厄普西隆/心灵军团", "遭遇战/厄普西隆/厄普西隆总部",
    "遭遇战/厄普西隆/天蝎组织", "遭遇战/焚风", "遭遇战/焚风/焚风子阵营一",
    "遭遇战/焚风/焚风子阵营二", "遭遇战/焚风/焚风子阵营三",
    "多人对战", "多人对战/战役合作", "多人对战/玩家对战", "多人对战/玩家对战/1对1",
    "多人对战/玩家对战/2对2", "多人对战/玩家对战/3对3", "多人对战/玩家对战/4对4",
    "多人对战/混战", "多人对战/混战/三人", "多人对战/混战/四人",
    "多人对战/混战/五人至八人", "多人对战/人机混合", "多人对战/特殊规则",
    "通用战术", "通用战术/经济与扩张", "通用战术/经济", "通用战术/生产与科技",
    "通用战术/生产", "通用战术/科技", "通用战术/扩张", "通用战术/移动与集结",
    "通用战术/移动", "通用战术/集结", "通用战术/编队与装载", "通用战术/编队",
    "通用战术/装载", "通用战术/部署与驻守", "通用战术/部署", "通用战术/基地防御",
    "通用战术/防御", "通用战术/接触战", "通用战术/进攻与撤退", "通用战术/进攻",
    "通用战术/撤退", "通用战术/空军", "通用战术/海军", "通用战术/支援技能",
    "通用战术/超级武器", "通用战术/维修与保护", "通用战术/维修", "通用战术/护矿",
    "通用战术/占领", "通用战术/侦察", "通用战术/单位专属反制",
    "游戏适配", "游戏适配/心灵终结", "游戏适配/红色警戒2原版",
    "游戏适配/尤里的复仇", "游戏适配/其他模组", "猫娘学习", "猫娘学习/临时方案",
)

ZH_LABELS = {
    "allied": "盟军", "soviet": "苏联", "epsilon": "厄普西隆", "foehn": "焚风",
    "opening": "开局", "tier2": "二级科技", "tier3": "三级科技", "late": "后期",
    "sudden_death": "决战阶段", "behind": "兵力落后", "even": "兵力均势", "ahead": "兵力占优",
    "open": "开阔地", "chokepoint": "狭窄口", "balanced_force": "综合部队",
    "infantry_swarm": "步兵海", "heavy_armor": "重装甲", "light_armor": "轻装甲",
    "air_swarm": "空军群", "naval_surface": "水面舰队", "static_defense": "基地防御",
    "superweapon": "超级武器", "artillery": "炮兵", "siege": "攻城单位",
    "bomber_strike": "轰炸机", "helicopter_raid": "直升机袭击", "transport_drop": "运输空降",
    "stealth_infiltration": "隐形渗透", "mind_control": "心灵控制", "economy_expansion": "经济扩张",
    "power_emergency": "电力危机", "submarine": "潜艇", "area_anti_infantry": "范围反步兵",
    "unknown": "未识别", "early rush": "早期突袭", "early_rush": "早期突袭",
    "surplus": "资源富余", "deficit": "资源不足", "transport_drop": "运输空降",
    "air_drop": "空降", "rush": "快速突袭", "counter_attack": "反击",
}

FACTION_CATEGORY_PATHS = {
    "allied": ("遭遇战/盟军", "遭遇战/盟军/美国", "遭遇战/盟军/欧洲联盟", "遭遇战/盟军/太平洋阵线"),
    "soviet": ("遭遇战/苏联", "遭遇战/苏联/苏俄", "遭遇战/苏联/拉丁同盟", "遭遇战/苏联/中国"),
    "epsilon": ("遭遇战/厄普西隆", "遭遇战/厄普西隆/心灵军团", "遭遇战/厄普西隆/厄普西隆总部", "遭遇战/厄普西隆/天蝎组织"),
    "foehn": ("遭遇战/焚风", "遭遇战/焚风/焚风子阵营一", "遭遇战/焚风/焚风子阵营二", "遭遇战/焚风/焚风子阵营三"),
}
OWNER_CATEGORY_PATHS = {
    "UnitedStates": "遭遇战/盟军/美国", "Europeans": "遭遇战/盟军/欧洲联盟",
    "Pacific": "遭遇战/盟军/太平洋阵线", "USSR": "遭遇战/苏联/苏俄",
    "Latin": "遭遇战/苏联/拉丁同盟", "Chinese": "遭遇战/苏联/中国",
    "PsiCorps": "遭遇战/厄普西隆/心灵军团", "Headquaters": "遭遇战/厄普西隆/厄普西隆总部",
    "ScorpionCell": "遭遇战/厄普西隆/天蝎组织", "Guild1": "遭遇战/焚风/焚风子阵营一",
    "Guild2": "遭遇战/焚风/焚风子阵营二", "Guild3": "遭遇战/焚风/焚风子阵营三",
}
AIR_THREATS = {"air_swarm", "bomber_strike", "helicopter_raid", "anti_air_net", "transport_drop"}
NAVAL_THREATS = {"naval_surface", "submarine", "amphibious_landing"}
DEFENSE_THREATS = {"early_rush", "harvester_raid", "transport_drop", "stealth_infiltration", "mind_control"}


@lru_cache(maxsize=1)
def _catalog_records() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(Path(__file__).with_name("unit_catalog.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    units = raw.get("units") if isinstance(raw, dict) else {}
    return {str(key).upper(): value for key, value in (units or {}).items() if isinstance(value, dict)}

CORE_STRATEGIES: tuple[dict[str, Any], ...] = (
    {
        "id": "core_gi_deployment_posture", "name_zh": "盟军大兵接敌部署与清场解除部署",
        "category_path": "通用战术/部署与驻守", "strategy_type": "action_flow",
        "priority": 80, "weight": 70, "risk_level": "low",
        "trigger": {"unit_type_ids": ["E1", "GGI"], "enemy_within_cells": 8},
        "team": {"roles": ["deployable_infantry"], "maximum_instances": 8},
        "flow": ["验证部署能力", "近距离接敌时部署", "目标过远时解除部署", "清场后恢复站立"],
        "verification": ["is_deployed状态符合期望", "目标消失后单位恢复机动"],
    },
    {
        "id": "core_harvester_noncombat", "name_zh": "采矿车永不加入战斗编组",
        "category_path": "通用战术/维修与保护", "strategy_type": "safety_policy",
        "priority": 100, "weight": 5000, "risk_level": "low",
        "trigger": {"unit_role": "resource_gatherer"},
        "constraints": ["禁止攻击", "禁止战斗警戒", "仅在真实威胁附近撤离", "威胁消失后恢复采矿"],
    },
    {
        "id": "core_base_target_layers", "name_zh": "敌方基地分层攻击顺序",
        "category_path": "通用战术/进攻与撤退", "strategy_type": "target_policy",
        "priority": 85, "weight": 70, "risk_level": "medium",
        "flow": ["清理基地周围作战单位", "攻击防御建筑", "按距离攻击生产与科技建筑", "最后攻击建造场"],
    },
    {
        "id": "core_aircraft_reload_release", "name_zh": "战机装弹期间释放控制权",
        "category_path": "通用战术/空军", "strategy_type": "safety_policy",
        "priority": 95, "weight": 500, "risk_level": "low",
        "constraints": ["弹药未满时不重复攻击", "停机场上不重复下达返回命令", "装弹完成后才重新出击"],
    },
    {
        "id": "core_independent_production_lanes", "name_zh": "六条生产线独立并行",
        "category_path": "通用战术/生产与科技", "strategy_type": "resource_policy",
        "priority": 100, "weight": 5000, "risk_level": "low",
        "constraints": ["建筑", "支援与插件", "步兵", "装甲", "空军", "海军"],
    },
)


class _ManagedConnection:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        self._connection.__enter__()
        return self._connection

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        try:
            return bool(self._connection.__exit__(exc_type, exc_value, traceback))
        finally:
            self._connection.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode(value: Any, fallback: Any) -> Any:
    if not isinstance(value, str):
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


class StrategyLibrary:
    """SQLite-backed source of truth for permanent and catgirl-learned strategies."""

    def __init__(self, path: Path | str | None = None):
        base = Path(__file__).resolve().parent
        self.path = Path(path) if path is not None else base / "data" / "strategy_library.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.initialize()

    def _connect(self) -> _ManagedConnection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return _ManagedConnection(connection)

    def initialize(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS categories(
                    path TEXT PRIMARY KEY,
                    display_order INTEGER NOT NULL DEFAULT 0,
                    builtin INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS strategies(
                    id TEXT PRIMARY KEY,
                    name_zh TEXT NOT NULL,
                    category_path TEXT NOT NULL,
                    strategy_type TEXT NOT NULL,
                    library_scope TEXT NOT NULL CHECK(library_scope IN ('permanent','temporary')),
                    status TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    usable_in_match INTEGER NOT NULL DEFAULT 1,
                    priority INTEGER NOT NULL DEFAULT 50,
                    weight REAL NOT NULL DEFAULT 50,
                    risk_level TEXT NOT NULL DEFAULT 'medium',
                    author TEXT NOT NULL DEFAULT '',
                    source_kind TEXT NOT NULL DEFAULT 'player',
                    source_text TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL
                );
                CREATE INDEX IF NOT EXISTS strategies_scope_status
                    ON strategies(library_scope,status,enabled,usable_in_match);
                CREATE INDEX IF NOT EXISTS strategies_category ON strategies(category_path);
                CREATE TABLE IF NOT EXISTS strategy_categories(
                    strategy_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    PRIMARY KEY(strategy_id,path)
                );
                CREATE INDEX IF NOT EXISTS strategy_categories_path
                    ON strategy_categories(path,strategy_id);
                CREATE TABLE IF NOT EXISTS strategy_history(
                    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    operation TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    changed_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_results(
                    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    failure_layer TEXT NOT NULL DEFAULT '',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    recorded_at REAL NOT NULL
                );
                """
            )
            db.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )
            db.executemany(
                "INSERT OR IGNORE INTO categories(path,display_order,builtin) VALUES(?,?,1)",
                ((path, index) for index, path in enumerate(DEFAULT_CATEGORIES)),
            )
            self._upgrade_category_tree(db)
            linked = db.execute("SELECT COUNT(*) FROM strategy_categories").fetchone()[0]
            if not linked and db.execute("SELECT COUNT(*) FROM strategies WHERE status!='deleted'").fetchone()[0]:
                self._rebuild_category_links(db)

    def _upgrade_category_tree(self, db: sqlite3.Connection) -> None:
        marker = db.execute("SELECT value FROM metadata WHERE key='category_revision'").fetchone()
        if marker and int(marker[0]) >= CATEGORY_REVISION:
            return
        replacements = {
            "遭遇战/焚风/Guild1": "遭遇战/焚风/焚风子阵营一",
            "遭遇战/焚风/Guild2": "遭遇战/焚风/焚风子阵营二",
            "遭遇战/焚风/Guild3": "遭遇战/焚风/焚风子阵营三",
            "多人对战/PVP": "多人对战/玩家对战",
            "多人对战/PVP/1V1": "多人对战/玩家对战/1对1",
            "多人对战/PVP/2V2": "多人对战/玩家对战/2对2",
            "多人对战/PVP/3V3": "多人对战/玩家对战/3对3",
            "多人对战/PVP/4V4": "多人对战/玩家对战/4对4",
        }
        for old_path, new_path in replacements.items():
            db.execute("UPDATE strategies SET category_path=?,updated_at=? WHERE category_path=?", (new_path, time.time(), old_path))
            db.execute("INSERT OR IGNORE INTO categories(path,display_order,builtin) VALUES(?,9999,1)", (new_path,))
        rows = db.execute(
            "SELECT id,strategy_type,payload_json FROM strategies WHERE source_kind='combat_doctrine'"
        ).fetchall()
        for row in rows:
            payload = _decode(row["payload_json"], {})
            conditions = payload.get("conditions") if isinstance(payload.get("conditions"), dict) else {}
            if row["strategy_type"] == "combat_matrix":
                name = self._matrix_name(conditions)
                path = self._matrix_category(conditions, str(payload.get("posture") or ""))
            else:
                enemy = payload.get("enemy_name") or payload.get("enemy_type_id") or "未知单位"
                name = "单位反制：" + self._zh(enemy, "未知单位")
                path = "通用战术/单位专属反制/" + self._zh(enemy, "未知单位")
            db.execute("UPDATE strategies SET name_zh=?,category_path=?,updated_at=? WHERE id=?", (name, path, time.time(), str(row["id"])))
            db.execute("INSERT OR IGNORE INTO categories(path,display_order,builtin) VALUES(?,9999,0)", (path,))
        db.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('category_revision',?)",
            (str(CATEGORY_REVISION),),
        )
        self._bump_generation(db)

    def bootstrap(self, doctrine_path: Path | str | None = None) -> dict[str, int]:
        source = Path(doctrine_path) if doctrine_path else Path(__file__).with_name("combat_doctrine.json")
        try:
            doctrine = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            doctrine = {}
        inserted = 0
        with self._lock, self._connect() as db:
            existing = db.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
            if existing:
                self._reclassify_combat_strategies(db)
                migrated = self._migrate_legacy_blocks(db)
                renamed = self._refresh_atomic_names(db)
                if migrated or renamed:
                    self._rebuild_category_links(db)
                return {"inserted": 0, "existing": int(existing)}
            now = time.time()
            rows: list[tuple[Any, ...]] = []
            for item in doctrine.get("entries", []) if isinstance(doctrine, dict) else []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                conditions = item.get("conditions") if isinstance(item.get("conditions"), dict) else {}
                name = self._matrix_name(conditions)
                category = self._matrix_category(conditions, str(item.get("posture") or ""))
                rows.append(self._row(
                    item, str(item["id"]), name, category, "combat_matrix",
                    "permanent", "stable", "系统迁移", "combat_doctrine", now,
                ))
            for item in doctrine.get("unit_responses", []) if isinstance(doctrine, dict) else []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                enemy_name = item.get("enemy_name") or item.get("enemy_type_id") or item["id"]
                name = f"单位反制：{enemy_name}"
                rows.append(self._row(
                    item, str(item["id"]), name, "通用战术/单位专属反制",
                    "unit_response", "permanent", "stable", "系统迁移",
                    "combat_doctrine", now,
                ))
            for item in CORE_STRATEGIES:
                rows.append(self._row(
                    item, str(item["id"]), str(item["name_zh"]), str(item["category_path"]),
                    str(item["strategy_type"]), "permanent", "stable", "系统迁移",
                    "hardcoded_reference", now,
                ))
            db.executemany(
                """INSERT OR IGNORE INTO strategies(
                    id,name_zh,category_path,strategy_type,library_scope,status,enabled,
                    usable_in_match,priority,weight,risk_level,author,source_kind,source_text,
                    payload_json,revision,created_at,updated_at,expires_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            inserted = db.execute("SELECT changes()").fetchone()[0]
            metadata = doctrine.get("metadata", {}) if isinstance(doctrine, dict) else {}
            db.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('combat_metadata',?)",
                (_json(metadata),),
            )
            db.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('generation',?)",
                (str(time.time_ns()),),
            )
            self._migrate_legacy_blocks(db)
            self._refresh_atomic_names(db)
            self._rebuild_category_links(db)
            count = db.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
        return {"inserted": int(inserted), "existing": int(count)}

    def rebuild_generated_strategies(
        self, doctrine_path: Path | str | None = None,
    ) -> dict[str, int]:
        """Recompile every generated rule while retaining player and learned rules."""
        source = Path(doctrine_path) if doctrine_path else Path(__file__).with_name("combat_doctrine.json")
        doctrine = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(doctrine, dict):
            raise ValueError("策略生成源不是有效对象")
        now = time.time()
        rows: list[tuple[Any, ...]] = []
        identifiers: list[str] = []
        for item in doctrine.get("entries", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            conditions = item.get("conditions") if isinstance(item.get("conditions"), dict) else {}
            identifier = str(item["id"])
            identifiers.append(identifier)
            rows.append(self._row(
                item, identifier, self._matrix_name(conditions),
                self._matrix_category(conditions, str(item.get("posture") or "")),
                "combat_matrix", "permanent", "stable", "系统重编译",
                "combat_doctrine", now,
            ))
        for item in doctrine.get("unit_responses", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            identifier = str(item["id"])
            identifiers.append(identifier)
            enemy_name = str(item.get("enemy_name") or item.get("enemy_type_id") or "未识别单位")
            rows.append(self._row(
                item, identifier, f"单位反制：{enemy_name}",
                f"通用战术/单位专属反制/{enemy_name}", "unit_response",
                "permanent", "stable", "系统重编译", "combat_doctrine", now,
            ))
        with self._lock, self._connect() as db:
            db.execute("CREATE TEMP TABLE current_generated_ids(id TEXT PRIMARY KEY)")
            db.executemany("INSERT INTO current_generated_ids(id) VALUES(?)", ((item,) for item in identifiers))
            db.execute(
                "DELETE FROM strategies WHERE source_kind='combat_doctrine' AND id NOT IN (SELECT id FROM current_generated_ids)"
            )
            db.executemany(
                """INSERT INTO strategies(
                    id,name_zh,category_path,strategy_type,library_scope,status,enabled,
                    usable_in_match,priority,weight,risk_level,author,source_kind,source_text,
                    payload_json,revision,created_at,updated_at,expires_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name_zh=excluded.name_zh,category_path=excluded.category_path,
                    strategy_type=excluded.strategy_type,library_scope=excluded.library_scope,
                    status=excluded.status,enabled=1,usable_in_match=1,
                    priority=excluded.priority,weight=excluded.weight,risk_level=excluded.risk_level,
                    author=excluded.author,source_kind=excluded.source_kind,
                    source_text=excluded.source_text,payload_json=excluded.payload_json,
                    revision=strategies.revision+1,updated_at=excluded.updated_at""",
                rows,
            )
            migrated = self._migrate_legacy_blocks(db)
            renamed = self._refresh_atomic_names(db)
            links = self._rebuild_category_links(db)
            metadata = doctrine.get("metadata") if isinstance(doctrine.get("metadata"), dict) else {}
            db.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('combat_metadata',?)",
                (_json(metadata),),
            )
            self._bump_generation(db)
        return {
            "generated": len(rows), "compiled": migrated,
            "renamed": renamed, "category_links": links,
        }

    def _migrate_legacy_blocks(self, db: sqlite3.Connection) -> int:
        """Give every strategy a standard, editable event-script tree."""
        rows = db.execute(
            "SELECT id,strategy_type,payload_json FROM strategies WHERE status!='deleted'"
        ).fetchall()
        migrated = 0
        for row in rows:
            payload = _decode(row["payload_json"], {})
            program = payload.get("block_program") if isinstance(payload, dict) else None
            if not isinstance(payload, dict):
                continue
            legacy_keys = {"conditions", "actions", "flow", "constraints", "production_roles", "target_priorities", "formation", "engagement", "tactical_controls", "posture", "trigger", "team", "verification", "enemy_type_id", "enemy_name", "force_ratio", "terrain", "enemy_tech_level", "enemy_role"}
            program_keys = set(program) if isinstance(program, dict) else set()
            if (
                payload.get("block_editor") == "原子积木树"
                and isinstance(program, dict)
                and isinstance(program.get("scripts"), list)
                and bool(program["scripts"])
                and not (set(payload) & legacy_keys)
                and program_keys <= {"scripts"}
            ):
                continue
            has_legacy = bool(set(payload) & legacy_keys) or not program_keys <= {"scripts"}
            source_for_compile = dict(payload)
            if has_legacy:
                source_for_compile.pop("block_program", None)
            try:
                compiled = compile_program(
                    source_for_compile, strategy_id=str(row["id"]),
                    strategy_type=str(row["strategy_type"]),
                )
            except BlockCompileError:
                continue
            payload = clean_atomic_payload(payload, compiled["program"], compiled["diagnostics"])
            db.execute(
                "UPDATE strategies SET payload_json=?,revision=revision+1,updated_at=? WHERE id=?",
                (_json(payload), time.time(), str(row["id"])),
            )
            migrated += 1
        if migrated:
            self._bump_generation(db)
        return migrated

    def _refresh_atomic_names(self, db: sqlite3.Connection) -> int:
        """Derive unit-response labels from the visible atomic selector."""
        rows = db.execute(
            "SELECT id,payload_json FROM strategies WHERE strategy_type='unit_response' AND status!='deleted'"
        ).fetchall()
        changed = 0
        for row in rows:
            payload = _decode(row["payload_json"], {})
            program = payload.get("block_program") if isinstance(payload, dict) else None
            selector_changed = repair_atomic_selectors(program) if isinstance(program, dict) else False
            record = doctrine_from_atomic_program(
                program if isinstance(program, dict) else {},
                strategy_id=str(row["id"]), strategy_type="unit_response",
            )
            if not isinstance(record, dict):
                continue
            unit_name = str(record.get("enemy_name") or "未识别单位")
            force = FORCE_RATIO_ZH.get(str(record.get("force_ratio") or ""), "任意兵力")
            terrain = TERRAIN_ZH.get(str(record.get("terrain") or ""), "任意地形")
            name = f"单位反制：{unit_name} / {force} / {terrain}"
            category = f"通用战术/单位专属反制/{unit_name}"
            current = db.execute("SELECT name_zh,category_path FROM strategies WHERE id=?", (str(row["id"]),)).fetchone()
            if current and current["name_zh"] == name and current["category_path"] == category and not selector_changed:
                continue
            db.execute(
                "UPDATE strategies SET name_zh=?,category_path=?,payload_json=?,updated_at=? WHERE id=?",
                (name, category, _json(payload), time.time(), str(row["id"])),
            )
            db.execute(
                "INSERT OR IGNORE INTO categories(path,display_order,builtin) VALUES(?,9999,0)",
                (category,),
            )
            changed += 1
        if changed:
            self._bump_generation(db)
        return changed

    @staticmethod
    def _zh(value: Any, fallback: str = "未分类") -> str:
        key = str(value or "")
        return ZH_LABELS.get(key, key.replace("_", " ") if key else fallback)

    @classmethod
    def _matrix_name(cls, conditions: dict[str, Any]) -> str:
        return "作战矩阵：" + " / ".join(
            cls._zh(conditions.get(key), "任意")
            for key in ("enemy_faction", "primary_threat", "stage", "force_ratio", "terrain")
        )

    @classmethod
    def _matrix_category(cls, conditions: dict[str, Any], posture: str = "") -> str:
        stance = str(posture or "")
        if not stance:
            ratio = str(conditions.get("force_ratio") or "even")
            stage = str(conditions.get("stage") or "opening")
            stance = "delay" if ratio == "behind" else "attack" if ratio == "ahead" and stage in {"tier3", "late", "sudden_death"} else "probe"
        if stance in {"defend", "delay"}:
            return "通用战术/基地防御"
        if stance in {"pressure", "attack", "decisive_attack"}:
            return "通用战术/进攻与撤退"
        return "通用战术/接触战"

    @classmethod
    def _category_paths_for_record(
        cls, strategy_type: str, record: dict[str, Any], primary: str,
    ) -> set[str]:
        paths = {str(primary)} if primary else set()
        if strategy_type == "combat_matrix":
            conditions = record.get("conditions") if isinstance(record.get("conditions"), dict) else {}
            faction = str(conditions.get("enemy_faction") or "")
            paths.update(FACTION_CATEGORY_PATHS.get(faction, ()))
            threat = str(conditions.get("primary_threat") or "")
            if threat in AIR_THREATS:
                paths.add("通用战术/空军")
            if threat in NAVAL_THREATS:
                paths.add("通用战术/海军")
            if threat in DEFENSE_THREATS:
                paths.add("通用战术/基地防御")
            if threat == "harvester_raid":
                paths.add("通用战术/维修与保护/护矿")
            if threat == "economy_expansion":
                paths.add("通用战术/经济与扩张")
            if threat == "tech_rush":
                paths.add("通用战术/生产与科技")
            if threat == "superweapon":
                paths.update(("通用战术/支援技能", "通用战术/超级武器"))
        elif strategy_type == "unit_response":
            type_id = str(record.get("enemy_type_id") or "").upper()
            unit = _catalog_records().get(type_id, {})
            paths.update(
                OWNER_CATEGORY_PATHS[owner]
                for owner in unit.get("owners") or () if owner in OWNER_CATEGORY_PATHS
            )
            kind = str(unit.get("kind") or "")
            movement = str(unit.get("movement_zone") or "")
            building_class = str(unit.get("building_class") or "")
            if kind == "aircraft":
                paths.add("通用战术/空军")
            elif kind == "naval" or "Water" in movement:
                paths.add("通用战术/海军")
            elif kind == "building" or building_class not in {"", "unit"}:
                paths.add("通用战术/进攻与撤退")
            ratio = str(record.get("force_ratio") or "even")
            paths.add({
                "behind": "通用战术/基地防御",
                "even": "通用战术/接触战",
                "ahead": "通用战术/进攻与撤退",
            }.get(ratio, "通用战术/接触战"))
        return {path for path in paths if path}

    def _set_category_links(
        self, db: sqlite3.Connection, strategy_id: str, paths: set[str],
    ) -> None:
        db.execute("DELETE FROM strategy_categories WHERE strategy_id=?", (strategy_id,))
        for path in sorted(paths):
            db.execute("INSERT OR IGNORE INTO categories(path,display_order,builtin) VALUES(?,9999,0)", (path,))
            db.execute(
                "INSERT OR IGNORE INTO strategy_categories(strategy_id,path) VALUES(?,?)",
                (strategy_id, path),
            )

    def _rebuild_category_links(self, db: sqlite3.Connection) -> int:
        rows = db.execute(
            "SELECT id,category_path,strategy_type,payload_json FROM strategies WHERE status!='deleted'"
        ).fetchall()
        linked = 0
        for row in rows:
            payload = _decode(row["payload_json"], {})
            program = payload.get("block_program") if isinstance(payload, dict) else {}
            record = doctrine_from_atomic_program(
                program if isinstance(program, dict) else {},
                strategy_id=str(row["id"]), strategy_type=str(row["strategy_type"]),
            )
            if not isinstance(record, dict):
                record = {}
            paths = self._category_paths_for_record(
                str(row["strategy_type"]), record, str(row["category_path"]),
            )
            self._set_category_links(db, str(row["id"]), paths)
            linked += len(paths)
        return linked

    def _reclassify_combat_strategies(self, db: sqlite3.Connection) -> None:
        marker = db.execute("SELECT value FROM metadata WHERE key='category_revision'").fetchone()
        if marker and int(marker[0]) >= 2:
            return
        rows = db.execute(
            "SELECT id,strategy_type,payload_json FROM strategies WHERE source_kind='combat_doctrine'"
        ).fetchall()
        for row in rows:
            payload = _decode(row["payload_json"], {})
            if row["strategy_type"] == "combat_matrix":
                conditions = payload.get("conditions") if isinstance(payload.get("conditions"), dict) else {}
                category = self._matrix_category(conditions, str(payload.get("posture") or ""))
                name = self._matrix_name(conditions)
            else:
                enemy = payload.get("enemy_name") or payload.get("enemy_type_id") or "未知单位"
                category = "通用战术/单位专属反制/" + self._zh(enemy, "未知单位")
                name = "单位反制：" + self._zh(enemy, "未知单位")
            db.execute(
                "UPDATE strategies SET name_zh=?,category_path=?,updated_at=? WHERE id=?",
                (name, category, time.time(), str(row["id"])),
            )
            db.execute("INSERT OR IGNORE INTO categories(path,display_order,builtin) VALUES(?,9999,0)", (category,))
        db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('category_revision','2')")
        self._bump_generation(db)

    @staticmethod
    def _row(
        payload: dict[str, Any], strategy_id: str, name: str, category: str,
        strategy_type: str, scope: str, status: str, author: str,
        source_kind: str, now: float,
    ) -> tuple[Any, ...]:
        return (
            strategy_id, name, category, strategy_type, scope, status, 1, 1,
            int(payload.get("priority", 50)), float(payload.get("weight", 50)),
            str(payload.get("risk_level", "medium")), author, source_kind,
            str(payload.get("source_text", "")), _json(payload), 1, now, now, None,
        )

    def categories(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            category_rows = db.execute(
                "SELECT path FROM categories ORDER BY display_order,path"
            ).fetchall()
            links = db.execute(
                """SELECT sc.path,sc.strategy_id FROM strategy_categories sc
                   JOIN strategies s ON s.id=sc.strategy_id WHERE s.status!='deleted'"""
            ).fetchall()
        known = {str(row["path"]) for row in category_rows}
        members: dict[str, set[str]] = {path: set() for path in known}
        for row in links:
            path = str(row["path"])
            identifier = str(row["strategy_id"])
            parts = path.split("/")
            for depth in range(1, len(parts) + 1):
                ancestor = "/".join(parts[:depth])
                if ancestor in members:
                    members[ancestor].add(identifier)
        return [
            {"path": str(row["path"]), "count": len(members[str(row["path"])])}
            for row in category_rows
        ]

    def list_strategies(
        self, *, scope: str = "", status: str = "", category: str = "",
        search: str = "", limit: int = 200, offset: int = 0,
    ) -> dict[str, Any]:
        clauses = ["status!='deleted'"]
        values: list[Any] = []
        if scope in {"permanent", "temporary"}:
            clauses.append("library_scope=?")
            values.append(scope)
        if status:
            clauses.append("status=?")
            values.append(status)
        if category:
            clauses.append(
                "EXISTS (SELECT 1 FROM strategy_categories sc WHERE sc.strategy_id=strategies.id AND sc.path LIKE ?)"
            )
            values.append(category.rstrip("/") + "%")
        if search:
            clauses.append("(name_zh LIKE ? OR id LIKE ? OR payload_json LIKE ?)")
            token = f"%{search}%"
            values.extend((token, token, token))
        where = " AND ".join(clauses)
        maximum = max(1, min(1000, int(limit)))
        start = max(0, int(offset))
        with self._connect() as db:
            total = int(db.execute(f"SELECT COUNT(*) FROM strategies WHERE {where}", values).fetchone()[0])
            rows = db.execute(
                f"""SELECT id,name_zh,category_path,strategy_type,library_scope,status,
                    enabled,usable_in_match,priority,weight,risk_level,author,source_kind,
                    revision,created_at,updated_at
                    FROM strategies WHERE {where}
                    ORDER BY priority DESC,updated_at DESC,id LIMIT ? OFFSET ?""",
                (*values, maximum, start),
            ).fetchall()
        return {"total": total, "offset": start, "limit": maximum, "items": [self._summary(row) for row in rows]}

    def get_strategy(self, strategy_id: str, include_deleted: bool = False) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM strategies WHERE id=?", (str(strategy_id),)).fetchone()
            category_rows = db.execute(
                "SELECT path FROM strategy_categories WHERE strategy_id=? ORDER BY path",
                (str(strategy_id),),
            ).fetchall()
        if row is None or (row["status"] == "deleted" and not include_deleted):
            return None
        result = self._summary(row)
        result.update({
            "source_text": str(row["source_text"]),
            "expires_at": row["expires_at"],
            "payload": _decode(row["payload_json"], {}),
            "category_paths": [str(item["path"]) for item in category_rows],
        })
        return result

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]), "name_zh": str(row["name_zh"]),
            "category_path": str(row["category_path"]),
            "strategy_type": str(row["strategy_type"]),
            "library_scope": str(row["library_scope"]), "status": str(row["status"]),
            "enabled": bool(row["enabled"]), "usable_in_match": bool(row["usable_in_match"]),
            "priority": int(row["priority"]), "weight": float(row["weight"]),
            "risk_level": str(row["risk_level"]), "author": str(row["author"]),
            "source_kind": str(row["source_kind"]), "revision": int(row["revision"]),
            "created_at": float(row["created_at"]), "updated_at": float(row["updated_at"]),
        }

    def save_strategy(self, value: dict[str, Any], strategy_id: str | None = None) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("strategy must be an object")
        identifier = str(strategy_id or value.get("id") or f"learned_{uuid.uuid4().hex[:12]}").strip()
        if not identifier or len(identifier) > 160:
            raise ValueError("strategy id is invalid")
        scope = str(value.get("library_scope") or "temporary")
        if scope not in {"permanent", "temporary"}:
            raise ValueError("library_scope must be permanent or temporary")
        status = str(value.get("status") or ("experimental" if scope == "temporary" else "stable"))
        allowed = TEMPORARY_STATUSES if scope == "temporary" else PERMANENT_STATUSES
        if status not in allowed:
            raise ValueError("status is not valid for this library")
        name = str(value.get("name_zh") or value.get("display_name") or identifier).strip()
        category = str(value.get("category_path") or ("猫娘学习/临时方案" if scope == "temporary" else "通用战术/接触战")).strip()
        payload = value.get("payload") if isinstance(value.get("payload"), dict) else dict(value)
        for field in ("payload", "library_scope", "status"):
            payload.pop(field, None)
        # Every saved strategy gets a normalized Scratch program. Legacy
        # string flows are converted here so runtime code consumes one AST.
        try:
            compiled = compile_program(
                payload, strategy_id=identifier,
                strategy_type=str(value.get("strategy_type") or "action_flow"),
            )
        except BlockCompileError as exc:
            raise ValueError(f"策略积木无法保存：{exc}") from exc
        payload = clean_atomic_payload(payload, compiled["program"], compiled["diagnostics"])
        strategy_type = str(value.get("strategy_type") or "action_flow")
        category_record = doctrine_from_atomic_program(
            compiled["program"], strategy_id=identifier, strategy_type=strategy_type,
        )
        category_paths = self._category_paths_for_record(
            strategy_type, category_record if isinstance(category_record, dict) else {}, category,
        )
        now = time.time()
        with self._lock, self._connect() as db:
            old = db.execute("SELECT * FROM strategies WHERE id=?", (identifier,)).fetchone()
            revision = int(old["revision"]) + 1 if old else 1
            if old:
                self._archive(db, old, "update")
            db.execute(
                """INSERT INTO strategies(
                    id,name_zh,category_path,strategy_type,library_scope,status,enabled,
                    usable_in_match,priority,weight,risk_level,author,source_kind,source_text,
                    payload_json,revision,created_at,updated_at,expires_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name_zh=excluded.name_zh,category_path=excluded.category_path,
                    strategy_type=excluded.strategy_type,library_scope=excluded.library_scope,
                    status=excluded.status,enabled=excluded.enabled,
                    usable_in_match=excluded.usable_in_match,priority=excluded.priority,
                    weight=excluded.weight,risk_level=excluded.risk_level,
                    author=excluded.author,source_kind=excluded.source_kind,
                    source_text=excluded.source_text,payload_json=excluded.payload_json,
                    revision=excluded.revision,updated_at=excluded.updated_at,
                    expires_at=excluded.expires_at""",
                (
                    identifier, name, category, strategy_type,
                    scope, status, int(value.get("enabled", True)),
                    int(value.get("usable_in_match", True)), int(value.get("priority", 50)),
                    float(value.get("weight", 50)), str(value.get("risk_level") or "medium"),
                    str(value.get("author") or ("猫娘" if scope == "temporary" else "玩家")),
                    str(value.get("source_kind") or ("catgirl_learning" if scope == "temporary" else "player")),
                    str(value.get("source_text") or ""), _json(payload), revision,
                    float(old["created_at"]) if old else now, now, value.get("expires_at"),
                ),
            )
            db.execute("INSERT OR IGNORE INTO categories(path,display_order,builtin) VALUES(?,9999,0)", (category,))
            self._set_category_links(db, identifier, category_paths)
            self._bump_generation(db)
        result = self.get_strategy(identifier)
        if result is None:
            raise RuntimeError("saved strategy disappeared")
        return result

    def promote(self, strategy_id: str, category_path: str | None = None) -> dict[str, Any]:
        current = self.get_strategy(strategy_id)
        if current is None:
            raise KeyError(strategy_id)
        if current["library_scope"] != "temporary":
            return current
        current.update({
            "library_scope": "permanent", "status": "stable",
            "category_path": category_path or current["category_path"].replace("猫娘学习/临时方案", "通用战术/接触战"),
            "source_kind": "promoted_catgirl_learning",
        })
        return self.save_strategy(current, strategy_id)

    def set_enabled(self, strategy_id: str, enabled: bool, usable_in_match: bool | None = None) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM strategies WHERE id=? AND status!='deleted'", (strategy_id,)).fetchone()
            if row is None:
                raise KeyError(strategy_id)
            self._archive(db, row, "enable" if enabled else "pause")
            db.execute(
                "UPDATE strategies SET enabled=?,usable_in_match=COALESCE(?,usable_in_match),revision=revision+1,updated_at=? WHERE id=?",
                (int(bool(enabled)), None if usable_in_match is None else int(bool(usable_in_match)), time.time(), strategy_id),
            )
            self._bump_generation(db)
        result = self.get_strategy(strategy_id)
        if result is None:
            raise RuntimeError("updated strategy disappeared")
        return result

    def delete(self, strategy_id: str) -> None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM strategies WHERE id=?", (strategy_id,)).fetchone()
            if row is None:
                raise KeyError(strategy_id)
            self._archive(db, row, "delete")
            db.execute(
                "UPDATE strategies SET status='deleted',enabled=0,usable_in_match=0,revision=revision+1,updated_at=? WHERE id=?",
                (time.time(), strategy_id),
            )
            self._bump_generation(db)

    def record_result(
        self, strategy_id: str, outcome: str, *, failure_layer: str = "",
        metrics: dict[str, Any] | None = None, context: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._connect() as db:
            if db.execute("SELECT 1 FROM strategies WHERE id=?", (strategy_id,)).fetchone() is None:
                raise KeyError(strategy_id)
            db.execute(
                "INSERT INTO strategy_results(strategy_id,outcome,failure_layer,metrics_json,context_json,recorded_at) VALUES(?,?,?,?,?,?)",
                (strategy_id, str(outcome), str(failure_layer), _json(metrics or {}), _json(context or {}), time.time()),
            )

    def runtime_snapshot(self) -> dict[str, Any]:
        with self._connect() as db:
            generation_row = db.execute("SELECT value FROM metadata WHERE key='generation'").fetchone()
            rows = db.execute(
                """SELECT * FROM strategies
                   WHERE status IN ('experimental','stable','recommended')
                   AND enabled=1 AND usable_in_match=1
                   ORDER BY priority DESC,weight DESC,id"""
            ).fetchall()
        items = []
        for row in rows:
            item = self._summary(row)
            item["payload"] = _decode(row["payload_json"], {})
            try:
                item["compiled_program"] = compile_program(
                    item["payload"], strategy_id=item["id"],
                    strategy_type=item["strategy_type"],
                )
            except BlockCompileError as exc:
                # A malformed user strategy must be visible to diagnostics but
                # must never prevent the rest of the strategy snapshot loading.
                item["compiled_program"] = None
                item["compile_error"] = str(exc)
            items.append(item)
        return {
            "schema": SCHEMA_VERSION,
            "generation": str(generation_row[0]) if generation_row else "0",
            "generated_at": time.time(),
            "strategies": items,
        }

    def generation(self) -> str:
        with self._connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='generation'").fetchone()
        return str(row[0]) if row else "0"

    def export_combat_doctrine(self) -> dict[str, Any]:
        with self._connect() as db:
            metadata_row = db.execute("SELECT value FROM metadata WHERE key='combat_metadata'").fetchone()
            rows = db.execute(
                """SELECT id,strategy_type,payload_json,priority,weight,risk_level FROM strategies
                   WHERE source_kind='combat_doctrine' AND status!='deleted' AND enabled=1
                   ORDER BY priority DESC,weight DESC,id"""
            ).fetchall()
        entries, unit_responses = [], []
        for row in rows:
            payload = _decode(row["payload_json"], None)
            if not isinstance(payload, dict):
                continue
            program = payload.get("block_program")
            record = doctrine_from_atomic_program(
                program if isinstance(program, dict) else {},
                strategy_id=str(row["id"]),
                strategy_type=str(row["strategy_type"]),
            )
            if not isinstance(record, dict):
                continue
            record["priority"] = int(row["priority"])
            record["weight"] = float(row["weight"])
            record["risk_level"] = str(row["risk_level"])
            (entries if row["strategy_type"] == "combat_matrix" else unit_responses).append(record)
        metadata = _decode(metadata_row[0], {}) if metadata_row else {}
        metadata.update({
            "matrix_entry_count": len(entries), "unit_response_count": len(unit_responses),
            "entry_count": len(entries) + len(unit_responses), "source": "strategy_library.sqlite3",
        })
        return {"schema": 1, "metadata": metadata, "entries": entries, "unit_responses": unit_responses}

    @staticmethod
    def _archive(db: sqlite3.Connection, row: sqlite3.Row, operation: str) -> None:
        snapshot = {key: row[key] for key in row.keys()}
        db.execute(
            "INSERT INTO strategy_history(strategy_id,revision,operation,snapshot_json,changed_at) VALUES(?,?,?,?,?)",
            (str(row["id"]), int(row["revision"]), operation, _json(snapshot), time.time()),
        )

    @staticmethod
    def _bump_generation(db: sqlite3.Connection) -> None:
        db.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('generation',?)",
            (str(time.time_ns()),),
        )


_shared_library: StrategyLibrary | None = None
_shared_library_lock = threading.Lock()


def shared_strategy_library() -> StrategyLibrary:
    global _shared_library
    with _shared_library_lock:
        if _shared_library is None:
            _shared_library = StrategyLibrary()
            _shared_library.bootstrap()
        return _shared_library
