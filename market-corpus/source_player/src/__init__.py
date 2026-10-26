"""N.E.K.O plugin facade: let the catgirl actually play Half-Life / Portal.

The facade owns only plumbing: lifecycle wiring, the LLM tool surface and the
Plugin Center entries.  Capture, game discovery, the reactive policy and the
proactive narration all live in :class:`~plugin.plugins.source_player.service.SourceService`.

Safety defaults (all changeable in ``plugin.toml``):

* the plugin never launches a game -- the user opens it first;
* enabling the plugin does not start playing -- ``source_play`` does;
* no key is injected unless the game window is in the foreground;
* F8 stops/resumes, and touching WASD/fire makes the catgirl yield immediately.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
    ui,
)

from . import catalog, prompts
from .service import SourceService

# ---------------------------------------------------------------------------
# JSON schemas (module level so tests/metadata see exactly what the LLM sees)
# ---------------------------------------------------------------------------

PLAY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "game": {
            "type": "string",
            "description": (
                "主人说的那部游戏，按原话传进来。例如：半条命、半条命重制版、黑山、"
                "半条命2、半条命2第一章、半条命2第二章、传送门、传送门2、Portal 2。"
                "不要自己改成别的游戏。不传时：如果正好有一部支持的游戏在运行，就接管它；"
                "否则会反问玩哪部。"
            ),
        },
        "objective": {
            "type": "string",
            "description": "可选：这一局的目标，例如「打到下一关」「解开这个测试室」。",
        },
    },
    "required": [],
}

PLAY_DESCRIPTION = (
    "让猫娘开始玩《半条命》系列或《传送门》系列。**游戏必须由主人先手动打开**；"
    "本工具只接管已经运行的游戏窗口，不会自己启动游戏。\n\n"
    "调用时机：主人说「去玩半条命」「玩传送门2」「黑山启动」「你来打半条命重制版」"
    "这类**开始/切换游戏**的要求。\n\n"
    "参数：\n"
    "  game: 主人说的游戏名（原话），例如 半条命 / 半条命重制版 / 黑山 / 半条命2 / 传送门2。\n"
    "  objective: 可选目标；不传就用默认「沿主线推进」。\n\n"
    "返回里会带 strategy（这部游戏怎么打）和 installed/running/status。"
    "如果游戏没开，返回会说明「等待主人手动打开」，不要谎称已经在玩。"
)

COMMAND_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "instruction": {
            "type": "string",
            "description": (
                "主人的原话，例如：往前走 / 往右走 / 后退 / 打 / 按 E / 跳 / 蹲下 / "
                "开手电 / 换手枪 / 打蓝门 / 打橙门 / 停手 / 继续 / 看右边。"
                "不要自己补充主人没说的动作。"
            ),
        },
        "seconds": {
            "type": "number",
            "description": "可选：这个动作持续多久；0 表示用默认时长。",
        },
    },
    "required": ["instruction"],
}

COMMAND_DESCRIPTION = (
    "让猫娘在游戏里立刻做一个具体动作（不是改长期目标）。\n\n"
    "调用时机：主人说「往前走一点」「打它」「按 E」「跳过去」「蹲下」「打蓝门」"
    "这类**当前画面里的操作**；或者主人中途加入、想和猫娘一起操作时。\n\n"
    "不要用它回答状态问题（用 source_status）、不要用它开始游戏（用 source_play）、"
    "不要重复发送同一条已经执行过的指令。\n\n"
    "参数：\n"
    "  instruction: 主人原话；支持前进/后退/左右/跳/蹲/冲刺/开火/换弹/使用/手电/"
    "蓝门/橙门/重力枪/武器切换/停手/继续。\n"
    "  seconds: 可选持续秒数。"
)

LOOK_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "note": {"type": "string", "description": "可选：为什么要看，只写进日志。"},
    },
    "required": [],
}

LOOK_DESCRIPTION = (
    "立刻截一张当前游戏画面并送进视觉上下文，返回画面特征和 HUD 判断。\n\n"
    "调用时机：主人问「现在什么情况」「打到哪了」「你看到什么」；"
    "或者你自己想确认当前画面/卡住时。不要连续调用很多次。\n\n"
    "注意：插件不会在本地识别血量数字；血量/弹药请从截图里读，读不到就说读不到。"
)

AIM_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "x": {"type": "number", "description": "目标在画面里的横向位置，0=最左，1=最右；也接受像素坐标。"},
        "y": {"type": "number", "description": "目标在画面里的纵向位置，0=最上，1=最下；也接受像素坐标。"},
        "fire": {"type": "boolean", "description": "转过去后是否立刻开火，默认 false。"},
        "button": {
            "type": "string",
            "enum": ["left", "right"],
            "description": "开火键；传送门里 left=蓝门，right=橙门。",
        },
    },
    "required": ["x", "y"],
}

AIM_DESCRIPTION = (
    "把视角转向画面中的某个位置（归一化坐标 0..1），可选马上开火。\n\n"
    "调用时机：你在截图里看到一个敌人/按钮/可放置白面，想精确对准它；"
    "或者 source_look 之后发现准星不在目标上。\n\n"
    "用法：先 source_look 看画面 → 估算目标在画面里的 x/y → 调用本工具；"
    "如果没对准，再调用一次微调。传送门里把 button 设为 left（蓝门）或 right（橙门）。\n\n"
    "注意：这是相对转动视角，不是移动鼠标指针；坐标只需大概，二次调用会逐渐收敛。"
)

SHOOT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "portal": {
            "type": "string",
            "enum": ["blue", "orange", "left", "right", "primary", "secondary"],
            "description": "blue/left/primary=左键（蓝门），orange/right/secondary=右键（橙门）。",
        },
    },
    "required": [],
}

SHOOT_DESCRIPTION = (
    "按一下开火键。传送门系列里 blue=左键蓝门、orange=右键橙门；"
    "半条命系列里就是普通攻击/副攻击。\n\n"
    "调用时机：准星已经对准目标，只需要开火；或者你要补一扇传送门。"
    "不要在对不准的时候连点，先 source_aim。"
)

DIRECTIVE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal": {
            "type": "string",
            "description": "这一阶段的目标，例如「找到出口」「解开这个测试室」「清掉这波敌人」。",
        },
        "steps": {
            "type": "string",
            "description": "可选：用中文分号/换行分隔的 1-5 步计划，例如「先看右边；打蓝门；跳过去」。",
        },
        "mode": {
            "type": "string",
            "enum": ["explore", "fight", "puzzle", "vehicle", "wait"],
            "description": "策略模式；解谜关用 puzzle，战斗用 fight，赶路用 explore。",
        },
        "seconds": {
            "type": "number",
            "description": "这个策略保持多久；0/不传表示默认 30 秒。",
        },
    },
    "required": ["goal"],
}

DIRECTIVE_DESCRIPTION = (
    "设置猫娘这一阶段的**高层目标和打法**（不是单个按键）。\n\n"
    "调用时机：你从截图判断出当前该做什么，例如「这个房间要先用蓝门+橙门搭路」"
    "「先清掉这波敌人」「沿走廊找出口」；主人说「你去解谜」「先打怪」也用这个。\n\n"
    "它会更新状态里的 objective/plan，并让本地策略切换模式；"
    "之后你仍可以用 source_command 做具体动作。\n\n"
    "不要每次解说都调用；只在目标/打法真的变化时调用。"
)

SEQUENCE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "maxItems": 12,
            "description": (
                "按顺序执行的动作。每项 action 可以是：forward/back/left/right/"
                "jump/crouch/use/fire/reload/flashlight/portal_blue/portal_orange，"
                "或 aim（配 x/y）、shoot（配 portal）、wait（配 seconds）。"
                "整个序列最多约 8 秒。"
            ),
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "seconds": {"type": "number"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "portal": {"type": "string", "enum": ["blue", "orange"]},
                },
                "required": ["action"],
            },
        }
    },
    "required": ["actions"],
}

SEQUENCE_DESCRIPTION = (
    "执行一小段连续动作（最多 12 步、约 8 秒），适合传送门解谜这种"
    "「先转过去→打蓝门→再转→打橙门→往前走」的组合操作。\n\n"
    "调用时机：你已经从截图想清楚一个短序列，想一次性做完；"
    "或者主人说「你按这个顺序试一下」。\n\n"
    "每一步可以是：\n"
    "  {\"action\": \"forward\", \"seconds\": 1.2}\n"
    "  {\"action\": \"aim\", \"x\": 0.62, \"y\": 0.40}\n"
    "  {\"action\": \"shoot\", \"portal\": \"blue\"}\n"
    "  {\"action\": \"use\"} / jump / crouch / wait / portal_blue / portal_orange\n\n"
    "玩家一碰键鼠，序列会很快（通常 0.1–0.4 秒内）停下并把控制权交还。"
)

STATUS_SCHEMA: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

STATUS_DESCRIPTION = (
    "查询插件和游戏状态：选了哪部、是否在运行、窗口是否在前台、截图后端、"
    "当前策略状态、最近动作、HUD/画面特征、是否被玩家接管、最近错误。\n\n"
    "调用时机：主人问「连上了吗」「现在在干嘛」「怎么没反应」「打到哪了」；"
    "或者你自己不确定当前状态时。"
)

STOP_SCHEMA: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

STOP_DESCRIPTION = (
    "让猫娘立刻停手：松开所有按键、停止自动操作，把键盘还给主人。\n\n"
    "调用时机：主人说「别玩了」「停手」「我来」「暂停」「先别打」。"
    "不会关闭游戏，也不会丢存档；之后可以说「继续」或再调 source_resume。"
)

RESUME_SCHEMA: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

RESUME_DESCRIPTION = (
    "让猫娘重新接管已经选定的游戏（在 source_stop 或玩家接手之后）。\n\n"
    "调用时机：主人说「继续」「你接着玩」「交给你」。如果还没选游戏，先用 source_play。"
)

GAMES_SCHEMA: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

GAMES_DESCRIPTION = (
    "列出插件支持的半条命/传送门系列、Steam 是否安装、当前是否在运行，"
    "以及哪些是 VR/多人所以不能玩。\n\n"
    "调用时机：主人问「你能玩哪些」「我装了哪些」「有没有黑山」；"
    "或者 source_play 没认出游戏名时。"
)

SELFCHECK_SCHEMA: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}

SELFCHECK_DESCRIPTION = (
    "做一次只读环境自检：Steam 库、游戏窗口、前台状态、截图后端逐个探测、"
    "输入结构体、配置。不会按键、不会启动游戏。\n\n"
    "调用时机：主人说「检查一下」「怎么截不到画面」「按键没反应」，"
    "或者第一次使用前确认环境。"
)


# ---------------------------------------------------------------------------
# Tool result helpers
# ---------------------------------------------------------------------------

def _tool_error(code: str, message: str) -> Dict[str, Any]:
    """Return the documented LLM-tool error shape.

    ``is_error`` tells the dialogue model the call failed, and ``error`` carries
    a stable code instead of leaking raw exception text into the conversation.
    """
    return {"output": {"reason": message}, "is_error": True, "error": code}


def _ok_or_err(result: Any) -> Any:
    """Map a tool result to ``Ok``/``Err`` for ``@plugin_entry`` handlers."""
    if isinstance(result, dict) and result.get("is_error"):
        output = result.get("output")
        reason = output.get("reason") if isinstance(output, dict) else None
        return Err(SdkError(str(reason or result.get("error") or "tool failed")))
    return Ok(result)


@neko_plugin
class SourcePlayerPlugin(NekoPluginBase):
    """Plugin facade: lifecycle + tool entries only."""

    def __init__(self, ctx):
        super().__init__(ctx)
        log_level = (os.environ.get("NEKO_LOG_LEVEL") or "INFO").strip().upper()
        try:
            self.file_logger = self.enable_file_logging(log_level=log_level)
        except ValueError:
            self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger

        self._cfg: Dict[str, Any] = {}
        self._lang: str = prompts.DEFAULT_LANG
        self._service = SourceService(
            logger=self.logger,
            push_message_fn=self.push_message,
            data_dir=self.data_path(),
        )
        self._service_started = False
        self._service_start_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lazy start (host's long-lived loop; never from the startup hook)
    # ------------------------------------------------------------------

    async def _ensure_service_started(self) -> bool:
        async with self._service_start_lock:
            if self._service_started:
                return True
            try:
                await self._service.start()
                self._service_started = True
                return True
            except Exception as exc:
                self.logger.warning(
                    "[startup] service.start failed; will retry — {}: {}",
                    type(exc).__name__, exc,
                )
                return False

    async def _on_command_loop_start(self) -> None:
        try:
            await self._ensure_service_started()
        except Exception as exc:
            self.logger.warning(
                "[eager-start] failed; lazy start remains — {}: {}",
                type(exc).__name__, exc,
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        raw = cfg.get("source", {}) if isinstance(cfg.get("source"), dict) else {}
        self._cfg = raw
        self._lang = prompts.user_lang()
        self._service.set_lang(self._lang)
        self._service.configure(raw)
        installed = self._service.installed_ids()
        running = self._service.running_ids()
        return Ok({
            "status": "ready",
            "lang": self._lang,
            "installed": installed,
            "running": running,
            "autopilot_enabled": self._service.cfg.autopilot_enabled,
            "dry_run": self._service.cfg.dry_run,
        })

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        if self._service_started:
            try:
                await self._service.stop()
            except Exception as exc:
                self.logger.warning(
                    "[shutdown] service.stop raised — {}: {}", type(exc).__name__, exc)
            self._service_started = False
        return Ok({"status": "shutdown"})

    @lifecycle(id="config_change")
    async def config_change(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        raw = cfg.get("source", {}) if isinstance(cfg.get("source"), dict) else {}
        self._cfg = raw
        self._service.configure(raw)
        return Ok({"status": "reconfigured", "dry_run": self._service.cfg.dry_run})

    # ------------------------------------------------------------------
    # LLM tools
    # ------------------------------------------------------------------

    @llm_tool(name="source_play", description=PLAY_DESCRIPTION,
              parameters=PLAY_SCHEMA, timeout=30.0)
    async def source_play_tool(self, *, game: Any = None, objective: Any = "", **_):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来，看插件日志。")

        installed = self._service.installed_ids()
        # Force a fresh running-game scan before the "no game name" fallback;
        # otherwise a Plugin Center button would see a stale empty list.
        self._service.get_status()
        running = self._service.running_ids()
        if not isinstance(game, str) or not game.strip():
            # A Plugin Center button has no argument to type into.  If exactly
            # one supported game is already running, adopt it; if several are
            # running, do not guess which one the user meant.
            if len(running) == 1:
                game = catalog.GAME_BY_ID[running[0]].name_zh
            elif len(running) > 1:
                names = "、".join(
                    catalog.GAME_BY_ID[gid].name_zh for gid in running
                    if gid in catalog.GAME_BY_ID
                )
                return {
                    "summary": (
                        f"检测到多部游戏在运行：{names}。请主人说清玩哪一部，"
                        "比如「玩传送门2」；我不会自己猜。"
                    ),
                    "running": running,
                }
            else:
                names = catalog.describe_installed(installed)
                return {
                    "summary": (
                        "没说玩哪一部。现在检测到已安装：" + names +
                        "。请说「去玩半条命」或「玩传送门2」，并先手动打开游戏。"
                    ),
                    "installed": [
                        catalog.GAME_BY_ID[gid].summary()
                        for gid in installed if gid in catalog.GAME_BY_ID
                    ],
                }
        resolution = catalog.resolve_game(game, installed=installed, running=running)
        if resolution.unsupported is not None:
            profile = resolution.unsupported
            reason = "VR 专用，需要头显和双手柄" if profile.vr_only else "多人/合作模式，需要其他玩家"
            return {
                "summary": (
                    f"《{profile.name_zh}》不能这样玩：{reason}。"
                    "我没有假装能操作它。可以换《半条命》《黑山》《半条命2》或《传送门2》。"
                ),
                "game": profile.summary(),
                "supported": False,
            }
        if resolution.game is None:
            names = catalog.describe_installed(installed)
            return {
                "summary": (
                    f"没认出要玩哪部游戏（用户说：{str(game)[:50]!r}）。"
                    f"现在检测到已安装：{names}。请主人说得更具体一点，"
                    "比如「半条命2」「黑山」「传送门2」。"
                ),
                "installed": [
                    catalog.GAME_BY_ID[gid].summary()
                    for gid in installed if gid in catalog.GAME_BY_ID
                ],
            }

        profile = resolution.game
        if not profile.playable:
            return {"summary": f"《{profile.name_zh}》不支持自动游玩。"}

        if profile.id not in set(installed) and profile.id not in set(running):
            names = catalog.describe_installed(installed)
            return {
                "summary": (
                    f"没在 Steam 库里找到《{profile.name_zh}》。现在检测到已安装：{names}。"
                    "请主人先在 Steam 安装这部游戏，或者换一部已安装的；"
                    "如果它是非 Steam 版并且已经打开，再让我试一次。"
                ),
                "game": profile.summary(),
                "installed": [
                    catalog.GAME_BY_ID[gid].summary()
                    for gid in installed if gid in catalog.GAME_BY_ID
                ],
            }

        result = self._service.play(profile, objective=str(objective or "").strip())
        status = result["status"]
        running_now = bool(result.get("running"))
        assumption = ""
        if resolution.assumed:
            assumption = (
                f"（我把「{str(game)[:30]}」理解成《{profile.name_zh}》；"
                "如果主人指的是别的作品，告诉我具体名字。）"
            )
        menu_now = bool(status.get("menu_active"))
        if running_now and menu_now:
            summary = (
                f"已经找到《{profile.name_zh}》的窗口，但画面停在主菜单/暂停菜单。"
                "请主人先点进关卡或读档；进入游戏后我就接手。"
                "**我不会替你点「新游戏」覆盖进度。**"
                f"{assumption}"
            )
        elif running_now:
            summary = (
                f"已接管《{profile.name_zh}》的窗口，目标：{result['objective']}。"
                f"{assumption} 我开始看画面、按自己的判断推进；主人随时可以接手。"
            )
        else:
            other_running = [
                catalog.GAME_BY_ID[gid].name_zh
                for gid in running
                if gid != profile.id and gid in catalog.GAME_BY_ID
            ]
            extra = ""
            if other_running:
                extra = f"（检测到《{'、'.join(other_running)}》在运行，但我要玩的是《{profile.name_zh}》。）"
            summary = (
                f"已选定《{profile.name_zh}》，但还没检测到它的窗口。"
                f"请主人**手动打开《{profile.name_zh}》并进入关卡/读档**；"
                "窗口出现后我会自动开始，不会再催指令。"
                f"{extra}{assumption}（这不是故障。）"
            )
        if self._service.cfg.dry_run:
            summary += " 现在是 dry_run 演练模式：只观察、不按键。"
        return {
            "summary": summary,
            "game": profile.summary(),
            "strategy": profile.strategy_brief(max_chapters=6),
            "chapters": [chapter.as_dict() for chapter in profile.chapters[:8]],
            "waiting_for_game": not running_now,
            "assumed": resolution.assumed,
            "running": status.get("running"),
            "status": status,
        }

    @llm_tool(name="source_command", description=COMMAND_DESCRIPTION,
              parameters=COMMAND_SCHEMA, timeout=20.0)
    async def source_command_tool(self, *, instruction: Any = None, seconds: Any = 0, **_):
        if not isinstance(instruction, str) or not instruction.strip():
            return _tool_error("INVALID_ARGUMENT", "source_command 需要一句具体操作，比如「往前走」「打」「按 E」。")
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来，看插件日志。")
        try:
            seconds_value = float(seconds) if seconds not in (None, "") else 0.0
        except (TypeError, ValueError):
            seconds_value = 0.0
        result = self._service.apply_command(instruction.strip(), seconds=seconds_value)
        return result

    @llm_tool(name="source_look", description=LOOK_DESCRIPTION,
              parameters=LOOK_SCHEMA, timeout=25.0)
    async def source_look_tool(self, *, note: Any = "", **_):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来，看插件日志。")
        return self._service.look(note=str(note or ""))

    @llm_tool(name="source_aim", description=AIM_DESCRIPTION,
              parameters=AIM_SCHEMA, timeout=20.0)
    async def source_aim_tool(self, *, x: Any = 0.5, y: Any = 0.5,
                              fire: Any = False, button: Any = "left", **_):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来。")
        try:
            xf, yf = float(x), float(y)
        except (TypeError, ValueError):
            return _tool_error("INVALID_ARGUMENT", "x/y 需要是数字（0..1 或像素坐标）。")
        if not self._service.get_status().get("running"):
            return {"summary": "还没有正在运行的游戏窗口；先让主人打开游戏并 source_play。"}
        return self._service.aim(xf, yf, fire=bool(fire), button=str(button or "left"))

    @llm_tool(name="source_shoot", description=SHOOT_DESCRIPTION,
              parameters=SHOOT_SCHEMA, timeout=15.0)
    async def source_shoot_tool(self, *, portal: Any = "primary", **_):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来。")
        if not self._service.get_status().get("running"):
            return {"summary": "还没有正在运行的游戏窗口；先让主人打开游戏并 source_play。"}
        return self._service.shoot(str(portal or "primary"))

    @llm_tool(name="source_directive", description=DIRECTIVE_DESCRIPTION,
              parameters=DIRECTIVE_SCHEMA, timeout=20.0)
    async def source_directive_tool(self, *, goal: Any = None, steps: Any = "",
                                    mode: Any = "explore", seconds: Any = 0, **_):
        if not isinstance(goal, str) or not goal.strip():
            return _tool_error("INVALID_ARGUMENT", "source_directive 需要一句目标，比如「找到出口」。")
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来。")
        try:
            seconds_value = float(seconds) if seconds not in (None, "") else 0.0
        except (TypeError, ValueError):
            seconds_value = 0.0
        return self._service.set_directive(
            mode=str(mode or "explore"),
            goal=goal.strip(),
            steps=str(steps or ""),
            seconds=seconds_value,
        )

    @llm_tool(name="source_sequence", description=SEQUENCE_DESCRIPTION,
              parameters=SEQUENCE_SCHEMA, timeout=25.0)
    async def source_sequence_tool(self, *, actions: Any = None, **_):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来。")
        return await self._service.run_sequence(actions)

    @llm_tool(name="source_stop", description=STOP_DESCRIPTION,
              parameters=STOP_SCHEMA, timeout=15.0)
    async def source_stop_tool(self, **_):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来。")
        if self._service._game is None:  # noqa: SLF001 - cheap state check
            return {"summary": "现在还没有在玩；不用停。"}
        status = self._service.stop_playing(reason="tool")
        return {"summary": "停手了，所有按键都松开了。想继续就说「继续」。", "status": status}

    @llm_tool(name="source_resume", description=RESUME_DESCRIPTION,
              parameters=RESUME_SCHEMA, timeout=15.0)
    async def source_resume_tool(self, **_):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来。")
        result = self._service.resume_playing(reason="tool")
        return result

    @llm_tool(name="source_status", description=STATUS_DESCRIPTION,
              parameters=STATUS_SCHEMA, timeout=15.0)
    async def source_status_tool(self, **_):
        try:
            status = self._service.get_status()
            return status
        except Exception as exc:
            self.logger.exception("[status] failed")
            return _tool_error("INTERNAL", type(exc).__name__)

    @llm_tool(name="source_games", description=GAMES_DESCRIPTION,
              parameters=GAMES_SCHEMA, timeout=20.0)
    async def source_games_tool(self, **_):
        self._service.get_status()
        installed = set(self._service.installed_ids())
        running = set(self._service.running_ids())
        games = []
        for profile in catalog.GAMES:
            item = profile.summary()
            item["installed"] = profile.id in installed
            item["running"] = profile.id in running
            games.append(item)
        installed_names = [
            catalog.GAME_BY_ID[gid].name_zh for gid in installed if gid in catalog.GAME_BY_ID
        ]
        return {
            "summary": (
                "支持：半条命系列（含 HL1 资料片、黑山、HL2、第一章、第二章、Lost Coast）"
                "与传送门 1/2。"
                f" 已安装：{'、'.join(installed_names) or '没检测到'}。"
                "《半条命：爱莉克斯》是 VR 专用，不能键鼠自动玩；合作模式也不支持。"
            ),
            "games": games,
            "installed": sorted(installed),
            "running": sorted(running),
        }

    @llm_tool(name="source_selfcheck", description=SELFCHECK_DESCRIPTION,
              parameters=SELFCHECK_SCHEMA, timeout=45.0)
    async def source_selfcheck_tool(self, **_):
        started = await self._ensure_service_started()
        if not started:
            return _tool_error("UNAVAILABLE", "插件服务起不来。")
        checks = await self._service.selfcheck()
        problems = checks.get("problems") or []
        summary = "自检通过。" if not problems else ("自检发现问题：" + "；".join(problems[:4]))
        return {"summary": summary, "checks": checks}

    # ------------------------------------------------------------------
    # Plugin Center entries (GUI actions / intent dispatch)
    # ------------------------------------------------------------------

    @ui.action(id="source_play", label="让猫娘玩半条命/传送门", icon="play",
               tone="primary", order=10)
    @plugin_entry(
        id="source_play",
        name="开始玩半条命/传送门",
        description=(
            "让猫娘接管已经打开的半条命/传送门系列。**不会自己启动游戏**："
            "主人先手动打开并进入关卡，然后说「去玩半条命」「玩传送门2」。"
            "调用后如果游戏还没开，插件会等待窗口出现。"
        ),
        input_schema=PLAY_SCHEMA,
        llm_result_fields=["summary"],
    )
    async def source_play_entry(self, *, game: Any = None, objective: Any = "", **_):
        result = await self.source_play_tool(game=game, objective=objective)
        return _ok_or_err(result)

    @ui.action(id="source_stop", label="让猫娘停手", icon="stop",
               tone="danger", order=20)
    @plugin_entry(
        id="source_stop",
        name="让猫娘停手",
        description=(
            "立刻停止自动操作并松开所有按键，把键盘还给主人。"
            "适用于「别玩了」「停手」「我来」「暂停」。不会关闭游戏。"
        ),
        input_schema=STOP_SCHEMA,
        llm_result_fields=["summary"],
    )
    async def source_stop_entry(self, **_):
        result = await self.source_stop_tool()
        return _ok_or_err(result)

    @plugin_entry(
        id="source_resume",
        name="让猫娘继续玩",
        description=(
            "在停手/玩家接手之后，让猫娘重新接管已经选定的游戏。"
            "适用于「继续」「你接着玩」「交给你」。"
        ),
        input_schema=RESUME_SCHEMA,
        llm_result_fields=["summary"],
    )
    async def source_resume_entry(self, **_):
        result = await self.source_resume_tool()
        return _ok_or_err(result)

    @ui.action(id="source_status", label="刷新状态", icon="refresh", order=30)
    @plugin_entry(
        id="source_status",
        name="查询半条命/传送门插件状态",
        description=(
            "查询当前选中的游戏、是否运行、窗口是否在前台、截图后端、"
            "策略状态、最近动作、玩家是否接手、最近错误。"
        ),
        input_schema=STATUS_SCHEMA,
        llm_result_fields=["summary"],
    )
    async def source_status_entry(self, **_):
        status = await self.source_status_tool()
        return _ok_or_err(status)

    @plugin_entry(
        id="source_look",
        name="看一眼当前游戏画面",
        description=(
            "截一张当前游戏画面并送进视觉上下文，返回画面特征。"
            "适用于「现在什么情况」「你看到什么」。"
        ),
        input_schema=LOOK_SCHEMA,
        llm_result_fields=["summary"],
    )
    async def source_look_entry(self, *, note: Any = "", **_):
        result = await self.source_look_tool(note=note)
        return _ok_or_err(result)

    @plugin_entry(
        id="source_selfcheck",
        name="半条命/传送门环境自检",
        description=(
            "只读自检：Steam 库、游戏窗口、前台、截图后端、输入结构体、配置。"
            "不会按键、不会启动游戏。"
        ),
        input_schema=SELFCHECK_SCHEMA,
        llm_result_fields=["summary"],
    )
    async def source_selfcheck_entry(self, **_):
        result = await self.source_selfcheck_tool()
        return _ok_or_err(result)
