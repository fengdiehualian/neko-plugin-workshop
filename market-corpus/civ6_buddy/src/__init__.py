"""文明6陪玩插件（civ6_buddy）— 让 YUI 陪玩家打文明6。

深度监控：解析 .Civ6Save 存档，获知当前局面（回合/速度/地图/玩家文明/
敌对文明/城邦/MOD），基于局面给陪玩建议（文明特性 + 对手 + 早期策略模板）。
运行依赖：本机 node（>=16）+ 插件内嵌 civ6_save_parser（已随插件打包）。

只读操作：不修改存档、不写游戏文件。存档目录可在 config 覆盖。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from plugin.sdk.plugin import NekoPluginBase, lifecycle, llm_tool, neko_plugin, plugin_entry

from . import civ6_advice, civ6_data, civ6_ops, civ6_parser, civ6_sdk, civ6_tuner

_logger = logging.getLogger("N.E.K.O.Plugin_civ6_buddy.civ6_buddy")


# ── 运营锁（跨插件优先级） ──
# 运营托管期间写锁文件 `%LOCALAPPDATA%\N.E.K.O\config\civ6_ops_active`（带时间戳），
# steam_manager 等插件见新鲜锁（<5 分钟）即让位，避免抢占 Neko 运营注意力。
# 锁带时间戳：civ6_buddy 每回合刷新；崩溃/被杀导致残留锁在 5 分钟后自动失效，不误伤。
def _ops_lock_path() -> str:
    return os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "N.E.K.O", "config", "civ6_ops_active"
    )
_OPS_LOCK_TTL = 300.0


def _ops_lock_set() -> None:
    """写/刷新运营锁（内容=写入时间戳）。"""
    p = _ops_lock_path()
    if not p:
        return
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def _ops_lock_clear() -> None:
    p = _ops_lock_path()
    if p:
        try:
            os.remove(p)
        except OSError:
            pass


def ops_lock_active(ttl: float = _OPS_LOCK_TTL) -> bool:
    """锁存在且新鲜（mtime 距今 < ttl）→ 运营托管中。供其他插件（steam_manager）查询。"""
    p = _ops_lock_path()
    if not p or not os.path.isfile(p):
        return False
    try:
        return (time.time() - os.path.getmtime(p)) < ttl
    except OSError:
        return False

_ERR_HINTS: dict[str, str] = {
    "SAVES_DIR_NOT_FOUND": "存档目录不存在：请先打开文明6新建一局并自动存档，或在本插件 config 中配置 saves_dir",
    "NO_SAVES": "尚无任何存档：开局后等自动存档生成（AutoSave_0001.Civ6Save）再问",
    "SAVE_NOT_FOUND": "存档名没对上：可先用 civ6_list_saves 看有哪些存档，再按名字查",
    "PARSER_MISSING": "插件内嵌解析器缺失：请重装插件",
    "NODE_MISSING": "本机没有 node：安装 Node.js（https://nodejs.org）后重启插件",
    "PARSE_TIMEOUT": "解析超时：存档可能损坏或过大，换个存档试",
    "PARSE_FAILED": "解析失败：存档可能损坏或为不兼容版本，如实上报，不要编造局面",
    "PARSE_OUTPUT_INVALID": "解析器输出异常：重试一次，仍失败则上报插件问题",
}


def _wrap_err(exc: civ6_parser.Civ6Error) -> dict:
    out: dict = {"output": str(exc), "is_error": True, "error": exc.code}
    hint = _ERR_HINTS.get(exc.code)
    if hint:
        out["hint"] = hint
    return out


def _tool_params(_: dict) -> dict:
    """宿主 llm_tool 传参防御：parameters 偶发被传成标量（int/str），非 dict 降级空字典。"""
    p = _.get("parameters")
    return p if isinstance(p, dict) else {}


@neko_plugin
class Civ6BuddyPlugin(NekoPluginBase):
    """N.E.K.O. 插件：文明6陪玩（解析存档获知局面 + 陪玩建议）。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._saves_dir = ""
        self._ops_buttons: dict = {}
        self._cfg_loaded = False
        self._ops_active = False

    def _load_config(self) -> None:
        """读取运行配置 plugin.toml（civ6_buddy.saves_dir / ops_buttons），失败静默用默认。"""
        if self._cfg_loaded:
            return
        self._cfg_loaded = True
        try:
            cfg_path = Path(self.plugin_dir) / "config" / "plugin.toml"
            if cfg_path.is_file():
                import tomllib
                data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
                sec = data.get("civ6_buddy", {})
                if isinstance(sec, dict):
                    if sec.get("saves_dir"):
                        self._saves_dir = str(sec["saves_dir"])
                    buttons = sec.get("ops_buttons")
                    if isinstance(buttons, dict):
                        cleaned = {}
                        for k in ("next_turn", "policy_ok", "production_yes"):
                            v = buttons.get(k)
                            if (isinstance(v, (list, tuple)) and len(v) == 2
                                    and all(isinstance(n, (int, float)) for n in v)):
                                cleaned[k] = (float(v[0]), float(v[1]))
                        self._ops_buttons = cleaned
        except Exception:  # 配置异常不影响插件工作
            _logger.warning("civ6_buddy config read failed, use defaults", exc_info=True)

    # ══════════ 工具 ══════════

    @llm_tool(
        name="civ6_list_saves",
        description=(
            "列出本机文明6存档。何时用：用户说'看我有哪些文明6存档/最近玩了什么'或"
            "civ6_parse_save 报存档找不到时。参数无。"
            "返回按时间倒序的存档列表（相对路径/时间/大小/自动或手动）。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=20.0,
    )
    async def civ6_list_saves(self, **_):
        self._load_config()
        try:
            saves = civ6_parser.list_saves(self._saves_dir)
        except civ6_parser.Civ6Error as exc:
            return _wrap_err(exc)
        if not saves:
            return {"output": "未找到任何存档。请先打开文明6开始一局（产生自动存档）。", "is_error": False, "saves": []}
        lines = [f"共 {len(saves)} 个存档（最新在前）："]
        for s in saves:
            lines.append(f"- {s['rel']}（{s['mtime_text']}，{s['size']//1024}KB，{'自动' if s['kind']=='auto' else '手动'}）")
        return {"output": "\n".join(lines), "is_error": False, "saves": saves}

    @llm_tool(
        name="civ6_parse_save",
        description=(
            "解析指定文明6存档并输出完整局面。何时用：用户指定某个存档（如'看AutoSave_0002'）或想对比时。"
            "参数 save：存档相对路径/文件名/关键字，留空=最新存档。"
            "返回回合、速度、地图、玩家文明、敌对文明、城邦、MOD 及陪玩提示。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "save": {"type": "string", "description": "存档相对路径/文件名/关键字（留空=最新）"},
            },
            "required": [],
        },
        timeout=60.0,
    )
    async def civ6_parse_save(self, *, save: str = "", **_):
        params = _tool_params(_)
        save = str(save or params.get("save") or "").strip()
        self._load_config()
        try:
            sit = civ6_parser.parse_save(save, self._saves_dir)
        except civ6_parser.Civ6Error as exc:
            return _wrap_err(exc)
        return {
            "output": civ6_parser.format_situation(sit),
            "is_error": False,
            "situation": sit,
        }

    @llm_tool(
        name="civ6_situation",
        description=(
            "获取文明6当前局面（自动读最新存档）。何时用：用户说'看看我这局/现在什么情况/"
            "帮我分析这局'等陪玩提问主入口。参数无。"
            "返回回合、速度、地图、你的文明与特性、敌对文明、城邦、MOD 与陪玩提示。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=60.0,
    )
    async def civ6_situation(self, **_):
        self._load_config()
        # 优先 FireTuner 实时读取（游戏开着且启用 Tuner 时数据即时准确）
        try:
            live = await civ6_tuner.read_situation(timeout=8.0)
            if live.get("ok"):
                sit = civ6_parser.situation_from_live(live)
                lines = civ6_parser.format_situation(sit)
                if sit.get("cities"):
                    lines += f"\n🏙️ 城市数：{sit['cities']}（实时）"
                if sit.get("current_research"):
                    lines += f"\n🔬 当前研究：{sit['current_research']}（实时）"
                return {
                    "output": lines,
                    "is_error": False,
                    "situation": sit,
                    "via": "tuner",
                }
        except Exception:
            pass  # Tuner 不可用/异常 → 回退存档解析
        try:
            sit = civ6_parser.parse_save("", self._saves_dir)
        except civ6_parser.Civ6Error as exc:
            return _wrap_err(exc)
        return {
            "output": civ6_parser.format_situation(sit),
            "is_error": False,
            "situation": sit,
        }

    @llm_tool(
        name="civ6_civ_profile",
        description=(
            "查询文明6文明/领袖背景与特性。何时用：用户问'刚果强吗/这个文明怎么玩/"
            "秦始皇特性'等文明知识问题，或陪玩时需要了解某文明的玩法倾向。"
            "参数 civ：文明名（中英文均可，如'刚果'/'Kongo'/'德国'）。"
            "返回中文名、领袖、文明特性一句话与简单玩法倾向。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "civ": {"type": "string", "description": "文明名（中文或英文 id，如 刚果/Kongo）"},
            },
            "required": ["civ"],
        },
        timeout=15.0,
    )
    async def civ6_civ_profile(self, *, civ: str = "", **_):
        params = _tool_params(_)
        civ = str(civ or params.get("civ") or "").strip()
        if not civ:
            return {"output": "请提供文明名，例如：刚果 / Kongo / 德国。", "is_error": True, "error": "NO_CIV"}
        # 先按英文 id 精确，再按中文名反查，再按不区分大小写关键字
        match = None
        q = civ.strip()
        if q in civ6_data.CIVILIZATIONS:
            match = q
        else:
            for cid, entry in civ6_data.CIVILIZATIONS.items():
                if entry["name"] == q or q.lower() in cid.lower():
                    match = cid
                    break
        if not match:
            if q in civ6_data.CITY_STATES:
                return {
                    "output": f"🏙️ {q}：城邦。{civ6_data.CITY_STATES[q]}。城邦可以通过使者结盟获得其加成。",
                    "is_error": False,
                }
            known = "、".join(f"{v['name']}（{k}）" for k, v in civ6_data.CIVILIZATIONS.items())
            return {"output": f"未找到文明「{civ}」。已知文明：{known}", "is_error": True, "error": "CIV_UNKNOWN"}
        entry = civ6_data.CIVILIZATIONS[match]
        leader = entry.get("leader", "").replace(":", "（") + "）" if entry.get("leader") else ""
        ua = entry.get("ua", "")
        return {
            "output": f"🏛️ {entry['name']}（{match}）\n领袖：{leader}\n特性：{ua}",
            "is_error": False,
            "profile": {"id": match, "name": entry["name"], "leader": leader, "ua": ua},
        }

    @llm_tool(
        name="civ6_advise",
        description=(
            "基于当前局面给文明6陪玩建议。何时用：用户问'这局怎么玩/我该干嘛/"
            "开局策略/科技怎么点'等。参数 topic：建议主题（可选，如 开局/科技/建城/"
            "战争/胜利路线），留空=综合开局建议。"
            "返回基于玩家文明特性、对手构成、城邦与回合阶段的规则化建议要点。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "建议主题：开局/科技/建城/战争/胜利/外交（留空=综合）"},
            },
            "required": [],
        },
        timeout=60.0,
    )
    async def civ6_advise(self, *, topic: str = "", **_):
        params = _tool_params(_)
        topic = str(topic or params.get("topic") or "").strip()
        self._load_config()
        try:
            sit = civ6_parser.parse_save("", self._saves_dir)
        except civ6_parser.Civ6Error as exc:
            return _wrap_err(exc)
        lines = [civ6_parser.format_situation(sit), "", "💡 陪玩建议："]
        advice = civ6_advice.advice_lines(sit, topic)
        lines += advice
        return {"output": "\n".join(lines), "is_error": False, "situation": sit, "advice": advice}

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        """启动钩子：仅做配置预加载与自检，不启动任何后台任务（纯只读插件）。"""
        try:
            self._load_config()
            civ6_parser.self_check(self._saves_dir)
        except Exception as exc:  # 自检失败不影响宿主启动
            _logger.warning("civ6_buddy startup self-check failed: %s", exc)
        return {"status": "ready", "plugin": "civ6_buddy"}

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        """关闭钩子：无后台资源需释放。"""
        return {"status": "stopped", "plugin": "civ6_buddy"}

    # ══════════ 前置链路：SDK 自动安装与 Tuner 配置（v0.4.0） ══════════

    @llm_tool(
        name="civ6_ensure_sdk",
        description=(
            "自动处理文明6底层通道前置：Steam SDK（FireTuner 服务端）检测/安装 + 游戏启用 Tuner + "
            "探测 4318 端口。何时用：用户说'把底层通道装好/自动装 SDK/为什么读不到城市数据'，"
            "或 civ6_tuner_status/civ6_city_details 报 GAME_TUNER_OFF 时。"
            "参数 action：check（默认，汇总状态）/install（SDK 缺失时触发 Steam 自动安装，"
            "appid 597260）/fix_tuner（自动写 AppOptions.txt 启用 Tuner，含备份）。"
            "返回链路各环节状态与下一步动作。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "check/install/fix_tuner，默认 check"},
            },
            "required": [],
        },
        timeout=30.0,
    )
    async def civ6_ensure_sdk(self, *, action: str = "check", **_):
        params = _tool_params(_)
        action = str(action or params.get("action") or "check").strip().lower()
        if action not in ("check", "install", "fix_tuner"):
            return {"output": "未知 action：可用 check / install / fix_tuner。", "is_error": True,
                    "error": "BAD_ACTION"}
        status = civ6_sdk.sdk_status()
        sdk_ok = status["sdk_installed"]
        tuner_ok = status["tuner_enabled"]
        port_ok = status["port_4318_listening"]

        if action == "fix_tuner":
            r = civ6_sdk.ensure_tuner_enabled()
            if not r["changed"]:
                return {"output": f"Tuner 配置无需修改：{r['detail']}。", "is_error": False,
                        "channel": "sdk", "tuner": r}
            return {
                "output": (f"已启用 Tuner：{r['detail']}。修改文件：{r['path']}。"
                           "请重启游戏生效（同屏只能一个 Tuner 连接，先关掉 SDK 自带的 FireTuner.exe）。"),
                "is_error": False, "channel": "sdk", "tuner": r,
            }

        if action == "install":
            if sdk_ok:
                return {"output": f"SDK 已安装（{status['sdk_dir']}），无需重复下载。"
                                  f"Tuner 配置：{('已启用' if tuner_ok else '未启用')}；"
                                  f"端口 4318：{('监听中' if port_ok else '未监听（需重启游戏）')}。",
                        "is_error": False, "channel": "sdk", "status": status}
            trig = civ6_sdk.trigger_steam_install()
            if not trig["ok"]:
                return {"output": trig["detail"], "is_error": True, "error": "STEAM_TRIGGER_FAILED",
                        "channel": "sdk"}
            return {"output": trig["detail"], "is_error": False,
                    "channel": "sdk", "status": status, "action": "install"}

        # check：汇总
        lines = ["文明6 底层通道（FireTuner/SDK）状态："]
        lines.append(f"- SDK 安装：{'✅ ' + status['sdk_dir'] if sdk_ok else '❌ 未安装（免费 Steam 工具，appid 597260）'}")
        lines.append(f"- 游戏目录：{'✅ ' + status['game_dir'] if status['game_dir'] else '⚠ 未找到（检查 Steam 库路径）'}")
        lines.append(f"- Tuner 配置（{status['app_options_path']}）：{'✅ 已启用' if tuner_ok else '❌ 未启用'}")
        lines.append(f"- 端口 4318：{'✅ 监听中（游戏已开 Tuner）' if port_ok else '❌ 未监听（SDK 未装或游戏未重启）'}")
        lines.append(f"- 游戏进程：{'✅ 运行中' if status['game_running'] else '未运行'}")
        missing = []
        if not sdk_ok:
            missing.append("install（自动触发 Steam 下载 SDK）")
        if not tuner_ok:
            missing.append("fix_tuner（自动启用 Tuner 配置）")
        if not port_ok and sdk_ok:
            missing.append("重启游戏后再探测（SDK 装好、Tuner 开好后需重启游戏）")
        if missing:
            lines.append("下一步：请执行 " + "、".join(f"civ6_ensure_sdk(action='{m.split('（')[0]}')" for m in missing))
        else:
            lines.append("✅ 链路就绪：SDK 已装、Tuner 已开、端口在听，可读真实城市数据。")
        return {"output": "\n".join(lines), "is_error": False, "channel": "sdk", "status": status}

    # ══════════ 进阶版：屏幕自动化运营（推荐制） ══════════

    @llm_tool(
        name="civ6_ops_status",
        description=(
            "读取文明6当前真实局面（FireTuner 官方 API）：回合/研究科技/城市生产队列/"
            "阻挡结束回合的通知（blockers）/单位待指挥状态。何时用：运营托管中，"
            "决定'这一回合该干什么'之前先读真实状态，或用户问'现在什么局面/卡在哪'。"
            "参数无。返回当前回合、城市生产（队列大小/在造什么/剩余回合）、"
            "需要处理的 blocker 清单（未选生产/未选科技/单位待指挥/世界议会等）与单位状态。"
            "纪律：汇报必须引用本工具返回的真实数据，禁止编造科技、生产或回合数。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=30.0,
    )
    async def civ6_ops_status(self, **_):
        from . import civ6_tuner
        sit = await civ6_tuner.read_situation(timeout=8.0)
        if not sit.get("ok"):
            return {"output": f"FireTuner 读取失败：{sit.get('detail', sit.get('error', ''))}。"
                              "请先执行 civ6_ensure_sdk(action='check') 检查底层通道。",
                    "is_error": True, "error": sit.get("error", "GAME_TUNER_OFF"), "channel": "firetuner"}
        prod = await civ6_tuner.read_production(timeout=8.0)
        blockers = await civ6_tuner.read_blockers(timeout=8.0)
        units = await civ6_tuner.units_blocker_status(timeout=8.0)
        lines = [f"回合 {sit.get('turn', '?')}（FireTuner 实时）"]
        civ = str(sit.get("civ") or "")
        tech = str(sit.get("tech") or "")
        if civ:
            lines.append(f"文明：{civ}")
        if tech:
            lines.append(f"当前研究：{tech}")
        if prod.get("ok") and prod.get("cities"):
            for c in prod["cities"]:
                sz = c.get("size", 0)
                if sz > 0:
                    lines.append(f"🏙️ 城市生产：{c.get('current_type') or '#' + str(c.get('hash'))} "
                                 f"（队列 {sz} 项，剩 {c.get('turns_left')} 回合）")
                else:
                    lines.append("🏙️ 城市生产：未选择（需要设置生产才能结束回合）")
        if blockers.get("ok") and blockers.get("blockers"):
            lines.append("需要处理：")
            for blk in blockers["blockers"]:
                lines.append(f"- {blk['type']}（{blk.get('message', '')}）")
        else:
            lines.append("无结束回合 blocker（可以直接结束回合）")
        if units.get("ok"):
            if units.get("blocked"):
                lines.append(f"⚠️ 单位待指挥：{len(units.get('units_with_moves', []))} 个有剩余移动力"
                             f"（{'、'.join(units['units_with_moves'][:5])}）")
            else:
                lines.append(f"单位状态：{units.get('total_units', 0)} 个，无待指挥单位")
        eco = await civ6_tuner.read_economy(timeout=8.0)
        if eco.get("ok"):
            gold = eco.get("gold", -1)
            faith = eco.get("faith", -1)
            pop = eco.get("population", 0)
            g = f"{gold:.0f}" if gold >= 0 else "?"
            f_ = f"{faith:.0f}" if faith >= 0 else "?"
            lines.append(f"经济：金币 {g}｜信仰 {f_}｜总人口 {pop}｜"
                         f"单位 {eco.get('scouts', 0)}侦察/{eco.get('warriors', 0)}勇士/"
                         f"{eco.get('builders', 0)}建造者")
            if eco.get("pantheon", -1) < 0 and faith >= 25:
                lines.append("💡 信仰已达 25：万神殿可选（YUI 会在回合管理中自动局面感知选择）")
        dip = await civ6_tuner.read_diplomacy(timeout=8.0)
        if dip.get("ok"):
            rel = "、".join(f"{c['name']}({c['state']})" for c in dip.get("civs", [])) or "无"
            lines.append(f"外交：使节 {dip.get('tokens', 0)}｜关系 {rel}")
            cs_list = dip.get("city_states", [])
            if cs_list:
                cs_txt = "、".join(
                    f"{c['name']}(使节{c.get('envoys', 0)},距{c.get('dist', '?')})" for c in cs_list[:3])
                lines.append(f"城邦：{cs_txt}")
        cul = await civ6_tuner.read_culture(timeout=8.0)
        if cul.get("ok"):
            lines.append(f"文化：产出 {cul.get('culture_yield', '?')}/回合｜旅游业绩 {cul.get('tourism', '?')}｜"
                         f"巨作 {cul.get('great_works', 0)} 件｜市政 {cul.get('civic', '?')}")
        return {
            "output": "\n".join(lines),
            "is_error": False,
            "channel": "firetuner",
            "situation": sit,
            "production": prod,
            "blockers": blockers.get("blockers", []),
            "units": units,
        }

    @llm_tool(
        name="civ6_ops_set_production",
        description=(
            "为城市设置生产项目（FireTuner 官方 API：CityManager.RequestOperation，"
            "设置后同连接验证队列并消除'未选生产'blocker）。何时用：运营托管中，"
            "读到 blockers 含 ENDTURN_BLOCKING_PRODUCTION，或要规划城市造什么。"
            "参数 kind：unit/building/district/project；item：GameInfo Type，如 "
            "UNIT_SCOUT（侦察兵）/BUILDING_MONUMENT（纪念碑）/DISTRICT_CAMPUS（学院）/"
            "BUILDING_GRANARY（粮仓）。"
            "纪律：生产决策要结合真实局面（城市产出/当前回合），说明'为什么造这个'，"
            "禁止编造'已设置生产'——本工具返回 ok=false 时必须如实报告。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "unit/building/district/project，默认 unit"},
                "item": {"type": "string", "description": "GameInfo Type，如 UNIT_SCOUT / BUILDING_MONUMENT"},
            },
            "required": ["item"],
        },
        timeout=30.0,
    )
    async def civ6_ops_set_production(self, *, item: str = "", kind: str = "unit", **_):
        params = _tool_params(_)
        item = str(item or params.get("item") or "").strip()
        kind = str(kind or params.get("kind") or "unit").strip().lower()
        if not item:
            return {"output": "请提供生产项目 Type，例如：UNIT_SCOUT / BUILDING_MONUMENT / DISTRICT_CAMPUS。",
                    "is_error": True, "error": "NO_ITEM"}
        from . import civ6_tuner
        r = await civ6_tuner.set_city_production(kind, item, timeout=12.0)
        if not r.get("ok"):
            return {"output": f"设置生产失败：{r.get('detail', r.get('error', ''))}",
                    "is_error": True, "error": r.get("error", "SET_FAILED"), "channel": "firetuner"}
        return {"output": r["detail"], "is_error": False, "channel": "firetuner",
                "item": item, "kind": kind, "queue_size": r.get("queue_size")}

    @llm_tool(
        name="civ6_ops_set_research",
        description=(
            "设置当前研究（科技或市政，FireTuner 官方 API：PlayerOperations.RESEARCH/"
            "PROGRESS_CIVIC，并消除对应 blocker）。何时用：运营托管中读到 blockers 含 "
            "ENDTURN_BLOCKING_RESEARCH（未选科技）或 ENDTURN_BLOCKING_CIVIC（未选市政），"
            "或要规划科研方向。"
            "参数 category：tech（科技）/civic（市政）；item：GameInfo Type，如 "
            "TECH_BRONZE_WORKING（青铜工作）/TECH_CURRENCY（货币）/CIVIC_CODE_OF_LAWS（法典）。"
            "纪律：科研决策要结合当前局面说明理由（如'马上要开战所以研究青铜工作'），"
            "禁止编造'已选择科技'——ok=false 必须如实报告。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "tech/civic，默认 tech"},
                "item": {"type": "string", "description": "GameInfo Type，如 TECH_BRONZE_WORKING"},
            },
            "required": ["item"],
        },
        timeout=30.0,
    )
    async def civ6_ops_set_research(self, *, item: str = "", category: str = "tech", **_):
        params = _tool_params(_)
        item = str(item or params.get("item") or "").strip()
        category = str(category or params.get("category") or "tech").strip().lower()
        if not item:
            return {"output": "请提供研究项 Type，例如：TECH_BRONZE_WORKING / TECH_CURRENCY / CIVIC_CODE_OF_LAWS。",
                    "is_error": True, "error": "NO_ITEM"}
        from . import civ6_tuner
        r = await civ6_tuner.set_research(category, item, timeout=12.0)
        if not r.get("ok"):
            return {"output": f"设置研究失败：{r.get('detail', r.get('error', ''))}",
                    "is_error": True, "error": r.get("error", "SET_FAILED"), "channel": "firetuner"}
        return {"output": r["detail"], "is_error": False, "channel": "firetuner",
                "category": category, "item": item}

    @llm_tool(
        name="civ6_ops_peek",
        description=(
            "确认文明6 游戏窗口与画面（只读：不点击、不切窗口、不抢焦点）。"
            "用 PrintWindow 直接抓取游戏画面（即使被其他窗口遮挡也能拿到游戏本体）并返回截图。"
            "何时用：用户说'你看看游戏/确认下窗口/截个图看看游戏'。"
            "重要：确认游戏窗口不要用系统截屏——那会截到最上层的聊天/终端窗口；本工具才能拿到真正的游戏画面。"
            "运营执行（点下一回合等）请用 civ6_ops_takeover / civ6_ops_tick，它们会自动定位并前置游戏窗口。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=30.0,
    )
    async def civ6_ops_peek(self, **_):
        try:
            r = civ6_ops.peek_window()
        except Exception as exc:
            return _wrap_err(exc)
        return r

    @llm_tool(
        name="civ6_ops_takeover",
        description=(
            "进入'运营托管'：玩家只负责打仗，YUI 接管本回合运营（推荐制：处理生产/科技/弹窗、"
            "推进回合，优先 FireTuner 官方 API，不可用时屏幕点击兜底）。何时用：用户说"
            "'接管运营/你替我玩/你来全盘操作/运营交给你'。"
            "【硬流程·必须执行】接管成功后，立即（同一次任务内）调用 civ6_ops_tick 且 "
            "loop_turns=20~50 自动连续运营，直到交还/战争/卡住为止；"
            "【严禁调用 steam_manager】运营托管期间禁止调用 steam_manager 任何工具"
            "（含 status/list/capture/run 等一切入口）——游戏局面由 civ6_buddy 提供，"
            "Steam 管理器无需介入；确需 Steam 操作必须先 civ6_ops_handoff 交还。"
            "严禁只接管不运营，严禁回复'你说继续我就点下一回合'后停手等待——用户说'全盘操作'"
            "就是授权你连续运营，不需要每回合都等'继续'。"
            "【行为纪律】接管即开始真实运营；每个回合必须先读真实局面（civ6_ops_status/tick 的"
            "turn_summaries）再行动；禁止编造科技/生产/回合数；禁止'只会点下一回合'——"
            "未选生产就设生产、未选科技就选科技（用 civ6_ops_set_production / "
            "civ6_ops_set_research 或 tick 参数），有 blocker 就处理，处理不了如实交还。"
            "运营执行会自动定位游戏窗口（PrintWindow 拿游戏画面），不需要玩家手动切窗口。"
            "参数无。不改存档、不替玩家移动战争单位。用户/战争打断用 civ6_ops_handoff 交还。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=30.0,
    )
    async def civ6_ops_takeover(self, **_):
        self._load_config()
        self._ops_active = True
        _ops_lock_set()  # 运营锁：steam_manager 等插件见锁让位
        try:
            sit = civ6_parser.parse_save("", self._saves_dir)
        except civ6_parser.Civ6Error as exc:
            return _wrap_err(exc)
        turn = sit.get("game_turn", 0)
        # 接管即运营：内置推进第一回合（Tuner 官方链路优先，屏幕点击兜底）。
        # 历史教训：LLM 可能"先回复用户再调 tick"导致只接管不运营（13:13 复现），
        # 这里接管时直接真实推进一回合，运营必然启动，不依赖 LLM 自觉。
        war_pre = None
        try:
            wr = await civ6_tuner.at_war()
            if wr.get("ok"):
                war_pre = wr.get("at_war", 0) > 0
        except Exception:
            war_pre = None
        if war_pre is True:
            # 战争中：玩家在打仗，运营不接管（避免抢玩家操作）
            self._ops_active = False
            _ops_lock_clear()
            return {
                "output": f"检测到战争状态（第 {turn} 回合）：运营不接管，控制权保持在你手上。"
                          "打仗归你；战斗结束后说'继续运营'我再接管。",
                "is_error": False, "active": False, "turn": turn, "handoff": True,
                "reason": "WAR_TUNER",
            }
        first = None
        tuner_ok = False
        try:
            ts = await civ6_tuner.tuner_status()
            tuner_ok = bool(ts.get("ok"))
        except Exception:
            tuner_ok = False
        if tuner_ok:
            try:
                t0r = await civ6_tuner.read_situation(timeout=8.0)
                tb = int(t0r.get("turn", 0) or 0) if t0r.get("ok") else turn
                first = await self._tuner_manage_turn(
                    tb, war_pre is True, "unit", "UNIT_SCOUT",
                    "TECH_BRONZE_WORKING", "CIVIC_CODE_OF_LAWS",
                )
            except Exception as exc:
                self._ops_active = False
                _ops_lock_clear()
                return {
                    "output": f"已接管（第 {turn} 回合），但首回合 Tuner 运营出现异常：{exc}。"
                              "请确认游戏窗口/FireTuner 正常后再说'继续运营'重试。",
                    "is_error": True, "active": False, "turn": turn, "handoff": True,
                    "reason": "OPS_UNEXPECTED",
                }
        else:
            try:
                first = await asyncio.to_thread(
                    civ6_ops.run_one_turn_ops,
                    {"saves_dir": self._saves_dir, "buttons": self._ops_buttons},
                    war_state=None,
                )
            except civ6_ops.OpsError as exc:
                first = {"ok": False, "handoff": True,
                         "output": f"接管后首回合运营出错：{exc}", "reason": "OPS_ERROR"}
            except Exception as exc:  # 非 OpsError（win32/线程异常）：清锁兜底，避免锁残留
                self._ops_active = False
                _ops_lock_clear()
                return {
                    "output": f"已接管（第 {turn} 回合），但首回合运营出现异常：{exc}。"
                              "请确认游戏窗口正常后再说'继续运营'重试。",
                    "is_error": True, "active": False, "turn": turn, "handoff": True,
                    "reason": "OPS_UNEXPECTED",
                }
        if first is not None and first.get("handoff"):
            self._ops_active = False
            _ops_lock_clear()
            return {
                "output": f"已接管运营（第 {turn} 回合），但首回合即交还：{first.get('output', '')}",
                "is_error": not first.get("ok", False),
                "active": False, "turn": turn, "handoff": True,
                "reason": first.get("reason", ""),
                "log": first.get("log", []),
            }
        first_line = ""
        if first is not None and first.get("ok"):
            first_line = f"\n接管即已推进第一回合：{first.get('output', '')}"
        return {
            "output": (
                f"已接管运营（第 {turn} 回合）。分工规则：运营归我（处理生产/科技/弹窗、推进回合），"
                "打仗归你。立即开始连续运营：调用 civ6_ops_tick 带 loop_turns=20 自动推进，"
                "每回合用真实状态（turn_summaries）汇报在干什么；战争状态或你说'我来打'就立即交还控制权。"
                "严禁调用 steam_manager（运营期间它已让位，调用只会产生无意义回执打断运营）——"
                "游戏局面由 civ6_buddy 的 status/tuner/tick 提供。" + first_line
            ),
            "is_error": False, "active": True, "turn": turn,
            "next_action": (
                "civ6_ops_tick(loop_turns=20)：立即自动连续运营 20 回合，不要等待用户再次喊'继续'；"
                "每回合用 turn_summaries 汇报当前研究/生产/城市状态。"
                "严禁调用 steam_manager 任何工具（其回执会打断运营节奏）；"
                "如确需 Steam 操作，先 civ6_ops_handoff 交还后再调用。"
            ),
        }

    async def _tuner_manage_turn(
        self, turn_before: int, war_flag: bool,
        auto_prod_kind: str, auto_prod_item: str, auto_tech: str, auto_civic: str,
    ) -> dict:
        """Tuner 完整一回合：读 blockers → 处理（生产/科研/市政/单位/软 blocker）→
        结束回合 → 轮询真实回合数验证推进。返回交还/成功。"""
        from . import civ6_tuner
        logs: list = []
        # 回合入口先关一次弹窗/ESC 菜单（v0.6.2 加固）：任务启动时游戏可能已卡在
        # 暂停菜单（用户实测反馈"给她命令后就这样"），Lua 官方关闭菜单后再读 blockers，
        # 避免菜单挡住 end_turn 导致任务被误判卡死中止。
        cp0 = await civ6_tuner.close_ui_popups(timeout=10.0)
        logs.append(f"回合入口弹窗/菜单清理：{cp0.get('detail', cp0.get('error', ''))}")
        b = await civ6_tuner.read_blockers(timeout=8.0)
        if not b.get("ok"):
            return {"handoff": True, "ok": False, "reason": "TUNER_OFF",
                    "output": f"读取 blocker 失败：{b.get('detail', b.get('error', ''))}",
                    "log": logs}
        hard: list = []
        for blk in b.get("blockers", []):
            bt = blk.get("type", "")
            if bt == "ENDTURN_BLOCKING_PRODUCTION":
                # 生产智能决策：用户显式给了参数则尊重，否则按真实局面自动选
                if (auto_prod_item or "").strip().upper() != "UNIT_SCOUT":
                    r = await civ6_tuner.set_city_production(auto_prod_kind, auto_prod_item, timeout=12.0)
                    logs.append(f"设置生产：{auto_prod_item} → {r.get('detail', r.get('error', ''))}")
                else:
                    ad = await civ6_tuner.auto_decide_production(timeout=10.0)
                    if ad.get("ok"):
                        r = await civ6_tuner.set_city_production(ad["kind"], ad["item"], timeout=12.0)
                        logs.append(f"生产决策：{ad.get('reason')} → {r.get('detail', r.get('error', ''))}")
                    else:
                        r = {"ok": False, "detail": ad.get("detail", "")}
                        logs.append(f"生产决策失败：{ad.get('detail', '')}")
                if not r.get("ok"):
                    hard.append((bt, r.get("detail", "")))
            elif bt == "ENDTURN_BLOCKING_RESEARCH":
                r = await civ6_tuner.set_research("tech", auto_tech, timeout=12.0)
                if not r.get("ok"):
                    # 默认科技已完成/不可用 → 自动改选第一个可用科技
                    ap = await civ6_tuner.auto_pick_research("tech", timeout=8.0)
                    if ap.get("ok"):
                        r = await civ6_tuner.set_research("tech", ap["item"], timeout=12.0)
                        logs.append(f"设置科研：{auto_tech} 不可用，自动改选 {ap['item']} → "
                                    f"{r.get('detail', r.get('error', ''))}")
                    else:
                        logs.append(f"设置科研：{auto_tech} → {r.get('detail', r.get('error', ''))}")
                else:
                    logs.append(f"设置科研：{auto_tech} → {r.get('detail')}")
                if not r.get("ok"):
                    hard.append((bt, r.get("detail", "")))
            elif bt == "ENDTURN_BLOCKING_CIVIC":
                r = await civ6_tuner.set_research("civic", auto_civic, timeout=12.0)
                if not r.get("ok"):
                    ap = await civ6_tuner.auto_pick_research("civic", timeout=8.0)
                    if ap.get("ok"):
                        r = await civ6_tuner.set_research("civic", ap["item"], timeout=12.0)
                        logs.append(f"设置市政：{auto_civic} 不可用，自动改选 {ap['item']} → "
                                    f"{r.get('detail', r.get('error', ''))}")
                    else:
                        logs.append(f"设置市政：{auto_civic} → {r.get('detail', r.get('error', ''))}")
                else:
                    logs.append(f"设置市政：{auto_civic} → {r.get('detail')}")
                if not r.get("ok"):
                    hard.append((bt, r.get("detail", "")))
            elif bt in ("ENDTURN_BLOCKING_GOVERNOR_IDLE",
                        "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE",
                        "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK",
                        "ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION",
                        "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE"):
                r = await civ6_tuner.auto_resolve_soft_blockers(timeout=10.0)
                logs.append(f"自动软 blocker：{r.get('detail', r.get('error', ''))}")
            elif bt == "ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION":
                # 世界议会投票阶段（MUST VOTE）：自动投票+提交（事件处理器注册后自动触发）
                wc = await civ6_tuner.register_wc_voter(timeout=10.0)
                logs.append(f"世界议会：{wc.get('detail', wc.get('error', ''))}")
                if not wc.get("ok"):
                    hard.append((bt, wc.get("detail", "")))
            elif bt == "ENDTURN_BLOCKING_FILL_CIVIC_SLOT":
                fp = await civ6_tuner.auto_fill_policy_slots(timeout=12.0)
                logs.append(f"填补政策槽：{fp.get('detail', fp.get('error', ''))}")
                if not fp.get("ok"):
                    hard.append((bt, fp.get("detail", "")))
            elif bt == "ENDTURN_BLOCKING_PANTHEON":
                pp = await civ6_tuner.auto_choose_pantheon(timeout=12.0)
                logs.append(f"万神殿：{pp.get('detail', pp.get('error', ''))}")
                if not pp.get("ok"):
                    hard.append((bt, pp.get("detail", "")))
            elif bt == "ENDTURN_BLOCKING_GIVE_INFLUENCE_TOKEN":
                ev = await civ6_tuner.auto_send_envoy(timeout=12.0)
                logs.append(f"使节：{ev.get('detail', ev.get('error', ''))}")
                if not ev.get("ok"):
                    hard.append((bt, ev.get("detail", "")))
            elif bt == "ENDTURN_BLOCKING_UNIT_PROMOTION":
                pr = await civ6_tuner.auto_promote_units(timeout=15.0)
                logs.append(f"单位升级：{pr.get('detail', pr.get('error', ''))}")
                if not pr.get("ok"):
                    hard.append((bt, pr.get("detail", "")))
            elif bt == "ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT":
                gp = await civ6_tuner.auto_appoint_governor(timeout=12.0)
                logs.append(f"总督任命：{gp.get('detail', gp.get('error', ''))}")
                if not gp.get("ok"):
                    hard.append((bt, gp.get("detail", "")))
            elif bt == "ENDTURN_BLOCKING_COMMEMORATION_AVAILABLE":
                cm = await civ6_tuner.auto_choose_commemoration(timeout=12.0)
                logs.append(f"时代纪念：{cm.get('detail', cm.get('error', ''))}")
                if not cm.get("ok"):
                    hard.append((bt, cm.get("detail", "")))
            elif bt == "ENDTURN_BLOCKING_STACKED_UNITS":
                # 军事单位堆叠：尝试跳过剩余移动力解除，失败则交还（移动方向是战术决策）
                r = await civ6_tuner.skip_idle_units(force=True, timeout=8.0)
                logs.append(f"堆叠单位：{r.get('detail', '完成')}")
                if not r.get("ok"):
                    hard.append((bt, r.get("detail", "")))
            elif bt == "ENDTURN_BLOCKING_UNITS":
                u = await civ6_tuner.units_blocker_status(timeout=8.0)
                if not u.get("ok"):
                    hard.append((bt, u.get("detail", "")))
                elif u.get("skip_idle_ok"):
                    # 先避险（敌强撤退，自保不引战）→ 建造者自动改良地块 → 再让侦察兵真正探路 → 结束剩余移动力
                    ev = await civ6_tuner.auto_evade_threats(timeout=15.0)
                    logs.append(f"避险：{ev.get('detail', ev.get('error', '无威胁'))}")
                    await asyncio.sleep(1.0)
                    bd = await civ6_tuner.auto_use_builder(timeout=20.0)
                    logs.append(f"建造者：{bd.get('detail', bd.get('error', '待命'))}")
                    await asyncio.sleep(1.0)
                    me = await civ6_tuner.move_explorers(timeout=15.0)
                    logs.append(f"侦察探路：{me.get('detail', me.get('error', '跳过'))}")
                    await asyncio.sleep(2.0)
                    r = await civ6_tuner.skip_idle_units(force=True, timeout=8.0)
                    logs.append(f"单位跳过：{r.get('detail', '跳过完成')}")
                else:
                    if war_flag:
                        hard.append((bt, "战争单位待指挥——打仗归玩家，已交还"))
                    else:
                        r = await civ6_tuner.skip_idle_units(force=True, timeout=8.0)
                        logs.append(f"单位自动跳过（非战争）：{r.get('detail', '完成')}")
            else:
                hard.append((bt, blk.get("message", "")))
        if hard:
            names = "、".join(f"{h}（{m[:36]}）" for h, m in hard)
            return {"handoff": True, "ok": False, "reason": "HARD_BLOCKER",
                    "output": f"存在需要决策/无法自动处理的 blocker：{names}。控制权交还，请处理或调整策略后继续。",
                    "log": logs}
        # 关弹窗（顾问提示/科技完成等会挡住结束回合——实测不关弹窗 ACTION_ENDTURN 被拦截、回合不推进）
        cp = await civ6_tuner.close_ui_popups(timeout=10.0)
        logs.append(f"弹窗清理：{cp.get('detail', cp.get('error', ''))}")
        # v0.6.0 全机制自动（每回合幂等调用，机制未解锁时安全跳过）：
        # 世界议会投票处理器（防 WORLD_CONGRESS_SESSION 卡回合）、花钱、宗教、伟人、商队、间谍、战争响应
        wc = await civ6_tuner.register_wc_voter(timeout=10.0)
        logs.append(f"世界议会：{wc.get('detail', wc.get('error', ''))}")
        mo = await civ6_tuner.auto_manage_money(timeout=12.0)
        logs.append(f"经济自动：{mo.get('detail', mo.get('error', ''))}")
        rg = await civ6_tuner.auto_found_religion(timeout=12.0)
        logs.append(f"宗教：{rg.get('detail', rg.get('error', ''))}")
        gp = await civ6_tuner.auto_recruit_great_person(timeout=12.0)
        logs.append(f"伟人：{gp.get('detail', gp.get('error', ''))}")
        tr = await civ6_tuner.auto_trade_route(timeout=12.0)
        logs.append(f"商队：{tr.get('detail', tr.get('error', ''))}")
        sp = await civ6_tuner.auto_manage_spy(timeout=12.0)
        logs.append(f"间谍：{sp.get('detail', sp.get('error', ''))}")
        wr = await civ6_tuner.auto_war_response(timeout=12.0)
        logs.append(f"战争：{wr.get('detail', wr.get('error', ''))}")
        # endturn 自愈兜底：回合没推进 → 再关弹窗再结束，最多 3 轮（防"新弹窗连环卡死"）
        for attempt in range(3):
            e = await civ6_tuner.end_turn(timeout=8.0)
            logs.append(e.get("detail", "结束回合"))
            if not e.get("ok"):
                return {"handoff": True, "ok": False, "reason": "ENDTURN_ERR",
                        "output": f"结束回合失败：{e.get('detail')}", "log": logs}
            for _ in range(3):
                await asyncio.sleep(3.0)
                s = await civ6_tuner.read_situation(timeout=6.0)
                if s.get("ok") and int(s.get("turn", 0) or 0) > turn_before:
                    return {"ok": True, "handoff": False,
                            "turn_after": int(s.get("turn", 0) or 0), "log": logs}
            # v0.6.2 加固：回合 30+ / mod 环境下 AI 结算实测 >40s（5 文明+9 城邦+
            # R.E.D. mod 拖慢），原 9s 轮询必然误判"未推进"→ 自愈重发 endturn →
            # 3 轮后交还 = 用户看到的"任务跑一半自动停"。放宽到 36s 窗口再判定。
            for _ in range(9):
                await asyncio.sleep(3.0)
                s = await civ6_tuner.read_situation(timeout=6.0)
                if s.get("ok") and int(s.get("turn", 0) or 0) > turn_before:
                    return {"ok": True, "handoff": False,
                            "turn_after": int(s.get("turn", 0) or 0), "log": logs}
            # 未推进：可能冒出新的拦截弹窗（奇观/尤里卡/城邦请求等无固定 Lua 路径的 modal）——
            # 第 2 轮起先做屏幕级右键解除（游戏原生关闭 modal 弹窗路径，实测对奇观弹窗有效；
            # 城邦请求弹窗=取消请求，符合"托管不主动派兵参战"纪律），再关弹窗再结束。
            if attempt < 2:
                if attempt == 1:
                    try:
                        rc = civ6_tuner._right_click_game_window()
                        logs.append(f"自愈右键解除 modal：{rc}")
                    except Exception as exc:
                        logs.append(f"自愈右键失败：{exc}")
                    await asyncio.sleep(1.0)
                cp2 = await civ6_tuner.close_ui_popups(timeout=10.0)
                logs.append(f"自愈重试{attempt + 1}：{cp2.get('detail', cp2.get('error', ''))}")
        return {"handoff": True, "ok": False, "reason": "TURN_NOT_ADVANCED",
                "output": "结束回合后回合数未推进（可能卡在结算/弹窗）。控制权交还，请看一眼游戏。",
                "log": logs}

    @llm_tool(
        name="civ6_ops_tick",
        description=(
            "运营托管中执行本回合（或连续多回合）运营操作。**优先 FireTuner 官方 API 链路**："
            "读阻挡结束回合的通知（blockers：未选生产/未选科技/市政/单位待指挥/世界议会等）→"
            "设置生产（prod_item）/设置科技（tech）/市政（civic）→自动解决软 blocker →"
            "结束回合（ACTION_ENDTURN）→真实回合数验证推进；FireTuner 不可用时才回退屏幕点击。"
            "返回 turn_summaries（每回合真实状态：回合/研究/生产/城市），"
            "汇报必须引用这些真实状态说明自己在干什么、为什么，不得只说'点了下一回合'。"
            "何时用：运营托管中，用户说'继续/执行本回合/点下一回合/你一直玩别停/我吃饭你玩/"
            "你全盘操作'。用户明确'全盘操作/一直玩/托管'时必须 loop_turns=20~50 连续运营，"
            "不得只执行 1 回合就停，也不得只点'下一回合'就汇报（每回合必须实际处理生产/科技等）。"
            "参数 loop_turns：连续运营回合数（默认 1，1-50）；prod_kind/prod_item：城市生产"
            "（默认 unit/UNIT_SCOUT 侦察兵）；tech：科技（默认 TECH_BRONZE_WORKING）；"
            "civic：市政（默认 CIVIC_CODE_OF_LAWS）。决策参数建议先按真实局面由你选定，"
            "默认值仅是兜底。战争/无法自动处理的 blocker/连续 2 次未推进时立即交还控制权。"
            "纪律：禁止编造科技/生产/回合数——一切汇报来自本工具返回的真实状态；"
            "运营期间严禁调用 steam_manager 任何工具。不改存档、不替玩家移动战争单位、不碰战斗。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "loop_turns": {
                    "type": "integer",
                    "description": "连续运营回合数（1-50，默认 1；>1 时自动循环，遇战争/硬 blocker/未推进提前停止）",
                },
                "prod_kind": {"type": "string", "description": "城市生产类别：unit/building/district/project，默认 unit"},
                "prod_item": {"type": "string", "description": "城市生产项目 GameInfo Type，默认 UNIT_SCOUT（侦察兵）"},
                "tech": {"type": "string", "description": "科技 GameInfo Type，默认 TECH_BRONZE_WORKING（青铜工作）"},
                "civic": {"type": "string", "description": "市政 GameInfo Type，默认 CIVIC_CODE_OF_LAWS（法典）"},
            },
            "required": [],
        },
        timeout=900.0,
    )
    async def civ6_ops_tick(self, **kw):
        self._load_config()
        from . import civ6_tuner

        try:
            loop_turns = int(kw.get("loop_turns", 1) or 1)
        except (TypeError, ValueError):
            loop_turns = 1
        if loop_turns < 1:
            loop_turns = 1
        if loop_turns > 50:
            loop_turns = 50
        # 运营决策参数（Neko 按真实局面指定；默认值仅兜底）
        auto_prod_kind = str(kw.get("prod_kind") or "unit").strip().lower()
        auto_prod_item = str(kw.get("prod_item") or "UNIT_SCOUT").strip()
        auto_tech = str(kw.get("tech") or "TECH_BRONZE_WORKING").strip()
        auto_civic = str(kw.get("civic") or "CIVIC_CODE_OF_LAWS").strip()

        # Tuner 可用性（决定主链路：Tuner 官方 API 优先，屏幕点击兜底）
        tuner_ok = False
        try:
            ts = await civ6_tuner.tuner_status()
            tuner_ok = bool(ts.get("ok"))
        except Exception:
            tuner_ok = False

        async def _turn_async() -> int | None:
            """实时回合数：Tuner 优先（游戏运行时准确），失败回退存档（可能滞后）。"""
            try:
                r = await civ6_tuner.read_situation(timeout=8.0)
                if r.get("ok"):
                    return int(r.get("turn", 0) or 0)
            except Exception:
                pass
            try:
                return int(civ6_parser.parse_save("", self._saves_dir).get("game_turn", 0))
            except Exception:
                return None

        async def _semantic_line(turn: int) -> str:
            """从 Tuner 读真实游戏状态，拼一句'我现在在干什么'的语义摘要，
            让托管方（Neko）能读懂并汇报，而不是只知道坐标/日志。"""
            bits = [f"回合{turn}"]
            try:
                r = await civ6_tuner.read_situation(timeout=6.0)
                if r.get("ok"):
                    civ = str(r.get("civ") or "")
                    tech = str(r.get("tech") or "")
                    cities = int(r.get("cities", 0) or 0)
                    if civ:
                        bits.append(f"文明{civ}")
                    if tech:
                        bits.append(f"研究《{tech}》")
                    bits.append(f"城市{cities}座")
            except Exception:
                pass
            try:
                p = await civ6_tuner.read_production(timeout=6.0)
                if p.get("ok") and p.get("cities"):
                    for c in p["cities"][:3]:
                        sz = c.get("size", 0)
                        if sz > 0:
                            bits.append(f"{c.get('current_type') or '#' + str(c.get('hash'))}生产剩{c.get('turns_left')}回")
                        else:
                            bits.append("城市未选生产")
            except Exception:
                pass
            try:
                c = await civ6_tuner.list_cities(timeout=6.0)
                if c.get("ok"):
                    pops = [
                        f"{x.get('name', '?')}人口{x.get('population', '?')}"
                        for x in c.get("cities", [])[:3]
                    ]
                    if pops:
                        bits.append("，".join(pops))
            except Exception:
                pass
            return "；".join(bits)

        async def _tuner_turn(turn_before: int) -> dict:
            """Tuner 完整一回合（复用共享回合管理器）。"""
            return await self._tuner_manage_turn(
                turn_before, war_flag,
                auto_prod_kind, auto_prod_item, auto_tech, auto_civic,
            )

        # 战争预检（Tuner 官方 API；战争即交还——打仗归玩家）
        war_flag = False
        try:
            wr = await civ6_tuner.at_war()
            if wr.get("ok"):
                war_flag = wr.get("at_war", 0) > 0
        except Exception:
            pass
        if war_flag:
            return {"output": "检测到战争状态：运营不接管，打仗归你。战斗结束后说'继续运营'我再接管。",
                    "is_error": False, "handoff": True, "reason": "WAR_TUNER", "active": False}

        t0 = await _turn_async()
        if t0 is None:
            t0 = 0
        logs: list = []
        turns_done = 0
        no_advance = 0
        turn_summaries: list = []
        for i in range(loop_turns):
            if tuner_ok:
                # Tuner 主链路：真实处理生产/科研/blocker，不再只点下一回合
                result = await _tuner_turn(t0)
                logs += result.get("log", [])
                if result.get("handoff"):
                    self._ops_active = False
                    _ops_lock_clear()
                    return {
                        "output": f"已执行 {turns_done} 回合后停止：{result.get('output', '运营停止')}",
                        "is_error": not result.get("ok", False),
                        "log": logs, "handoff": True,
                        "reason": result.get("reason", ""), "turns_done": turns_done,
                        "turn_summaries": turn_summaries, "tuner_mode": True,
                    }
                t1 = result.get("turn_after", t0)
                no_advance = 0
            else:
                # Tuner 不可用：屏幕点击兜底（原逻辑）
                try:
                    # 同步点击段丢到线程执行：内部含阻塞 sleep/截图/点击，
                    # 直接 await 会卡死事件循环（实测 sleep(1s) 被拖到 5.7s）→ 运营"半天不动"。
                    result = await asyncio.to_thread(
                        civ6_ops.run_one_turn_ops,
                        {"saves_dir": self._saves_dir, "buttons": self._ops_buttons},
                        war_state=war_flag if i == 0 else None,
                    )
                except civ6_ops.OpsError as exc:
                    self._ops_active = False
                    _ops_lock_clear()
                    out = _wrap_err(exc)
                    out["handoff"] = True
                    out["turns_done"] = turns_done
                    return out
                logs += result.get("log", [])
                if result.get("handoff"):
                    self._ops_active = False
                    _ops_lock_clear()
                    return {
                        "output": f"已执行 {turns_done} 回合后停止：{result.get('output', '运营停止')}",
                        "is_error": not result.get("ok", False),
                        "log": logs, "handoff": True,
                        "reason": result.get("reason", ""), "turns_done": turns_done,
                        "turn_summaries": turn_summaries, "tuner_mode": False,
                    }
                # 回合推进验证：Tuner 实时回合数
                t1 = await _turn_async()
                if t1 is None:
                    t1 = t0
                if t1 <= t0:
                    no_advance += 1
                    if no_advance >= 2:
                        self._ops_active = False
                        _ops_lock_clear()
                        return {
                            "output": (
                                f"已执行 {turns_done} 回合后停止：连续 2 次点击后回合数未推进"
                                f"（{t0} → {t1}）。可能游戏停在等待确认/结算，或按钮坐标失效。"
                                "请看一眼游戏窗口：若画面正常可再说'继续'重试；有弹窗请手动处理。控制权暂交还。"
                            ),
                            "is_error": True, "error": "TURN_NOT_ADVANCED",
                            "log": logs, "handoff": True, "turn_before": t0, "turn_after": t1,
                            "turns_done": turns_done,
                            "turn_summaries": turn_summaries, "tuner_mode": False,
                        }
                else:
                    no_advance = 0
            # 推进成功：结算
            turns_done += 1
            t0 = t1
            _ops_lock_set()  # 刷新运营锁保鲜——托管持续中不清锁（handoff/战争/错误才清）
            # 语义摘要：让托管方知道每个回合'在干什么'（研究/生产/城市真实状态）
            try:
                sem = await _semantic_line(t0)
                if sem:
                    turn_summaries.append(sem)
                    logs.append(f"运营摘要：{sem}")
            except Exception:
                pass
            # 每回合结算后战争复检（Tuner；战争即交还——打仗归玩家）
            try:
                wr2 = await civ6_tuner.at_war()
                if wr2.get("ok") and wr2.get("at_war", 0) > 0:
                    self._ops_active = False
                    _ops_lock_clear()
                    return {
                        "output": f"已执行 {turns_done} 回合，Tuner 检测到战争状态，控制权交还给你（打仗归你）。",
                        "is_error": False, "handoff": True,
                        "log": logs + [f"结算后 Tuner 战争检测：{wr2.get('at_war')} 个敌对对象"],
                        "turns_done": turns_done,
                        "turn_summaries": turn_summaries, "tuner_mode": tuner_ok,
                    }
            except Exception:
                pass
            if i < loop_turns - 1:
                await asyncio.sleep(0.5)
        # 正常完成：托管保持（锁不清，steam_manager 继续让位）；交还由 handoff 负责
        return {
            "output": f"连续运营完成：共执行 {turns_done}/{loop_turns} 回合（回合推进至 {t0}）。"
                      + ("" if turns_done == loop_turns else "部分回合未推进已提前停止。")
                      + "运营托管保持中；需要继续可再调用 tick，要结束请 civ6_ops_handoff。",
            "is_error": False, "log": logs, "handoff": False,
            "turn_after": t0, "turns_done": turns_done, "turns_target": loop_turns,
            "turn_summaries": turn_summaries, "tuner_mode": tuner_ok,
        }

    @llm_tool(
        name="civ6_ops_citizens",
        description=(
            "精细运营：城市市民/地块分配。何时用：运营托管中，用户说'优化城市人口分配/"
            "让市民去高锤地块/按增长分配'等。参数 target：balanced（综合）/growth（食物优先）/"
            "production（锤子优先），默认 balanced。"
            "执行方式（v0.4.0）：FireTuner 可用时直接读取全部城市真实人口与产出，"
            "然后按目标执行游戏官方焦点分配（市民自动倾斜到目标产出地块，官方 API 级操作，"
            "不点像素、不改存档），并报告执行结果。Tuner 不可用时如实说明并引导装 SDK/开 Tuner。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string",
                           "description": "分配目标：balanced/growth/production，默认 balanced"},
            },
            "required": [],
        },
        timeout=60.0,
    )
    async def civ6_ops_citizens(self, *, target: str = "balanced", **_):
        params = _tool_params(_)
        target = str(target or params.get("target") or "balanced").strip().lower()
        if target not in ("balanced", "growth", "production"):
            target = "balanced"
        from . import civ6_tuner
        # 1) Tuner 是否可用
        status = await civ6_tuner.tuner_status()
        if not status["ok"]:
            return {
                "output": (
                    f"精细运营（市民/地块分配）需要底层通道：当前 FireTuner 不可用"
                    f"（{status.get('detail', '')}）。请先执行 civ6_ensure_sdk(action='check') 看缺口，"
                    "再按提示 install（自动装 SDK）/ fix_tuner（自动开 Tuner），重启游戏后重试。"
                ),
                "is_error": True, "error": status.get("error", "GAME_TUNER_OFF"),
                "channel": "firetuner", "requires_sdk": True,
            }
        # 2) 读真实城市数据
        cities = await civ6_tuner.list_cities()
        if not cities.get("ok"):
            return {"output": f"读取城市数据失败：{cities.get('detail', '')}",
                    "is_error": True, "error": cities.get("error", "GAME_TUNER_OFF"),
                    "channel": "firetuner"}
        if not cities["cities"]:
            return {"output": "Tuner 已连接但未读到城市（可能还没进局/本地玩家不存在）。",
                    "is_error": False, "channel": "firetuner"}
        # 3) 映射 target → 官方焦点
        focus_map = {"balanced": "DEFAULT", "growth": "FOOD", "production": "PRODUCTION"}
        focus = focus_map[target]
        r = await civ6_tuner.set_focus(focus)
        if not r["ok"]:
            return {"output": f"设置焦点失败：{r.get('detail', r.get('error', ''))}",
                    "is_error": True, "error": r.get("error", "GAME_TUNER_OFF"),
                    "channel": "firetuner"}
        applied = {x["city"]: x["focus"] for x in r.get("results", [])}
        lines = ["精细运营执行完成（通道：FireTuner 官方焦点机制）："]
        for c in cities["cities"]:
            y = c["yields"]
            eff = "已应用" if c["name"] in applied else "未收到回执"
            lines.append(
                f"- {c['name']}：人口 {c['population']}，食物 {y.get('food', '?')}，"
                f"生产 {y.get('prod', '?')}，金币 {y.get('gold', '?')}，"
                f"科技 {y.get('science', '?')}，文化 {y.get('culture', '?')}，"
                f"信仰 {y.get('faith', '?')} —— 目标「{target}」（焦点 {focus}）{eff}"
            )
        if target == "production":
            lines.append("市民已自动向高生产地块倾斜；如需恢复均衡执行 target=balanced。")
        elif target == "growth":
            lines.append("市民已自动向高食物地块倾斜（加速人口增长）；如需恢复均衡执行 target=balanced。")
        else:
            lines.append("焦点已清空，恢复系统默认均衡分配。")
        return {"output": "\n".join(lines), "is_error": False,
                "channel": "firetuner", "focus": focus, "applied": applied,
                "cities": cities["cities"]}

    @llm_tool(
        name="civ6_tuner_status",
        description=(
            "检查文明6 FireTuner 底层通道是否可用。何时用：用户问'底层通道/能直接读游戏数据吗'"
            "或精细运营/城市数据读取前先探测。参数无。"
            "需要 Steam 已装 Civ6 SDK（appid 597260）且游戏内启用 Tuner（禁成就），"
            "游戏重启后监听 4318 端口。返回游戏身份与 Lua states 列表或明确错误码。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=20.0,
    )
    async def civ6_tuner_status(self, **_):
        from . import civ6_tuner
        status = await civ6_tuner.tuner_status()
        if not status["ok"]:
            return {
                "output": ("FireTuner 底层通道不可用：请确认 ① Steam 已装『Sid Meier's Civilization "
                           "VI Development Assets』（Tools，appid 597260）；② 游戏内选项开启 "
                           "『Tuner (disables achievements)』（会禁用成就）；③ 重启游戏后端口 4318 "
                           "监听。当前详情：" + str(status.get("detail", ""))),
                "is_error": True, "error": status.get("error", "GAME_TUNER_OFF"),
                "channel": "firetuner",
            }
        return {"output": status["detail"], "is_error": False,
                "app": status.get("app", ""), "states": status.get("states", []),
                "channel": "firetuner"}

    @llm_tool(
        name="civ6_city_details",
        description=(
            "通过 FireTuner 底层通道读取本城全部城市的实时数据（人口 + 食物/生产/金币/科技/文化/信仰）。"
            "何时用：用户问'我的城市现在什么产出/人口多少'，或精细运营前需要真实数据。参数无。"
            "需要 Tuner 可用（见 civ6_tuner_status）；不可用时返回明确错误码。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=30.0,
    )
    async def civ6_city_details(self, **_):
        from . import civ6_tuner
        r = await civ6_tuner.list_cities()
        if not r["ok"]:
            return {"output": f"读取城市数据失败：{r.get('detail', r.get('error', ''))}",
                    "is_error": True, "error": r.get("error", "GAME_TUNER_OFF"),
                    "channel": "firetuner", "cities": []}
        if not r["cities"]:
            return {"output": "Tuner 已连接但未读到城市（可能还没进局/本地玩家不存在）。",
                    "is_error": False, "channel": "firetuner", "cities": []}
        lines = [f"共 {len(r['cities'])} 座城市："]
        for c in r["cities"]:
            y = c["yields"]
            lines.append(
                f"- {c['name']}：人口 {c['population']}，食物 {y.get('food', '?')}，"
                f"生产 {y.get('prod', '?')}，金币 {y.get('gold', '?')}，"
                f"科技 {y.get('science', '?')}，文化 {y.get('culture', '?')}，信仰 {y.get('faith', '?')}"
            )
        return {"output": "\n".join(lines), "is_error": False,
                "channel": "firetuner", "cities": r["cities"]}

    @llm_tool(
        name="civ6_ops_focus",
        description=(
            "通过 FireTuner 为本城全部城市设置产出焦点（游戏官方市民分配机制：市民自动倾向焦点产出）。"
            "何时用：精细运营中，用户说'城市优先生产/优先增长/把人口往高锤地块放'等。"
            "参数 focus：FOOD（增长）/PRODUCTION（生产）/GOLD/SCIENCE/CULTURE/FAITH/DEFAULT（清空）。"
            "需要 Tuner 可用；这是官方 API 级操作，不点像素、不碰战斗。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "focus": {"type": "string",
                          "description": "FOOD/PRODUCTION/GOLD/SCIENCE/CULTURE/FAITH/DEFAULT，默认 FOOD"},
            },
            "required": [],
        },
        timeout=30.0,
    )
    async def civ6_ops_focus(self, *, focus: str = "FOOD", **_):
        params = _tool_params(_)
        focus = str(focus or params.get("focus") or "FOOD").strip()
        from . import civ6_tuner
        r = await civ6_tuner.set_focus(focus)
        if not r["ok"]:
            return {"output": f"设置焦点失败：{r.get('detail', r.get('error', ''))}",
                    "is_error": True, "error": r.get("error", "GAME_TUNER_OFF"),
                    "channel": "firetuner"}
        if not r["results"]:
            return {"output": "焦点已设置但未收到城市回执（可能无本地城市）。",
                    "is_error": False, "channel": "firetuner", "focus": r.get("focus", "")}
        names = "、".join(x["city"] for x in r["results"])
        return {"output": f"已为 {len(r['results'])} 座城市（{names}）设置焦点：{r['focus']}。"
                          "市民会自动向该产出倾斜分配（游戏官方机制）。",
                "is_error": False, "channel": "firetuner",
                "focus": r.get("focus", ""), "results": r["results"]}

    @llm_tool(
        name="civ6_ops_handoff",
        description=(
            "交还运营控制权：退出运营托管（战争开始/用户要自己操作时）。"
            "何时用：用户说'我来打/我自己来/停止托管/交还控制'，或战争检测后。"
            "参数无。只置状态，不操作游戏。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        timeout=15.0,
    )
    async def civ6_ops_handoff(self, **_):
        self._ops_active = False
        _ops_lock_clear()
        return {"output": "运营托管已结束，控制权交还给你。需要再托管时说'接管运营'。",
                "is_error": False, "active": False}


@plugin_entry()
def create_plugin(ctx):
    return Civ6BuddyPlugin(ctx)
