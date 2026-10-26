from __future__ import annotations

import asyncio
from typing import Any, Optional

from . import addressing
from .feedback_classifier import QQFeedbackClassifier
from .pipeline_models import (
    KIND_GROUP_BAN_NOTICE,
    KIND_GROUP_JOIN_NOTICE,
    QQReplyRequest,
)

#: 开放平台通道的观测值。真相在 ``QQOpenPlatformConnection.CHANNEL``，这里抄一
#: 份而不是 import，是为了不把 websockets / httpx 拖进本模块的导入链；两者相
#: 等由测试钉死。
_OPEN_PLATFORM_CHANNEL = "open"


class QQMessageDispatcher:
    #: 一个群里连续出现多少个**互不相同**的说话人、且无一对得上名册，才认为
    #: 「这个群从来认不出任何已登记用户」值得报一条。1 个陌生人本来就该认不
    #: 出，那是正常的；一连几个都认不出、而名册里明明有管理员，才是信号。
    OPEN_PLATFORM_SCOPE_ALARM_SPEAKERS = 3
    #: 最多跟踪多少个群的告警状态，避免长期运行时无界增长。
    OPEN_PLATFORM_SCOPE_ALARM_MAX_GROUPS = 128
    #: 「未认领的群内 ID」池的上限（群数 × 每群人数）。这个池是给人看的一份
    #: 待办清单，不是账本：满了就按最久未见淘汰，丢一条的代价只是那个人要再
    #: 发一次言才重新出现在列表里。
    OPEN_PLATFORM_CLAIM_MAX_GROUPS = 64
    OPEN_PLATFORM_CLAIM_MAX_PER_GROUP = 32

    #: 戳一戳：多久内的戳算同一场「风暴」（只用于留痕，不改变行为 ——
    #: 现在不管几个人戳都只跟戳、不回话）。
    POKE_STORM_WINDOW_SECONDS = 30.0
    #: 同一窗口内多少个**互不相同**的人戳她，日志里才写一句"风暴"。
    POKE_STORM_MIN_POKERS = 2
    #: 跟戳的闸：同一个人在这个窗口内最多被回戳几次。
    #: 「跟戳」不等于陪到底 —— 没有这道闸就是无限互戳（对方戳一下、她戳一下…）。
    POKE_BACK_WINDOW_SECONDS = 300.0
    POKE_BACK_MAX_PER_POKER = 2
    #: 「跟戳别人之间的戳」时，同一个群两次跟戳的最小间隔。
    #: 没有这条限速，5 个人连戳时她会在同一秒里把所有被戳的人都戳一遍 ——
    #: 像机关枪，不像人。**戳她本人的回戳不受它限制**（那是对她的动作，该立刻回应）。
    POKE_FOLLOW_MIN_INTERVAL_SECONDS = 15.0

    #: 合成轮里**绕过门控**的那几种：它们"该不该开这一轮"已经在脚本里判完了
    #: （新人入群该欢迎；禁言反应的对象判断见 `_maybe_react_to_ban`），到这里再让
    #: 注意力闸 / 必要性闸审一遍，等于同一件事被两套口径各判一次。
    #: 使用者口径（2026-09-29）：「派发层通过再让猫娘决定说不说」。
    GATE_BYPASS_SYNTHETIC_SOURCES = frozenset({KIND_GROUP_JOIN_NOTICE, KIND_GROUP_BAN_NOTICE})

    #: 上面那批里**还要必定回复**的：只有入群欢迎 —— 那是她必须打的招呼（改动前就是
    #: 这个行为）。禁言反应**不在此列**：派发层判完"这是正在跟她对话的人"之后，
    #: 说不说由模型自己决定（它可以什么都不发）。替她决定就等于把"反应"变成"必须开口"。
    FORCED_SYNTHETIC_SOURCES = frozenset({KIND_GROUP_JOIN_NOTICE})

    #: 禁言/解禁反应的冷却（秒）：**每群一次**，且同一人同一事件只反应一次。
    #: 使用者口径（2026-09-29）：「每群冷却 600 秒 + 同一事件只反应一次」。
    #: 需要它是因为每次反应 = 一次 LLM 调用，而活跃群里有人被管理处理并不罕见。
    BAN_REACTION_COOLDOWN_SECONDS = 600.0

    def __init__(self, plugin: Any):
        self.plugin = plugin
        #: 禁言反应的节流：``{group:user:sub_type: 上次反应时刻}`` 与 ``{group: 上次反应时刻}``。
        #: 内存态、重启即失（与戳一戳的三道闸同一性质）。
        self._ban_reactions: dict[str, float] = {}
        self._ban_group_last: dict[str, float] = {}
        self._open_platform_bootstrap_lock = asyncio.Lock()
        #: ``{group_id: {sender_id: {first_seen, last_seen, count, nickname}}}``
        #: 只进内存、不落盘：它是「现在还没认领的人」，重启后由新消息自然重
        #: 建。落盘等于把一份 openid 名单永久化，而这些 id 正是敏感的那类。
        self._open_platform_pending_claims: dict[str, dict[str, dict]] = {}
        #: 群 → 上次「跟戳别人之间的戳」的时刻（限速用，内存态即可）。
        self._last_poke_follow: dict[str, float] = {}

    async def _maybe_reserve_open_platform_admin(
        self, message: dict[str, Any],
    ) -> None:
        if message.get("message_type") != "private":
            return
        qq_client = getattr(self.plugin, "qq_client", None)
        permission_mgr = getattr(self.plugin, "permission_mgr", None)
        sender_id = str(message.get("user_id") or "").strip()
        if (
            qq_client is None
            or qq_client.needs_attention
            or permission_mgr is None
            or not sender_id
        ):
            return
        async with self._open_platform_bootstrap_lock:
            # Another queued message from the same first user may have been
            # receipt-stamped before this dispatcher promoted the winner.  Do
            # not broaden this to arbitrary later permission changes: only the
            # admin reserved by this process's bootstrap may inherit that
            # bootstrap receipt.
            if getattr(self, "_open_platform_bootstrap_admin_id", None) == sender_id:
                if permission_mgr.get_permission_level(sender_id) == "admin":
                    message["_private_permission_level_at_receipt"] = "admin"
                    return
                # Permission changes are authoritative.  Expire the bootstrap
                # shortcut immediately so a removed/demoted first user can
                # never read owner memory through a stale receipt override.
                self._open_platform_bootstrap_admin_id = None
            if permission_mgr.list_users():
                return
            permission_mgr.add_user(
                sender_id,
                "admin",
                message.get("user_nickname") or "管理员",
            )
            self.plugin._refresh_admin_qq()
            self._open_platform_bootstrap_admin_id = sender_id
            message["_private_permission_level_at_receipt"] = "admin"
            message["_open_platform_admin_promoted_at_receipt"] = True

    @staticmethod
    def _resolve_open_platform_group_key(
        message: dict[str, Any],
    ) -> tuple[str, str]:
        """告警用的群标识，外加「它是从哪个字段取到的」。

        不能只认 ``message["group_id"]``：``_convert_event`` 只读
        ``data.get("group_id")``，而开放平台若按 v2 语义下发 ``group_openid``，
        这个键恒为空串——于是这个告警会在**最可能出问题的那种部署上整个哑
        掉**，正好是设计文档 §2.15.4.4(a) 点名「比 R11 更早爆」的那一种。
        一个只负责「让问题可见」的东西，不能在兄弟缺陷兑现时自己先瞎。

        所以回落到原始 payload 里任何一个带 group 的标识字段（按名字找，不
        枚举，理由同取证插桩）。**刻意只读、绝不回填** ``message["group_id"]``：
        回填会改变该通道 ``speaker_id`` / subject 的字节，那是 §2.15.4.4(a)
        自己的事，取证数据回来之前一行都不该动。

        Returns:
            ``(群标识, 它来自 raw 的哪个键)``。第二项为空串表示走的是正常的
            ``group_id``；非空本身就是 §2.15.4.4(a) 已兑现的证据。
        """
        group_id = str(message.get("group_id") or "").strip()
        if group_id:
            return group_id, ""
        raw = message.get("raw")
        if not isinstance(raw, dict):
            return "", ""
        for key in sorted(str(k) for k in raw):
            if "group" not in key.lower():
                continue
            value = str(raw.get(key) or "").strip()
            if value:
                return value, key
        return "", ""

    def _note_open_platform_pending_claim(
        self,
        message: dict[str, Any],
        permission_level: Any,
    ) -> None:
        """记录「这个群里出现了一个不在名册上的 ID」，供人工认领。

        设计出处：``docs/design/speaker-trust-entity-semantics.md``
        §2.15.4.3 第 1 级（操作者人工断言）。开放平台上同一个人在每个群是一
        个不同的 ``member_openid``，主人要在每个群单独被认出来，就得把那个群
        里的 ID 加进名册——而那串 openid 在界面上根本无处可看，只能去翻日
        志。这个池就是把它摆到界面上。

        **纯观测，不改任何权限判定。**它只回答「有哪些 ID 还没被认领」，不
        回答「这些 ID 是不是同一个人」——后者是被硬约束否决的自动合并，只能
        由人在 UI 上逐个断言。

        对上了名册的 ID 立刻移出：认领完成就该从待办清单里消失。
        """
        try:
            channel = str(message.get("channel") or "").strip().lower()
            if channel != _OPEN_PLATFORM_CHANNEL:
                return
            group_id, _ = self._resolve_open_platform_group_key(message)
            sender_id = str(message.get("user_id") or "").strip()
            if not group_id or not sender_id:
                return
            pool = getattr(self, "_open_platform_pending_claims", None)
            if pool is None:
                pool = {}
                self._open_platform_pending_claims = pool
            if str(permission_level or "none") != "none":
                bucket = pool.get(group_id)
                if bucket is not None:
                    bucket.pop(sender_id, None)
                    if not bucket:
                        pool.pop(group_id, None)
                return
            bucket = pool.get(group_id)
            if bucket is None:
                if len(pool) >= self.OPEN_PLATFORM_CLAIM_MAX_GROUPS:
                    self._evict_stalest_claim_group(pool)
                bucket = {}
                pool[group_id] = bucket
            now = int(__import__("time").time())
            entry = bucket.get(sender_id)
            if entry is None:
                if len(bucket) >= self.OPEN_PLATFORM_CLAIM_MAX_PER_GROUP:
                    stalest = min(
                        bucket,
                        key=lambda key: bucket[key].get("last_seen", 0),
                    )
                    bucket.pop(stalest, None)
                entry = {"first_seen": now, "count": 0, "nickname": ""}
                bucket[sender_id] = entry
            entry["last_seen"] = now
            entry["count"] = int(entry.get("count", 0)) + 1
            # 昵称是别人随手打的，且这里是**唯一**不经过 PermissionManager
            # 那道清洗就直达界面的入口（名册那条路会拒掉带控制字符的昵称）。
            # 剥掉控制字符再截断：它们在表格里看不见，在内联 handler 里却能
            # 把那段 JS 截断。
            nickname = "".join(
                char for char in str(message.get("user_nickname") or "")
                if char.isprintable()
            ).strip()
            if nickname:
                entry["nickname"] = nickname[:64]
        except Exception:
            # 观测绝不允许把消息管线带下去。
            pass

    @staticmethod
    def _evict_stalest_claim_group(pool: dict[str, dict[str, dict]]) -> None:
        """淘汰最久没有新消息的那个群。"""
        def _group_last_seen(group_id: str) -> int:
            bucket = pool.get(group_id) or {}
            return max(
                (int((row or {}).get("last_seen", 0)) for row in bucket.values()),
                default=0,
            )

        if not pool:
            return
        pool.pop(min(pool, key=_group_last_seen), None)

    def list_open_platform_pending_claims(
        self, *, is_claimed: Any = None,
    ) -> list[dict[str, Any]]:
        """待认领清单，最近出现的排前面。

        ``is_claimed`` 是一个 ``(actor) -> bool`` 的谓词，为真的当场出清单
        并从池里删掉。没有它的话，一个刚被加进名册的人要等**再发一次言**
        才会消失（移除只发生在 `_note_open_platform_pending_claim`），而操
        作者认领完最可能的下一步就是刷新页面——看见同一行还在，于是重复
        点一次。
        """
        pool = getattr(self, "_open_platform_pending_claims", None) or {}
        if callable(is_claimed):
            for group_id in list(pool):
                bucket = pool.get(group_id) or {}
                for sender_id in list(bucket):
                    try:
                        claimed = bool(is_claimed(sender_id))
                    except Exception:
                        claimed = False
                    if claimed:
                        bucket.pop(sender_id, None)
                if not bucket:
                    pool.pop(group_id, None)
        rows: list[dict[str, Any]] = []
        for group_id, bucket in pool.items():
            for sender_id, entry in bucket.items():
                rows.append({
                    "group_id": group_id,
                    "user_id": sender_id,
                    "nickname": str(entry.get("nickname") or ""),
                    "first_seen": int(entry.get("first_seen", 0)),
                    "last_seen": int(entry.get("last_seen", 0)),
                    "message_count": int(entry.get("count", 0)),
                })
        rows.sort(key=lambda row: row["last_seen"], reverse=True)
        return rows

    def _note_open_platform_identity_scope(
        self,
        message: dict[str, Any],
        permission_level: Any,
    ) -> None:
        """开放平台身份作用域的**纯观测**告警（R11）。

        设计出处：``docs/design/speaker-trust-entity-semantics.md`` §2.15.4。

        怀疑的是：开放平台在私聊里下发的 author.id 与在群里下发的可能不是同
        一个作用域（``user_openid`` vs ``member_openid``）。若真如此，
        ``_maybe_reserve_open_platform_admin`` 通过**第一条私聊**授权的主人，
        在**所有群**里都匹配不上名册——档位解析成 ``none``、
        ``speaker_is_owner`` 恒假、信赖度的 confirmation/correction 来源直接
        断供。

        这个怀疑**尚未取证**（取证插桩见 ``qq_open_plat.py`` 顶部的 R11 一
        节）。所以这里只报，不修：

        - 不改任何权限判定，不写名册，不碰 message 的任何既有键；
        - 若 id 本来就同作用域（R11 不成立），本方法永远走不到告警那一步，
          是彻底的 no-op。

        **不要**顺手把 ``_maybe_reserve_open_platform_admin`` 里那句全局
        ``if permission_mgr.list_users(): return`` 改成「按当前通道过滤后判
        空」。那不是疏漏，是让通道切换 fail-closed 的门：按通道过滤在刚切到
        open_platform 时恒为空，等于让切换后第一个私聊 bot 的陌生人自动拿到
        admin。
        """
        try:
            channel = str(message.get("channel") or "").strip().lower()
            if channel != _OPEN_PLATFORM_CHANNEL:
                return
            group_id, group_key_source = self._resolve_open_platform_group_key(
                message,
            )
            sender_id = str(message.get("user_id") or "").strip()
            if not group_id or not sender_id:
                return
            permission_mgr = getattr(self.plugin, "permission_mgr", None)
            if permission_mgr is None:
                return
            roster = permission_mgr.list_users() or []
            admins = [
                user for user in roster
                if str((user or {}).get("level") or "") == "admin"
            ]
            # 名册里没有管理员 ⇒ 群里认不出人是理所当然的，不是信号。
            if not admins:
                return

            states = getattr(self, "_open_platform_scope_alarm_state", None)
            if states is None:
                states = {}
                self._open_platform_scope_alarm_state = states
            state = states.get(group_id)
            if state is None:
                if len(states) >= self.OPEN_PLATFORM_SCOPE_ALARM_MAX_GROUPS:
                    return
                state = {"unmatched": set(), "matched": False, "warned": False}
                states[group_id] = state
            # 这个群里但凡有过一个人对上名册，就证明群侧 id 与名册同作用域，
            # 此后永久闭嘴。
            if state["matched"]:
                return
            if str(permission_level or "none") != "none":
                state["matched"] = True
                state["unmatched"].clear()
                return
            if state["warned"]:
                return
            state["unmatched"].add(sender_id)
            if len(state["unmatched"]) < self.OPEN_PLATFORM_SCOPE_ALARM_SPEAKERS:
                return
            state["warned"] = True
            state["unmatched"].clear()
            text = (
                f"[R11] 开放平台身份作用域告警: 群 {group_id} 已出现 "
                f"{self.OPEN_PLATFORM_SCOPE_ALARM_SPEAKERS} 个不同说话人，"
                f"无一匹配已登记用户（名册中有 {len(admins)} 个管理员）。"
                "若主人是靠「第一条私聊自动授权」拿到的管理员，那个 id 可能"
                "只在私聊作用域有效，需要在本群单独把群内 id 加进信任用户。"
                "本条仅为诊断，不改变任何权限判定。"
            )
            if group_key_source:
                # 顺带报的是另一件事，而且比 R11 更早爆：群 id 根本不在
                # _convert_event 读的那个键上（§2.15.4.4(a)）。
                text += (
                    f"（另：本群的群 id 不在 group_id 字段上，实际挂在 "
                    f"{group_key_source}——这会让群消息在别处被当成「无群」，"
                    "需要单独修，见设计文档 §2.15.4.4(a)。）"
                )
            logger = getattr(self.plugin, "logger", None)
            if logger is not None:
                logger.warning(text)
            emit_log = getattr(self.plugin, "_emit_log", None)
            if callable(emit_log):
                emit_log("WARN", text)
        except Exception:
            # 观测绝不允许把消息管线带下去。
            pass

    async def enrich_open_platform_attachments(
        self, message: dict[str, Any], *, label_defs: list, raw_content: str,
    ) -> bool:
        """开放平台入站附件里的**非图片**：接到文件渲染链路上。返回 True = 命中黑名单。

        为什么非做不可：图片那半有去处（``prompting._queue_attachment_images`` 会把 URL
        下载成多模态图喂给模型），文件那半以前直接掉在地上（``_collect_image_attachments``
        只认 ``image``/``image_url``）。表现是"对方发了个文件，她只看到空气" ——
        而且**不报错**，所以只能靠读代码发现。

        路由到**同一个** ``_fetch_file_content``（文本解码 / 二进制标记 / 按扩展名走 VLM），
        与 NapCat 的文件段同口径，不另写一套解析。
        """
        enricher = getattr(self.plugin, "enricher", None)
        if enricher is None or not hasattr(enricher, "_attachment_files"):
            return False
        # 先按**内容**把"其实是图片"的文件附件改成图片附件：这样它会走多模态那条路，
        # 她真的看得见；真文件才落到下面的文本渲染。真机上"用户把图当文件发"就是靠
        # 这一步从"这个文件打不开欸"变成看得见（见 promote_image_attachments 的说明）。
        if hasattr(enricher, "promote_image_attachments"):
            try:
                promoted = await enricher.promote_image_attachments(message)
            except Exception:
                promoted = 0
                logger = getattr(self.plugin, "logger", None)
                if logger is not None:
                    logger.warning("QQ 附件图片识别失败", exc_info=True)
            if promoted:
                emit_log = getattr(self.plugin, "_emit_log", None)
                if callable(emit_log):
                    emit_log("INFO", f"[附件] 按内容识别出 {promoted} 张图片（按文件发来的）")
        attachment_files = enricher._attachment_files(message)
        if not attachment_files:
            return False
        try:
            await enricher._fetch_file_content(message, attachment_files)
        except Exception:
            # 附件解析失败不该让整条消息消失：留痕，然后照原样往下走。
            logger = getattr(self.plugin, "logger", None)
            if logger is not None:
                logger.warning("QQ 附件文件解析失败", exc_info=True)
            return False
        enriched_content = str(message.get("content") or "").strip()
        if enriched_content == raw_content:
            # 渲染没改动内容（比如 URL 取不到）——不谎报"已解析"。
            return False
        emit_log = getattr(self.plugin, "_emit_log", None)
        if callable(emit_log):
            emit_log("INFO", f"[附件] 解析 {len(attachment_files)} 个文件附件")
        return bool(
            enriched_content and QQFeedbackClassifier.is_blacklisted(enriched_content, label_defs)
        )

    def _resolve_poke_nickname(self, user_id: str, raw_msg: dict[str, Any]) -> str:
        """从戳一戳事件中获取用户昵称"""
        uid = str(user_id or "").strip()
        if not uid:
            return "未知用户"
        # 优先用 sender 中的 nickname/card
        sender = raw_msg.get("sender") or {}
        if isinstance(sender, dict):
            nick = sender.get("card") or sender.get("nickname") or ""
            if str(nick).strip():
                return str(nick).strip()
        # 其次查权限管理器中的昵称
        if self.plugin.permission_mgr:
            nick = self.plugin.permission_mgr.get_nickname(uid)
            if nick:
                return nick
        return f"QQ用户{uid}"

    def _is_gate_bypassed_synthetic(self, source: str) -> bool:
        """这一轮的合成来源是不是"脚本已判完、不必再过门控"的那几种。"""
        return str(source or "") in self.GATE_BYPASS_SYNTHETIC_SOURCES

    def _is_forced_synthetic(self, source: str) -> bool:
        """这一轮的合成来源是不是"必定回复"的那几种（只有入群欢迎）。"""
        return str(source or "") in self.FORCED_SYNTHETIC_SOURCES

    def _ban_reaction_allowed(
        self, group_id: str, user_id: str, sub_type: str, now: float,
    ) -> bool:
        """禁言反应的节流：每群冷却 + 同一人同一事件只反应一次。

        使用者口径（2026-09-29）：两者都要。批量禁言（管理在清刷屏的人）时，
        没有这道闸她会在几分钟里连说好几句。
        """
        cooldown = self.BAN_REACTION_COOLDOWN_SECONDS
        key = f"{group_id}:{user_id}:{sub_type}"
        last_same = self._ban_reactions.get(key, 0.0)
        last_group = self._ban_group_last.get(group_id, 0.0)
        if now - last_same < cooldown:
            self.plugin._emit_log(
                "DEBUG",
                f"[Ban] 群{group_id} {user_id} {sub_type} 同一事件刚反应过"
                f"（还剩 {cooldown - (now - last_same):.0f}s）→ 不反应",
            )
            return False
        if now - last_group < cooldown:
            self.plugin._emit_log(
                "DEBUG",
                f"[Ban] 群{group_id} 距上次禁言反应不到 {cooldown:.0f}s"
                f"（还剩 {cooldown - (now - last_group):.0f}s）→ 不反应",
            )
            return False
        self._ban_reactions[key] = now
        self._ban_group_last[group_id] = now
        return True

    def _maybe_react_to_ban(self, message: dict[str, Any]) -> bool:
        """把「某人被禁言/解禁」改写成一条合成系统消息。返回 True = 该走 pipeline。

        它**不是**任何人的发言：`content` 是我们写的事件描述，`user_id` 是事件主角
        （沿用入群通知那条路的写法），`_synthetic_source` 标记它，供提示词与记忆排除。

        三道闸，缺一不可：

        1. 群必须是 **trusted**（她只在这种群里开口；normal 群是"按概率转达给主人"，
           在那里说话等于把别人的禁言转达成她的话）；
        2. 被禁言的人必须**正在和她对话**（一来一回，见 `dialogue_partner`）——
           使用者选的就是这个口径：「正在和她对话的人」；
        3. 节流（`_ban_reaction_allowed`）：每群 600 秒 + 同一事件一次。
        """
        group_id = str(message.get("group_id") or "").strip()
        user_id = str(message.get("user_id") or "").strip()
        sub_type = str(message.get("sub_type") or "ban").strip() or "ban"
        if not group_id or not user_id:
            return False

        level = ""
        permission_mgr = getattr(self.plugin, "group_permission_mgr", None)
        if permission_mgr:
            try:
                level = str(permission_mgr.get_group_level(group_id) or "")
            except Exception:
                level = ""
        if level != "trusted":
            self.plugin._emit_log(
                "DEBUG", f"[Ban] 群{group_id} 不是 trusted（{level or '未登记'}）→ 不反应",
            )
            return False

        gate = getattr(self.plugin, "attention_gate_service", None)
        if gate is None or not hasattr(gate, "is_in_dialogue_with"):
            return False
        if not gate.is_in_dialogue_with(group_id, user_id):
            self.plugin._emit_log(
                "DEBUG",
                f"[Ban] 群{group_id} {user_id} 被{('禁言' if sub_type == 'ban' else '解禁')}"
                f"，但他不是正在和她对话的人 → 不反应",
            )
            return False

        now = float(__import__("time").time())
        if not self._ban_reaction_allowed(group_id, user_id, sub_type, now):
            return False

        name = self._resolve_poke_nickname(user_id, message)
        duration = int(message.get("duration") or 0)
        if sub_type == "lift_ban":
            what = "的禁言被解除了"
        elif duration > 0:
            minutes, seconds = divmod(duration, 60)
            span = f"{minutes} 分 {seconds} 秒" if minutes else f"{seconds} 秒"
            what = f"被管理员禁言了 {span}"
        else:
            what = "被管理员禁言了"

        message["message_type"] = "group"
        message["group_id"] = group_id
        message["user_id"] = user_id
        message["is_at_bot"] = False
        message["content"] = (
            f"[系统] {name}{what}。他刚才还在跟你说话。"
            f"想接一句就自然地说一句（不必 @ 他，也别评论管理员怎么管群）；不想说就不说。"
        )
        message["raw_message"] = message["content"]
        message["message_id"] = f"ban_{group_id}_{user_id}_{int(now)}"
        message["_synthetic_source"] = "group_ban_notice"
        self.plugin._emit_log(
            "INFO", f"[Ban] 群{group_id} {name}{what} → 交给模型决定说不说",
        )
        return True

    def _poke_follow_allowed(self, group_id: str, now: float) -> bool:
        """「跟戳别人之间的戳」的群级限速（见 `POKE_FOLLOW_MIN_INTERVAL_SECONDS`）。"""
        last = self._last_poke_follow.get(group_id, 0.0)
        if now - last < self.POKE_FOLLOW_MIN_INTERVAL_SECONDS:
            return False
        self._last_poke_follow[group_id] = now
        return True

    async def _poke_back(self, group_id: str, user_id: str, now: float) -> bool:
        """戳回去（或跟着戳）。返回是否真的戳了。

        每人 5 分钟最多 `POKE_BACK_MAX_PER_POKER` 次：跟戳不等于陪到底，
        没有这道闸就是无限互戳。失败只记日志 —— 戳一戳是轻量互动，
        戳不出去不该把整条派发炸掉。
        """
        uid = str(user_id or "").strip()
        if not uid:
            return False
        timestamps = self.plugin._poke_timestamps.setdefault(uid, [])
        timestamps[:] = [t for t in timestamps if t > now - self.POKE_BACK_WINDOW_SECONDS]
        if len(timestamps) >= self.POKE_BACK_MAX_PER_POKER:
            return False
        timestamps.append(now)
        try:
            await self.plugin.qq_client.send_group_poke(group_id, uid)
        except Exception as e:
            self.plugin._emit_log("INFO", f"回戳失败: {e}")
            return False
        return True

    def _has_waking_keyword(self, message_text: str) -> bool:
        """检查消息是否包含唤醒关键词。"""
        text = str(message_text or "").strip()
        if not text:
            return False
        for label in (self.plugin._qq_settings or {}).get("backlog_labels") or []:
            if not isinstance(label, dict):
                continue
            priority = int(label.get("priority") or 0)
            if priority <= 0:
                continue
            for kw in label.get("keywords") or []:
                word = str(kw).strip()
                if word and word in text:
                    return True
        return False

    async def process_messages(self):
        while self.plugin._running:
            try:
                message = await self.plugin.qq_client.receive_message()
                if message:
                    if isinstance(message, dict):
                        # 接收时刻的群记忆政策快照：handler 在全局并发闸/
                        # 会话锁上可能排队数秒，处理侧任何晚读都会把 OFF
                        # 时代收到的消息标成已授权。真正的接收边界在这里
                        # （task 创建之前），随消息本体传递。
                        settings_now = getattr(self.plugin, "_qq_settings", {}) or {}
                        message["_group_memory_at_receipt"] = bool(
                            settings_now.get("group_memory_enabled", False)
                        )
                        # 成员记忆是群记忆的子开关：两个都开才算收到时有
                        # 授权（后端已钳制，这里是收口处的对偶判据）。
                        message["_member_memory_at_receipt"] = bool(
                            settings_now.get("group_member_memory_enabled", False)
                        ) and bool(settings_now.get("group_memory_enabled", False))
                        # 私聊 participant 记忆政策的接收边界章（对偶上面
                        # 两枚；群消息不消费它）。
                        message["_participant_memory_at_receipt"] = bool(
                            settings_now.get(
                                "private_participant_memory_enabled", False,
                            )
                        )
                        # 通道观测的接收边界快照（对偶上面几枚）：会话缓冲
                        # 可能跨越一次模式切换，flush 时读实时配置会把旧通道
                        # 的消息记成新通道。纯诊断字段，不参与任何判定。
                        message["_speaker_channel_at_receipt"] = str(
                            message.get("channel") or ""
                        ).strip().lower() or None
                        if message.get("message_type") == "group":
                            sender_at_receipt = str(
                                message.get("user_id") or ""
                            ).strip()
                            permission_at_receipt = None
                            permission_mgr = getattr(
                                self.plugin, "permission_mgr", None,
                            )
                            if permission_mgr is not None:
                                permission_at_receipt = (
                                    permission_mgr.get_permission_level(
                                        sender_at_receipt
                                    )
                                )
                            message[
                                "_group_speaker_permission_level_at_receipt"
                            ] = permission_at_receipt
                            # 纯观测，挂在刚算出的那个档位后面：告警读的必须
                            # 是管线真正用的那个值，不能自己再查一次。
                            self._note_open_platform_identity_scope(
                                message, permission_at_receipt,
                            )
                            self._note_open_platform_pending_claim(
                                message, permission_at_receipt,
                            )
                        if message.get("message_type") == "private":
                            sender_at_receipt = str(
                                message.get("user_id") or ""
                            ).strip()
                            permission_at_receipt = None
                            permission_mgr = getattr(
                                self.plugin, "permission_mgr", None,
                            )
                            if permission_mgr is not None:
                                permission_at_receipt = (
                                    permission_mgr.get_permission_level(
                                        sender_at_receipt
                                    )
                                )
                            message["_private_permission_level_at_receipt"] = (
                                permission_at_receipt
                            )
                    task = __import__("asyncio").create_task(self.plugin._run_message_handler(message))
                    self.plugin.handler_runtime_service.track_handler_task(task)
            except __import__("asyncio").CancelledError:
                break
            except Exception as e:
                self.plugin.logger.error(f"Error processing message: {e}")
                await __import__("asyncio").sleep(1)

    def _is_blacklisted_user(self, qq_number: str) -> bool:
        """这个 QQ / openid 是否被拉黑（用户级别里的 ``blacklist``）。

        级别与 admin / trusted / normal 存在同一份名单里（``PermissionManager``），
        所以控制台那张「信任用户」表就是黑名单的管理入口。
        """
        manager = getattr(self.plugin, "permission_mgr", None)
        if manager is None:
            return False
        try:
            return str(manager.get_permission_level(qq_number) or "").strip().lower() == "blacklist"
        except Exception:
            return False

    async def handle_message(self, message: dict[str, Any]):
        # 用户黑名单：这个人发的消息 / 戳一戳 / 入群通知一律不处理，**绝不进管线**。
        # 必须放在最前面，三个理由：
        #   ① 下面戳一戳分支会在更早的位置直接 send_group_poke 并 return ——
        #      放后面就拦不住"回戳"；
        #   ② 要在 backlog 记录之前，否则黑名单用户的话仍会进 backlog，被
        #      「回溯补回」在焦点切换时喂给猫娘；
        #   ③ 要在 enrichment（VLM/STT/引用链）之前，既省一遍开销，也不让内容
        #      有机会被别处引用。
        blacklist_sender = str(message.get("user_id") or "").strip()
        if blacklist_sender and self._is_blacklisted_user(blacklist_sender):
            self.plugin._emit_log(
                "INFO",
                f"用户黑名单过滤: user={blacklist_sender} type={message.get('message_type')}",
            )
            return
        # 戳一戳通知：**一律只跟戳，不回话**（戳她 → 回戳她的人；戳别人 → 跟着戳被戳的人）。
        #
        # 使用者 2026-09-27：「戳戳风暴就不需要回复了，只需要跟戳」，以及追问后选的
        # 「poke 通知一律不进对话，只跟戳」。
        # 改之前这里是反的：「人少 → 回戳不说话；人多（风暴）→ 不回戳、丢给 LLM 让她
        # 在群里说点什么」，戳别人则整个交给模型。真机 15:20 那次风暴就因此多花了一轮
        # 生成 —— 她要的语义是：戳一戳本来就是轻量互动，不必说话、也不必为它开一轮对话
        # （顺带不再把它当成"有人在点名她"，不再抢焦点/上锁）。
        #
        # 三道闸：
        #   · 每人 5 分钟内最多回戳 2 次（跟戳不等于陪到底，否则就是无限互戳）；
        #   · 跟戳别人之间的戳每群 15 秒最多一次（免得同一秒戳一串人）；
        #   · 黑名单用户的戳在更早的位置已被拦掉（见 handle_message 顶部的用户黑名单）。
        if message.get("message_type") == "notice" and message.get("notice_type") == "poke":
            group_id = str(message.get("group_id") or "").strip()
            poker_id = str(message.get("user_id") or "").strip()
            target_id = str(message.get("target_id") or "").strip()
            self_id = str(getattr(self.plugin.qq_client, "self_id", "") or "")
            if not group_id or not poker_id:
                return
            is_poke_me = bool(self_id and target_id == self_id)
            now = __import__("time").time()
            # 她自己戳别人 → NapCat 会把这次动作**回显**成一条通知（user = 她自己）。
            # 那不是群里的互动，是她的动作回声：真机 2026-09-27 全日志 69 条戳通知里
            # 有 10 条是这种回显，每一条都被当成"某人戳了某人"喂进管线 —— 白开一轮
            # 生成，模型还可能再戳一次，于是**自己喂自己**（15:20:40 回显 → 生成 →
            # 15:20:52 又戳一次）。放在最前面拦掉，连"戳别人"那条路都不进。
            # 拿不到 self_id 时不猜（宁可照旧处理，也不要误吞真人的戳）。
            if self_id and poker_id == self_id:
                self.plugin._emit_log(
                    "DEBUG", f"[Poke] 忽略自己的戳回显（target={target_id}）",
                )
                return
            poker_name = self._resolve_poke_nickname(poker_id, message)
            target_name = self._resolve_poke_nickname(target_id, message) if target_id and not is_poke_me else ""

            if is_poke_me:
                # 统计短时间窗内戳她的**不同人**（只用于留痕：风暴是"好几个人一起戳"）。
                storm = self.plugin._poke_storm.setdefault(group_id, [])
                storm[:] = [(t, p) for t, p in storm if now - t < self.POKE_STORM_WINDOW_SECONDS]
                if not any(p == poker_id for p in (p for _, p in storm)):
                    storm.append((now, poker_id))
                storm_count = len(storm)
                if storm_count >= self.POKE_STORM_MIN_POKERS:
                    self.plugin._emit_log(
                        "INFO",
                        f"[Poke] 群{group_id} 戳一戳风暴（{storm_count} 人，最近的是 {poker_name}）"
                        f"→ 只跟戳，不回复",
                    )
                await self._poke_back(group_id, poker_id, now)
                return  # 一律不回话：戳一戳不进管线，也不抢焦点
            # 戳别人 → 她也**跟着戳一下**（跟戳被戳的那个人），但不说话、不进管线。
            #
            # 使用者 2026-09-27 选的正是这一条：「poke 通知一律不进对话，只跟戳」
            # （选项里的"完全不理"没选）。所以模型这里彻底不用出场了 —— 戳一戳是
            # 轻量互动，开一轮生成去决定"要不要戳"性价比极低。
            #
            # 跟戳限速：每群 `POKE_FOLLOW_MIN_INTERVAL_SECONDS` 一次。没有这条的话，
            # 5 个人连戳时她会在同一秒里把所有被戳的人都戳一遍 —— 像机关枪，不像人。
            # **戳她本人的回戳不受这条限制**：那是对她的动作，该立刻回应。
            if self._poke_follow_allowed(group_id, now):
                if await self._poke_back(group_id, target_id or poker_id, now):
                    self.plugin._emit_log(
                        "DEBUG",
                        f"[Poke] 跟戳：{poker_name} 戳了 {target_name or target_id}",
                    )
            return
        # 新人入群通知 → 注入欢迎提示
        if message.get("notice_type") == "group_increase":
            group_id = str(message.get("group_id") or "").strip()
            user_id = str(message.get("user_id") or "").strip()
            if group_id and user_id:
                self.plugin._emit_log("INFO", f"新人入群: group={group_id} user={user_id}")
                message["message_type"] = "group"
                message["group_id"] = group_id
                message["user_id"] = user_id
                message["is_at_bot"] = False
                message["content"] = f"[系统] 新成员 {user_id} 加入了群聊，你可以欢迎一下。注意：要像真人一样自然地欢迎，不要用模板化的欢迎语。"
                message["raw_message"] = message["content"]
                message["message_id"] = f"welcome_{group_id}_{user_id}_{int(__import__('time').time())}"
                # 合成控制指令，不是入群成员的发言：标记 source 让成员
                # bucket 排除、prompt 行进 digest 排除名单。
                message["_synthetic_source"] = "group_join_notice"
            # 不 return，走正常 pipeline
        # 群里有人被禁言 / 被解禁 → **只对"正在和她对话的人"**反应，方式是合成一条
        # 系统消息交给正常 pipeline（与入群欢迎同一条路，见下面 `_maybe_react_to_ban`）。
        #
        # 连接层只把**第三方**被禁言/解禁送上来：自己或全员被禁言时不入队 ——
        # 那种情况她根本发不出去（`is_group_muted` 会让整条消息处理直接跳过）。
        if (
            message.get("message_type") == "notice"
            and message.get("notice_type") == "group_ban"
        ):
            if not self._maybe_react_to_ban(message):
                return
            # 反应得成就继续往下走：它已经变成一条合成的群消息。
        # 黑名单优先：命中负优先级标签 → 不记录、不处理
        label_defs = list((self.plugin._qq_settings or {}).get("backlog_labels") or [])
        raw_content = str(message.get("content") or "").strip()
        if raw_content and QQFeedbackClassifier.is_blacklisted(raw_content, label_defs):
            self.plugin._emit_log("INFO", f"黑名单过滤: text={raw_content[:40]}")
            return
        # Open-platform bootstrap is serialized here, after all private-message
        # eligibility filters but before backlog/context work.  A filtered
        # first sender must never acquire owner-memory privileges.
        await self._maybe_reserve_open_platform_admin(message)

        # 后台拉取引用/转发/语音/文件内容 + VLM 描述（在独立 handler task 中
        # await，避免 WS handler 死锁）。已移入连接层的 ``enrich_message``。
        if self.plugin.qq_client and self.plugin.qq_client.needs_attention and self.plugin.enricher:
            enricher = self.plugin.enricher
            # 连接器不再打 _pending_* 标记（只管收发/归一）；由插件 enricher 识别需增强的段
            _rids = enricher._expand_reply_segments(message)
            if _rids:
                message["_pending_reply_ids"] = _rids
            _fwd = enricher._expand_forward_segments(message)
            if _fwd:
                message["_pending_forward_ids"] = _fwd
            _rec = enricher._transcribe_record_segments(message)
            if _rec:
                message["_pending_record_files"] = _rec
            _files = enricher._collect_file_segments(message)
            if _files:
                message["_pending_file_ids"] = _files
            message = await enricher.enrich_message(message)
            # 语音/引用/转发可能丰富了消息内容 → 复检黑名单
            enriched_content = str(message.get("content") or "").strip()
            if enriched_content != raw_content and QQFeedbackClassifier.is_blacklisted(enriched_content, label_defs):
                self.plugin._emit_log("INFO", f"黑名单过滤(转录后): text={enriched_content[:40]}")
                return
        elif self.plugin.qq_client and self.plugin.enricher:
            # 开放平台：只收 @ 消息，附件里的非图片在别处没有消费方 —— 见
            # `enrich_open_platform_attachments` 的说明。命中黑名单同样不再继续。
            if await self.enrich_open_platform_attachments(
                message, label_defs=label_defs, raw_content=raw_content,
            ):
                self.plugin._emit_log(
                    "INFO",
                    f"黑名单过滤(附件解析后): text={str(message.get('content') or '')[:40]}",
                )
                return
        await self.plugin.backlog_service.record_message(message)
        # 注意力的逐条更新统一由 attention_gate_service.evaluate() 负责（消息进管线时
        # 调用），这里不再自己更新一次 —— 历史上那条分支只在非 neko_dynamic 模式下跑，
        # 模式合并后它已经不可达。
        self.plugin._emit_log("INFO", f"收到消息: type={message.get('message_type')} from={message.get('user_id')} text={str(message.get('content',''))[:40]}")
        getattr(self.plugin, "_maybe_push_status_event", lambda: None)()  # 消息活动 → SSE 通知前端刷新状态
        message_type = message.get("message_type")
        sender_id = str(message.get("user_id") or "").strip()
        message_text = self.plugin._sanitize_message_text(
            message.get("content", ""),
            is_reply_to_bot=bool(message.get("is_reply_to_bot")),
        )
        attachments = list(message.get("attachments") or [])
        user_nickname = message.get("user_nickname")
        # 引用上下文：仅注入 LLM prompt，不混入 message_text 以防污染会话历史
        reply_context = str(message.get("_reply_context", "") or "").strip()
        if message_type == "private":
            session_key = self.plugin._build_session_key(sender_id=sender_id, is_group=False)
            if session_key in self.plugin._user_sessions:
                self.plugin._user_sessions[session_key]["last_activity_at"] = __import__("time").time()
            fwd_count = int(message.get("_forward_sub_count", 0) or 0) if isinstance(message, dict) else 0
            current_message_id = str(message.get("message_id") or message.get("msg_id") or "").strip()
            await self.handle_private_message(
                sender_id, message_text, attachments=attachments,
                user_nickname=user_nickname, forward_sub_count=fwd_count,
                current_message_id=current_message_id,
                reply_context=reply_context,
                participant_memory_at_receipt=(
                    message.get("_participant_memory_at_receipt")
                    if isinstance(message, dict) else None
                ),
                private_permission_level_at_receipt=(
                    message.get("_private_permission_level_at_receipt")
                    if isinstance(message, dict) else None
                ),
                open_platform_admin_promoted_at_receipt=bool(
                    message.get("_open_platform_admin_promoted_at_receipt")
                    if isinstance(message, dict) else False
                ),
            )
        elif message_type == "group":
            group_id = str(message.get("group_id") or "").strip()
            is_at_bot = message.get("is_at_bot", False)
            is_reply_to_bot = message.get("is_reply_to_bot", False)
            current_message_id = str(message.get("message_id") or message.get("msg_id") or "").strip()
            quoted_message_id = str(message.get("quoted_message_id") or "").strip()
            # 被引用的那条是**谁**发的：连接器解析回复段时就取出来了
            # （`_extract_interaction_context` 的 `quoted_sender_id`），此前从没被读过。
            quoted_sender_id = str(message.get("quoted_sender_id") or "").strip()
            # 原始段（数组或 CQ 串）：判"首段 @ 的是谁"要用段序，而连接器给的是布尔量。
            # 取段的办法复用 QQMessageEnricher._message_segments（"段在哪个键下"的唯一真相）。
            segments = addressing.segments_of(message, getattr(self.plugin, "enricher", None))
            mentioned_user_ids = [
                str(user_id or "").strip()
                for user_id in list(message.get("mentioned_user_ids") or [])
                if str(user_id or "").strip()
            ]
            mentions_other_user = bool(message.get("mentions_other_user", False))
            mentions_all = bool(message.get("mentions_all", False))
            message_timestamp = int(message.get("timestamp") or 0)
            session_key = self.plugin._build_session_key(sender_id=sender_id, is_group=True, group_id=group_id)
            if session_key in self.plugin._user_sessions:
                self.plugin._user_sessions[session_key]["last_activity_at"] = __import__("time").time()
            fwd_count = int(message.get("_forward_sub_count", 0) or 0) if isinstance(message, dict) else 0
            # ── 禁言检查：bot 在该群被禁言 → 只记录不入 pipeline ──
            if self.plugin.qq_client and self.plugin.qq_client.is_group_muted(group_id):
                self.plugin._emit_log("INFO", f"[Mute] 群{group_id} 禁言中，跳过消息处理")
                return
            await self.handle_group_message(
                group_id,
                sender_id,
                message_text,
                is_at_bot,
                group_memory_at_receipt=(
                    message.get("_group_memory_at_receipt")
                    if isinstance(message, dict) else None
                ),
                member_memory_at_receipt=(
                    message.get("_member_memory_at_receipt")
                    if isinstance(message, dict) else None
                ),
                group_speaker_permission_level_at_receipt=(
                    message.get(
                        "_group_speaker_permission_level_at_receipt"
                    )
                    if isinstance(message, dict) else None
                ),
                speaker_channel_at_receipt=(
                    message.get("_speaker_channel_at_receipt")
                    if isinstance(message, dict) else None
                ),
                synthetic_source=(
                    str(message.get("_synthetic_source") or "")
                    if isinstance(message, dict) else ""
                ),
                attachments=attachments,
                user_nickname=user_nickname,
                current_message_id=current_message_id,
                quoted_message_id=quoted_message_id,
                quoted_sender_id=quoted_sender_id,
                segments=segments,
                mentioned_user_ids=mentioned_user_ids,
                mentions_other_user=mentions_other_user,
                mentions_all=mentions_all,
                message_timestamp=message_timestamp,
                forward_sub_count=fwd_count,
                reply_context=reply_context,
                is_reply_to_bot=is_reply_to_bot,
            )
            await self.plugin._maybe_notify_backlog_summary(group_id=group_id)

    async def handle_private_message(
        self, sender_id: str, message_text: str,
        attachments: Optional[list[dict[str, Any]]] = None,
        user_nickname: Optional[str] = None, forward_sub_count: int = 0,
        current_message_id: str = "",
        reply_context: str = "",
        participant_memory_at_receipt: bool | None = None,
        private_permission_level_at_receipt: str | None = None,
        open_platform_admin_promoted_at_receipt: bool = False,
    ):
        # 开放平台：第一个私聊用户自动成为管理员，之后可在前端配置
        if open_platform_admin_promoted_at_receipt:
            self.plugin._emit_log(
                "INFO", f"开放平台自动设置管理员: {sender_id}",
            )
            try:
                await self.plugin.settings_service.persist_business_config()
            except Exception:
                # The in-memory reservation already protects this process;
                # startup persistence remains best-effort, matching the
                # pre-existing fallback path below.
                pass
        elif self.plugin.qq_client and not self.plugin.qq_client.needs_attention:
            if self.plugin.permission_mgr and not self.plugin.permission_mgr.list_users():
                self.plugin.permission_mgr.add_user(sender_id, "admin", user_nickname or "管理员")
                self.plugin._refresh_admin_qq()
                self.plugin._emit_log("INFO", f"开放平台自动设置管理员: {sender_id}")
                try:
                    await self.plugin.settings_service.persist_business_config()
                except Exception:
                    pass
        # LLM 生成前预缓冲：如果已有等待中的回复，跳过 pipeline。
        # 私聊缓冲可单独关闭 —— 关掉时这条直接走 pipeline，不排进任何等待队列。
        _buffer = getattr(self.plugin, "reply_buffer_service", None)
        if _buffer and _buffer.is_enabled(
            getattr(self.plugin, "_qq_settings", {}) or {}, is_group=False
        ):
            session_key = self.plugin._build_session_key(sender_id=sender_id, is_group=False)
            if self.plugin.reply_buffer_service.pre_buffer(
                session_key,
                message_text,
                sender_id,
                False,
                "",
                participant_memory_at_receipt=participant_memory_at_receipt,
                private_permission_level_at_receipt=(
                    private_permission_level_at_receipt
                ),
            ):
                return
        self.plugin._emit_log("INFO", f"私聊 pipeline 开始: from={sender_id} text={message_text[:40]}")
        request = QQReplyRequest(
            message_text=message_text,
            sender_id=sender_id,
            attachments=attachments,
            is_group=False,
            user_nickname=user_nickname,
            fallback_to_text_on_voice_failure=True,
            source_kind="incoming_private",
            forward_sub_count=forward_sub_count,
            reply_context=reply_context,
            # 接收边界的 participant 记忆政策章（None=旁路调用者，build
            # 内回退实时读）：排队期间 OFF→ON 不得让收到时无授权的私聊
            # 被收集。
            participant_memory_at_receipt=participant_memory_at_receipt,
            private_permission_level_at_receipt=(
                private_permission_level_at_receipt
            ),
        )
        outcome = await self.plugin.reply_pipeline.run(request)
        if outcome.action == "reply" and outcome.reply_text and current_message_id:
            await self.plugin.backlog_store.mark_message_reviewed(current_message_id)
        # 非回复结局补上原因：私聊只有一句 action=ignore 时，看不出是权限（发送者
        # 不在信任列表）还是别的门控，排查代价很高。
        _reason = ""
        _permission = ""
        if outcome.action != "reply":
            _traces = getattr(outcome, "traces", None) or []
            _meta = (getattr(_traces[0], "metadata", None) or {}) if _traces else {}
            _reason = str(_meta.get("attention_gate_reason") or "")
            _permission = str(_meta.get("permission_level") or "")
        self.plugin._emit_log(
            "INFO",
            f"私聊 pipeline 结果: action={outcome.action} text={'有' if outcome.reply_text else '空'}"
            + (f" reason={_reason or '-'} permission={_permission or '-'}" if outcome.action != "reply" else ""),
        )
        self.plugin.runtime_service.record_pipeline_outcome(source=request.source_kind, request=request, outcome=outcome)

    async def handle_group_message(
        self,
        group_id: str,
        sender_id: str,
        message_text: str,
        is_at_bot: bool,
        attachments: Optional[list[dict[str, Any]]] = None,
        user_nickname: Optional[str] = None,
        current_message_id: str = "",
        quoted_message_id: str = "",
        quoted_sender_id: str = "",
        segments: Any = None,
        mentioned_user_ids: Optional[list[str]] = None,
        mentions_other_user: bool = False,
        mentions_all: bool = False,
        message_timestamp: int = 0,
        forward_sub_count: int = 0,
        reply_context: str = "",
        is_reply_to_bot: bool = False,
        group_memory_at_receipt: bool | None = None,
        member_memory_at_receipt: bool | None = None,
        group_speaker_permission_level_at_receipt: str | None = None,
        speaker_channel_at_receipt: str | None = None,
        synthetic_source: str = "",
    ):
        # 群记忆政策快照优先取消息接收边界（process_messages 在 task 创建
        # 前打在消息上——handler 可能在全局并发闸/会话锁上排队数秒）；旁路
        # 调用者无消息级快照时至少在本函数第一个 await 前定格。OFF 时代
        # 收到的发言不得因处理期间切 ON 获得入库授权——对偶 backlog 行的
        # group_memory_enabled_at_receipt。反向（处理期间切 OFF）由 prime
        # 门控与读点复检兜住。
        if group_memory_at_receipt is None:
            group_memory_at_receipt = bool(
                (getattr(self.plugin, "_qq_settings", {}) or {}).get(
                    "group_memory_enabled", False,
                )
            )
        group_memory_at_receipt = bool(group_memory_at_receipt)
        if group_speaker_permission_level_at_receipt is None:
            permission_mgr = getattr(self.plugin, "permission_mgr", None)
            if permission_mgr is not None:
                group_speaker_permission_level_at_receipt = (
                    permission_mgr.get_permission_level(str(sender_id))
                )
        force_reply = False
        # 「谁在跟谁说话」的结论。门控跑得到就有值，跑不到（新人入群这类绕过门控的
        # 合成轮、没有门控服务的轻量宿主）保持 None → 请求里三个字段留空 →
        # 提示词层退回改动前的 6 个标签。
        addressee: Any = None
        # 新人入群 / 禁言反应：脚本已经判完"该不该开这一轮"，这里绕过门控。
        # 其中只有入群欢迎必定回复；禁言反应**不强制** —— 使用者口径：
        # 「派发层通过再让猫娘决定说不说」。
        if self._is_gate_bypassed_synthetic(synthetic_source):
            force_reply = self._is_forced_synthetic(synthetic_source)
        elif hasattr(self.plugin, "attention_gate_service") and self.plugin.attention_gate_service is not None:
            gate_decision = await self.plugin.attention_gate_service.evaluate(
                group_id=group_id,
                sender_id=sender_id,
                is_at_bot=is_at_bot,
                message_text=message_text,
                message_id=current_message_id,
                quoted_message_id=quoted_message_id,
                sender_nickname=user_nickname or "",
                timestamp=message_timestamp,
                is_reply_to_bot=is_reply_to_bot,
                # 「谁在跟谁说话」的原料：连接器早就算好了这三个字段
                # （`_extract_interaction_context`），此前插件侧一个都没读。
                mentioned_user_ids=list(mentioned_user_ids or []),
                mentions_all=mentions_all,
                quoted_sender_id=quoted_sender_id,
                segments=segments,
            )
            # 结论再算一次（纯函数），这次是给**提示词层**用的：此刻
            # `msgs_after_reply` 已被 evaluate 推进过，正是"她刚说完之后的第一条"
            # 那个口径。门控内部那次是为了打分与硬门控 —— 两处同一个 resolver，
            # 不会漂移。
            addressee = self.plugin.attention_gate_service.resolve_addressee_for(
                message_text=message_text,
                is_at_bot=is_at_bot,
                is_reply_to_bot=is_reply_to_bot,
                mentions_all=mentions_all,
                mentioned_user_ids=list(mentioned_user_ids or []),
                quoted_message_id=quoted_message_id,
                quoted_sender_id=quoted_sender_id,
                segments=segments,
                group_id=group_id,
            )
            # 群友复读 → 跟着复读一次（>5 个不同的人 + 这个群是焦点才触发，
            # 见 repeat_echo_service）。刻意放在门控**之后**（那条 evaluate
            # 刚把这条消息计进注意力，焦点判定才反映"现在"）、ignore 判断
            # **之前**（她要不要跟一句复读，与"这条消息本身放不放行给 LLM"
            # 是两件事：回复频率闸拦的是普通回复，不该顺带把复读也拦掉；
            # 而"整批都是复读"时缓冲那边的总结会被抑制，不会变成两条）。
            if self.plugin.repeat_echo_service is not None:
                await self.plugin.repeat_echo_service.maybe_echo(
                    group_id=group_id, sender_id=sender_id, text=message_text,
                )
            if gate_decision.action == "ignore":
                self.plugin.logger.info(
                    f"[AttentionGate] 群 {group_id} 消息被忽略 (sender={sender_id}, reason={gate_decision.reason})"
                )
                return
            force_reply = gate_decision.force_reply
            # 这条消息要交给 LLM 了 → 从 backlog 里标为已看。
            #
            # 判据从 `reason == "focus_group"` 改成"只要没被 ignore"：跨群取舍删掉后
            # 能走到这里的 reason 有 in_conversation / at_bot / keyword:* / reply_to_bot /
            # normal_group_passthrough，它们**都会**进 LLM —— 老写法只标焦点群那一种，
            # 于是被 @ 的和命中关键词的消息一直留在 backlog 里当"没看过"，
            # 下一次回溯补回会把同一条消息再答一遍。
            if current_message_id:
                if hasattr(self.plugin, "backlog_store") and self.plugin.backlog_store:
                    await self.plugin.backlog_store.mark_message_reviewed(current_message_id)

        group_scene_mode = "directed_user" if is_at_bot else "shared_context"
        # 插话抑制已退役：它只在 neko_scene 下可达，而「该不该接」现在由
        # reply_necessity 的必要性判定统一负责（见 docs/SESSION-HANDOFF.md §12）。
        group_memory_enabled = group_memory_at_receipt
        request = QQReplyRequest(
            message_text=message_text,
            sender_id=sender_id,
            attachments=attachments,
            is_group=True,
            group_id=group_id,
            user_nickname=user_nickname,
            is_at_bot=is_at_bot,
            is_reply_to_bot=is_reply_to_bot,
            source_kind=synthetic_source or "incoming_group",
            forward_sub_count=forward_sub_count,
            group_scene_mode=group_scene_mode,
            current_message_id=current_message_id,
            quoted_message_id=quoted_message_id,
            mentioned_user_ids=list(mentioned_user_ids or []),
            mentions_other_user=mentions_other_user,
            mentions_all=mentions_all,
            reply_context=reply_context,
            reply_message_id="",
            at_user_id="",
            fallback_to_text_on_voice_failure=True,
            force_reply=force_reply,
            use_memory_context=group_memory_enabled,
            persist_memory=group_memory_enabled,
            member_memory_at_receipt=member_memory_at_receipt,
            group_speaker_permission_level_at_receipt=(
                group_speaker_permission_level_at_receipt
            ),
            speaker_channel_at_receipt=speaker_channel_at_receipt,
            **addressing.verdict_kwargs(addressee),
        )
        if synthetic_source:
            # 合成控制轮（入群欢迎等）：prompt 行不是任何参与者的发言，
            # pipeline 跑完后记入排除名单（对偶 proactive/rapid-fire；
            # 本 handler 已持会话锁，before 在锁内取）。
            svc = self.plugin.session_memory_service
            hist_before = svc.session_history_len(f"group:{group_id}")
            try:
                outcome = await self.plugin.reply_pipeline.run(request)
            finally:
                svc.record_synthetic_prompt_rows(f"group:{group_id}", hist_before)
        else:
            outcome = await self.plugin.reply_pipeline.run(request)
        # 回复后即时标 reviewed，统一 backlog 管道
        if outcome.action == "reply" and outcome.reply_text and current_message_id:
            if hasattr(self.plugin, "backlog_service") and self.plugin.backlog_service:
                await self.plugin.backlog_store.mark_message_reviewed(current_message_id)

        # 焦点群/近焦点群：输出 LLM 自行判断的结果
        if not is_at_bot:
            if outcome.action == "reply" and outcome.reply_text:
                self.plugin._emit_log("INFO", f"[LLM自判] 决定回复: {outcome.reply_text[:40]}")
            else:
                self.plugin._emit_log("INFO", "[LLM自判] 决定不回复")

        # NapCat: 回复后消耗注意力
        if outcome.action == "reply" and outcome.reply_text:
            if self.plugin.qq_client and self.plugin.qq_client.needs_attention:
                if hasattr(self.plugin, "attention_gate_service") and self.plugin.attention_gate_service:
                    # 带上"这条回复是对谁说的"：禁言反应要判断被禁言的人是不是此刻
                    # 正在跟她对话的那一个（一来一回，见 dialogue_partner）。
                    await self.plugin.attention_gate_service.on_reply_sent(
                        group_id, user_id=sender_id,
                    )

        self.plugin.runtime_service.record_pipeline_outcome(source=request.source_kind, request=request, outcome=outcome)


