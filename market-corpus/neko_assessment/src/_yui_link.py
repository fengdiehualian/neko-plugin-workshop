"""把她请到对话里自己答题，再把她的回答读回来。

为什么不用「把她的记忆注入 prompt 让模型替她答」
------------------------------------------------
那样答话的是**插件**，只是套了她的人设和记忆——不是她。她既不知道自己在答题，
答题时也没有她自己真实的对话上下文（最近聊了什么、什么语气、什么关系）。

正确的做法是**宿主本来就有能力做的**：
  1. 插件把题目 `push_message` 进对话（`ai_behavior="respond"`），
     由宿主用**她本人**的人设、记忆、上下文生成回复——屏幕上就是她在说话；
  2. 再把她那条新回复读回来，解析出答案。

这样「答案是她自己下的」，而且她天然就记得——因为那就是她真实的对话。

读回有两条通道（实测教训）
--------------------------
**主通道：宿主记忆服务的实时对话流**
`http://127.0.0.1:<MEMORY_SERVER_PORT>/new_dialog/<她的名字>` 返回已经渲染好的
对话文本，形如 ``梦瑶月 | …`` / ``YUI | …``（多行回复会续行）。这是宿主自己
维护的服务，**不依赖任何文件路径**，也不必猜目录。

**备用通道：`<她的记忆目录>/time_indexed.db`**
但必须**按 `id` 锚点读，不能按 `timestamp` 读**：该库是「快照式」写入，
一次快照把这一轮新增的多行**写成同一个时间戳**（实测同一批 ai/human 三行
时间戳完全相同）。用 `timestamp > since` 去轮询会读到用户自己那条、读漏她那条。
`id` 是自增的，实测 2314 行里 `id` 与 `timestamp` **零冲突**，才是可靠的游标。

路径不许写死
------------
不同人装在不同地方。按可靠性依次取：
  1. 环境变量 `NEKO_YUI_MEMORY_DIR`（测试/手动指定）
  2. 插件配置 `yui_memory_dir`
  3. **问宿主**：`config_manager.memory_dir` + 角色名（宿主自己就是这么拼的）
  4. 从插件自己的数据目录/安装位置上溯找 `memory/<角色名>`
找不到就如实回报「读不到」，绝不猜一个路径硬用。

依赖：json / os / re / sqlite3 / time / urllib。读失败一律降级，绝不抛给调用方。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# 角色名兜底（正常都能从宿主 config_manager 拿到）
_DEFAULT_CHARACTER = "YUI"
# 主人昵称的兜底。**只是给"已知名字"多一个认法**——对话流的说话人判据是形状，
# 不依赖这个值，所以不同人改了昵称也不会解析错。
_MASTER_FALLBACK = "梦瑶月"

# 本地服务一律绕开系统代理：本机的沙箱/公司代理会拦 127.0.0.1（踩过）
_NO_PROXY = urllib.request.ProxyHandler({})


# ── 角色名 / 根目录：优先问宿主 ────────────────────────────────
def host_config_manager() -> Any:
    """宿主的 config_manager；拿不到返回 None（脱离宿主跑测试时就是这样）。"""
    try:
        from utils.config_manager import get_config_manager

        return get_config_manager()
    except Exception:
        return None


def host_character_name(cm: Any = None) -> str:
    """她叫什么。宿主 `get_character_data()` 的第二个返回值就是。"""
    cm = cm or host_config_manager()
    if cm is None:
        return ""
    try:
        data = cm.get_character_data()
        if isinstance(data, (list, tuple)) and len(data) >= 2:
            name = str(data[1] or "").strip()
            if name:
                return name
    except Exception:
        pass
    return ""


def host_memory_root(cm: Any = None) -> str:
    """宿主放记忆的根目录（`memory_dir` 属性）。"""
    cm = cm or host_config_manager()
    if cm is None:
        return ""
    for attr in ("memory_dir", "memory_root"):
        try:
            value = getattr(cm, attr, None)
        except Exception:
            value = None
        if value:
            return str(value)
    getter = getattr(cm, "get_memory_dir", None)
    if callable(getter):
        try:
            return str(getter() or "")
        except Exception:
            pass
    return ""


def resolve_yui_dir(explicit: str = "", hint_dirs: list[str] | None = None) -> tuple[str, str]:
    """定位她的记忆目录。返回 ``(目录, 来源说明)``；找不到返回 ``("", 原因)``。"""
    env = str(os.environ.get("NEKO_YUI_MEMORY_DIR") or "").strip()
    if env:
        return env, "环境变量 NEKO_YUI_MEMORY_DIR"
    explicit = str(explicit or "").strip()
    if explicit:
        return explicit, "插件配置 yui_memory_dir"

    cm = host_config_manager()
    name = host_character_name(cm) or _DEFAULT_CHARACTER
    root = host_memory_root(cm)
    if root:
        cand = os.path.join(root, name)
        if os.path.isdir(cand):
            return cand, f"宿主 config_manager.memory_dir + 角色名「{name}」"
        # 角色名拿不到时，根目录下唯一带对话库的子目录也认
        try:
            subs = [d for d in os.listdir(root)
                    if os.path.isfile(os.path.join(root, d, "time_indexed.db"))]
        except Exception:
            subs = []
        if len(subs) == 1:
            return os.path.join(root, subs[0]), "宿主 memory_dir 下唯一带对话库的角色目录"
        return "", f"宿主 memory_dir（{root}）里找不到角色「{name}」的目录"

    # 从插件自己的位置/数据目录上溯（插件一般装在 <N.E.K.O>/plugins/<id> 下）
    for hint in list(hint_dirs or []) + [os.path.dirname(os.path.abspath(__file__))]:
        if not hint:
            continue
        base = Path(hint)
        for up in range(0, 5):
            try:
                cand = base.parents[up - 1] if up else base
            except IndexError:
                break
            mem = cand / "memory"
            if not mem.is_dir():
                continue
            direct = mem / name
            if direct.is_dir():
                return str(direct), f"从 {hint} 上溯找到 memory/{name}"
            try:
                subs = [d for d in os.listdir(mem)
                        if (mem / d / "time_indexed.db").is_file()]
            except Exception:
                subs = []
            if len(subs) == 1:
                return str(mem / subs[0]), f"从 {hint} 上溯找到 memory/{subs[0]}"
    return "", "问不到宿主路径，也没能从安装位置上溯找到 memory/<角色名>"


# ── 主通道：宿主记忆服务的实时对话流 ──────────────────────────
_BASE_CACHE: dict[str, str] = {}
# 宿主把记忆服务固定跑在 127.0.0.1 上；端口正常从 config 拿，拿不到就按这个顺序探。
_FALLBACK_PORTS = (48912, 48911, 48913, 48910, 48914, 48915, 48916, 48917)


def candidate_ports() -> list[int]:
    """记忆服务可能监听的端口，按可信度排序（去重）。"""
    out: list[int] = []
    env = str(os.environ.get("NEKO_MEMORY_SERVER_PORT") or "").strip()
    if env.isdigit():
        out.append(int(env))
    try:
        from config import MEMORY_SERVER_PORT  # type: ignore

        out.append(int(MEMORY_SERVER_PORT))
    except Exception:
        pass
    out.extend(_FALLBACK_PORTS)
    seen: set[int] = set()
    uniq: list[int] = []
    for port in out:
        if port not in seen:
            seen.add(port)
            uniq.append(port)
    return uniq


def _open(url: str, timeout: float = 2.0, data: bytes | None = None) -> str:
    """读一个本地 http 服务。失败抛异常，由调用方降级。"""
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    opener = urllib.request.build_opener(_NO_PROXY)
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def memory_server_base(name: str = "", *, refresh: bool = False) -> str:
    """定位宿主的记忆服务。返回 ``http://127.0.0.1:<port>``；找不到返回空串。"""
    if not refresh and _BASE_CACHE.get("base"):
        return _BASE_CACHE["base"]
    who = str(name or _DEFAULT_CHARACTER).strip() or _DEFAULT_CHARACTER
    for port in candidate_ports():
        base = "http://127.0.0.1:%d" % port
        try:
            body = _open("%s/new_dialog/%s?render_language=zh-CN" % (base, who), timeout=1.2)
        except Exception:
            continue
        if body.strip():
            _BASE_CACHE["base"] = base
            return base
    return ""


def _parse_feed(text: str, speakers: tuple[str, ...]) -> list[dict]:
    """把渲染好的对话文本拆成 ``[{"role":…, "text":…}]``。

    格式是 ``<说话人> | <正文>``；正文里的换行会续行，直到下一行出现新的
    ``<说话人> | ``。

    说话人**不能写死**（主人的昵称人人不同），所以判据是形状：
    竖线左边是一小段没有冒号、不以 ``-``/``#`` 开头、长度 ≤ 24 的文字。
    抬头的人设/记忆段全是 ``- 键: 值`` / ``### 标题`` 这种形状，自然被排除；
    已知名字（她 / SYSTEM_MESSAGE）无条件认。
    """
    roles = tuple(r for r in speakers if r)
    out: list[dict] = []
    cur: dict | None = None
    for line in text.split("\n"):
        role = ""
        body = ""
        bar = line.find(" | ")
        if bar > 0:
            head = line[:bar]
            looks_like_speaker = (
                bool(head)
                and len(head) <= 24
                and ":" not in head
                and "：" not in head
                and not head.lstrip().startswith(("-", "#", "*", ">"))
            )
            if head in roles or head == "SYSTEM_MESSAGE" or looks_like_speaker:
                role = head
                body = line[bar + 3:]
        if role:
            if cur:
                out.append(cur)
            cur = {"role": role, "text": body.strip()}
        elif cur is not None:
            cur["text"] = (cur["text"] + "\n" + line.rstrip()) if line.strip() else cur["text"] + "\n"
    if cur:
        out.append(cur)
    for item in out:
        item["text"] = item["text"].strip()
    return [t for t in out if t["text"]]


class YuiFeed:
    """宿主对话流（只读）。**这是读回她实地说了什么的第一个来源。**

    它读的是宿主自己的服务，所以不依赖记忆目录能不能定位到。
    """

    def __init__(self, name: str = "", base: str = ""):
        self.name = str(name or _DEFAULT_CHARACTER).strip() or _DEFAULT_CHARACTER
        self.base = str(base or "")

    def _base(self) -> str:
        return self.base or memory_server_base(self.name)

    @property
    def available(self) -> bool:
        return bool(self._base())

    def turns(self) -> list[dict]:
        base = self._base()
        if not base:
            return []
        try:
            body = _open("%s/new_dialog/%s?render_language=zh-CN" % (base, self.name), timeout=4.0)
        except Exception:
            return []
        return _parse_feed(body, (self.name, _DEFAULT_CHARACTER, _MASTER_FALLBACK))

    def her_turns(self) -> list[str]:
        """她说过的话，按时间顺序。"""
        return [t["text"] for t in self.turns() if t["role"] == self.name]

    def snapshot(self) -> dict:
        """推题**之前**记下游标：**整段对话**的条数 + 最后一条的原文。

        记整段（不只她的话）是因为要拿 ``turns[n-1]`` 做位置校验——窗口
        滚动过就靠不上位置了，那时才退回内容定位。
        """
        turns = self.turns()
        return {
            "count": len(turns),
            "tail": turns[-1]["text"] if turns else "",
            "her_n": sum(1 for t in turns if t["role"] == self.name),
        }

    def new_turns(self, snap: dict) -> list[dict]:
        """相对 ``snap`` 新出现的对话轮。

        先按位置认（要求第 ``count`` 条仍是当时那条，否则说明窗口滚过），
        再退回按内容认。**两条都认不出来就返回空——宁可当没答，也不猜。**
        """
        turns = self.turns()
        snap = snap if isinstance(snap, dict) else {}
        try:
            n = int(snap.get("count") or 0)
        except Exception:
            n = 0
        tail = str(snap.get("tail") or "")
        if n and n <= len(turns) and turns[n - 1]["text"] == tail:
            return turns[n:]
        if tail:
            for i in range(len(turns) - 1, -1, -1):
                if turns[i]["text"] == tail:
                    return turns[i + 1:]
        if not tail and not n:
            return turns
        return []

    def new_her_turns(self, snap: dict) -> list[str]:
        """她相对 ``snap`` 新说的话（按时间顺序）。"""
        return [t["text"] for t in self.new_turns(snap) if t["role"] == self.name]

    def stats(self) -> dict:
        base = self._base()
        info: dict[str, Any] = {
            "available": bool(base),
            "source": ("宿主记忆服务对话流（%s）" % base) if base else "",
            "base": base,
            "character": self.name,
        }
        if not base:
            info["reason"] = "连不上宿主记忆服务（%s）" % (
                "/".join(str(p) for p in candidate_ports()[:4]))
            return info
        try:
            turns = self.turns()
        except Exception as exc:
            info["reason"] = str(exc)
            return info
        hers = [t for t in turns if t["role"] == self.name]
        info["messages"] = len(turns)
        info["her_messages"] = len(hers)
        info["latest"] = (turns[-1]["text"][:60] if turns else "")
        return info


# ── 实时总线：`ctx.bus`（SDK v2 门面）────────────────────────────
# 宿主把插件事件总线包成 SDK v2 的门面。实测对象面（插件日志里那行"ctx.bus 对象面"）：
#
#   ctx.bus = SdkBusContext[conversations, events, frames, lifecycle, memory, messages]
#     .messages      → SdkMessagesBus      → get(**kwargs) → SdkBusList[SdkBusMessageRecord]
#     .events        → SdkEventsBus        → get(**kwargs)
#     .lifecycle     → SdkLifecycleBus     → get(**kwargs)
#     .conversations → SdkConversationsBus → get(**kwargs) / get_by_id(conversation_id, …)
#     .memory        → SdkMemoryBus        → get(bucket_id=None, limit, timeout)
#
# `SdkBusList` 上还有 where / sort / limit / filter / size / watch(debounce_ms)
# （`watch` 是订阅，返回 `SdkBusWatcher`）。记录类是 dataclass + slots，有
# `.dump() -> dict`、`.raw`、`.key`、`.version`、`.from_raw()`。
#
# ⚠ **`get` 是 async 的**（SDK 里有 `SdkBusNamespace._call.<locals>._await_result`），
# 所以要把它跑在一个事件循环里。这些数据在**内存**里，不落盘 → 不滞后。
#
# ⚠ `ctx.bus.memory` 只有 `get`，**没有 `get_sync`**（上一版就是栽在这儿：照抄
# 官方插件 neko_warthunder 的 `ctx.bus.memory.get_sync` 得到一个"不可用"）。
# 所以这里**不写死调用形状**：挨个试几种常见的，把成功的那种记下来供日志/面板自查。
async def _noop() -> None:  # pragma: no cover - 只为让 asyncio 有得跑
    return None


def run_awaitable(value: Any) -> Any:
    """把 awaitable 跑完（独立事件循环）；不是 awaitable 就原样返回。"""
    if not hasattr(value, "__await__"):
        return value
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(value)
    finally:
        try:
            loop.close()
        except Exception:
            pass


def _unwrap(value: Any) -> Any:
    """剥掉 `Ok(value)` 之类的包装。"""
    for attr in ("value", "result", "data"):
        inner = getattr(value, attr, None)
        if inner is not None and not isinstance(value, (list, tuple, dict, str)):
            return inner
    return value


def _as_dicts(value: Any) -> list[dict]:
    """把 `SdkBusList` / 列表 / 包装对象统一成 ``list[dict]``。"""
    value = _unwrap(value)
    dump = getattr(value, "dump", None)
    if callable(dump):
        try:
            items = dump()
        except Exception:
            items = None
        if isinstance(items, list):
            return [dict(x) for x in items if isinstance(x, dict)]
    try:
        items = list(value)
    except Exception:
        return []
    out: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            out.append(item)
            continue
        got = _record_to_dict(item)
        if got:
            out.append(got)
    return out


def _record_to_dict(record: Any) -> dict:
    """一条记录 → dict。字段名不写死：dump / to_dict / raw / __dict__ / slots 挨个试。"""
    for attr in ("dump", "to_dict", "as_dict"):
        fn = getattr(record, attr, None)
        if callable(fn):
            try:
                got = fn()
            except Exception:
                got = None
            if isinstance(got, dict):
                return got
    raw = getattr(record, "raw", None)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            got = json.loads(raw)
            if isinstance(got, dict):
                return got
        except Exception:
            pass
    for attr in ("__dict__", "__slots__"):
        got = getattr(record, attr, None)
        if attr == "__slots__" and got:
            got = {name: getattr(record, name, None) for name in got}
        if isinstance(got, dict) and got:
            return {
                str(k): v for k, v in got.items()
                if not str(k).startswith("_") and v is not None
            }
    return {}


# ── 时间戳：各种形态统一成 epoch 秒 ──────────────────────────
def parse_ts(value: Any) -> float:
    """把各种时间戳统一成 epoch 秒（本机时区）；认不出返回 0。

    为什么非要有它：宿主的落盘是**懒触发**的，会把**几分钟前的旧对话**当成新内容
    一次性刷出来（对话流尤其明显）。只有拿记录自己的时间戳和"本轮开始时刻"比，
    才能把"迟到的旧回答"和"她刚说的话"分开——踩过：上一轮的回答被当成新一轮的
    答案，面板上就是"跳题"。
    """
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        got = float(value)
        if got > 1e12:                      # 毫秒
            got /= 1000.0
        return got if got > 1e8 else 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return parse_ts(float(text))
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return time.mktime(time.strptime(text[:26], fmt))
        except Exception:
            continue
    return 0.0


# 用户侧的消息一律不算"她的回答"
_USER_SIDE_TYPES = frozenset({
    "user_message", "user_text", "user", "voice_user_message", "asr_text",
    "input_transcript", "text_user_message", "register_text_user_message",
})
_USER_SIDE_ROLES = frozenset({"user", "human", "master", "主人"})


def _record_text(raw: dict) -> str:
    """从一条总线记录里抠出文本。字段名不写死，挨个试常见的几个。"""
    for key in ("text", "content", "message", "body", "visible_text", "speech"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):                     # OpenAI 风格 content 数组
            parts = []
            for item in value:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or ""))
                else:
                    parts.append(str(item))
            joined = "".join(parts).strip()
            if joined:
                return joined
    data = raw.get("data")
    if isinstance(data, dict):
        return _record_text(data)
    return ""


#: 每个命名空间要试的调用形状。挨个试，第一个能拿到东西的记下来（宿主没公开文档）。
#: **优先带 timeout 的形状**——万一某个形状会把调用卡住，有超时至少不会冻住线程。
#: ⚠ `memory` 的签名是 `(*, bucket_id: str, limit=20, timeout=5.0)`，`bucket_id` 必填。
_CALL_SHAPES: dict[str, tuple[dict, ...]] = {
    "conversations": ({"limit": 40, "timeout": 1.0}, {"max_count": 40, "timeout": 1.0},
                      {"limit": 40}, {"max_count": 40}, {}),
    "messages": ({"limit": 40, "timeout": 1.0}, {"max_count": 40, "timeout": 1.0},
                 {"limit": 40}, {"max_count": 40}, {}),
    "events": ({"limit": 40, "timeout": 1.0}, {"limit": 40}, {}),
    "lifecycle": ({"limit": 40, "timeout": 1.0}, {"limit": 40}, {}),
    "memory": ({"bucket_id": "", "limit": 20, "timeout": 1.0},
               {"bucket_id": "", "limit": 20}),
}
#: 查询顺序：**先 conversations**——她的回话属于"对话"，而 `messages` 实测只有
#: `MESSAGE_PUSH`（往对话里推的消息流），插件自己的推送也在里面，不能当她的回答。
_SPACE_ORDER = ("conversations", "messages", "events", "lifecycle", "memory")

#: 这些 `type` 是"往对话里推的消息"，不是谁说的话——**绝不能当回答**。
#: 实测 `ctx.bus.messages` 只给 `MESSAGE_PUSH`，而我们自己的题目推送就在其中。
_PUSH_TYPES = frozenset({"message_push", "push", "proactive_message"})


class YuiBus:
    """实时总线（**内存**，不落盘，所以不滞后）。**这是唯一的实时读回通道。**

    每次 `get` 拿回最近一批记录，所以靠"见过就不再要"去重；开新回合先
    ``mark_seen()`` 把存量吃掉，之后拿到的就都是新的。
    """

    _MAX_SEEN = 400

    def __init__(self, ctx: Any = None, name: str = ""):
        self.ctx = ctx
        self.name = str(name or _DEFAULT_CHARACTER).strip() or _DEFAULT_CHARACTER
        self._seen: dict[str, None] = {}          # 当有序集合用
        self._round: list[str] = []               # 本轮开始以来收到的新话（给"补收"用）
        self._round_records: list[dict] = []      # 同上，带 ts
        self._kinds: list[str] = []
        self._error = ""
        self._shapes: dict[str, str] = {}         # 命名空间 → 哪种调用形状管用
        self._last_count = 0

    # ── 找命名空间 ───────────────────────────────────────────
    def _space(self, attr: str) -> Any:
        bus = getattr(self.ctx, "bus", None)
        return getattr(bus, attr, None) if bus is not None else None

    @property
    def available(self) -> bool:
        bus = getattr(self.ctx, "bus", None)
        if bus is None:
            self._error = "ctx.bus 不存在"
            return False
        for attr in ("messages", "conversations", "events", "memory"):
            space = self._space(attr)
            if space is not None and callable(getattr(space, "get", None)):
                return True
        self._error = "ctx.bus 上没有可用的 get（属性：%s）" % ",".join(
            sorted(n for n in dir(bus) if not n.startswith("_")))
        return False

    def _fetch_space(self, attr: str, limit: int | None = None) -> list[dict]:
        """调某个命名空间，返回它给的记录（dict 列表）。失败返回空。

        ``limit`` 会覆盖调用形状里的 ``limit``/``max_count``（宿主两种都认）。
        """
        space = self._space(attr)
        fn = getattr(space, "get", None)
        if not callable(fn):
            return []
        for base in _CALL_SHAPES.get(attr, ({},)):
            kwargs = base
            if limit:
                kwargs = {k: (int(limit) if k in ("limit", "max_count") else v)
                          for k, v in base.items()}
            try:
                got = run_awaitable(fn(**kwargs))
            except TypeError:
                continue                       # 这个形状不接受这些参数
            except Exception as exc:
                self._error = "%s.get(%s)：%s" % (attr, kwargs, exc)
                continue
            rows = _as_dicts(got)
            if rows:
                if attr not in self._shapes:
                    self._shapes[attr] = "%s.get(%s)" % (
                        attr, ", ".join("%s=%r" % kv for kv in kwargs.items()) or "")
                return rows
        return []


    def _fetch(self, limit: int | None = None) -> list[dict]:
        """**合并所有命名空间**的记录，并标注来源。

        为什么不能"谁先有算谁"：`ctx.bus.messages` 实测只返回 `MESSAGE_PUSH`
        （往对话里推的消息流，插件自己的推送也在里面），而她的回话在
        `conversations` 上。先撞上 messages 就返回，会把**我们自己的题目**当成
        她的回答——这个错真踩过：面板把她答的题显示成我推的题目原文，
        「解析成选项 3/4」其实是从我题目的选项编号里抠出来的。

        :param limit: **每条通道各取这么多条**（不是合起来这么多，见 `records`）。
        """
        out: list[dict] = []
        for attr in _SPACE_ORDER:
            rows = self._fetch_space(attr, limit)
            for row in rows:
                row = dict(row)
                row["__space"] = attr
                out.append(row)
            if attr == "conversations" and not rows:
                # `conversations.get()` 只给"会话"、消息要用 id 再拉一层——
                # 但这层调用一条要花到 1 秒、还可能要好几次，所以**只在主路
                # 真的空手而归时**才走它（每次轮询都走会让整轮慢好几秒）。
                for row in self._fetch_in_conversations(rows):
                    row["__space"] = "conversations.by_id"
                    out.append(row)
        return out

    def _fetch_in_conversations(self, rows: list[dict]) -> list[dict]:
        """按会话 id 再拉一层消息（`conversations.get_by_id`）。"""
        space = self._space("conversations")
        by_id = getattr(space, "get_by_id", None)
        if not callable(by_id):
            return []
        out: list[dict] = []
        ids: list[Any] = []
        for row in rows[-3:]:                     # 只看最近几个会话，别乱翻历史
            for key in ("conversation_id", "id", "key", "conv_id"):
                value = row.get(key)
                if value not in (None, "") and value not in ids:
                    ids.append(value)
                    break
        for cid in ids:
            for kwargs in ({"max_count": 40, "timeout": 1.0}, {}):
                try:
                    got = run_awaitable(by_id(cid, **kwargs))
                except TypeError:
                    try:
                        got = run_awaitable(by_id(cid))   # 位置参数版本
                    except Exception:
                        break
                except Exception:
                    break
                inner = _as_dicts(got)
                if inner:
                    if "conversations.by_id" not in self._shapes:
                        self._shapes["conversations.by_id"] = "get_by_id(%r)" % cid
                    out.extend(inner)
                    break
        return out

    # ── 对外 ────────────────────────────────────────────────
    def records(self, limit: int = 40) -> list[dict]:
        """最近的一批记录，归一化成 ``{space, kind, role, text, ts}``。失败返回空。

        🔴 **``limit`` 是"每条通道各取这么多"，不是"合起来这么多"。**
        这里踩过一个把整条链路废掉的坑：老实现是 ``merged[-limit:]`` —— 取合并后
        列表的**尾部** ``limit`` 条。而 `messages`（插件推送流）在 `_SPACE_ORDER`
        里排在 `conversations` 后面，又**永远有 40 条带正文的记录**，于是切尾之后
        `conversations` 的记录**一条都不剩**。日志里的铁证：轮询每次都打
        ``总线轮询 → messages×40 末条(MESSAGE_PUSH,…)｜本轮新话 0 条 []``，
        而同一秒的逐通道自查（不走 `records`）却能看到 ``conversations：40 条``。
        ——她的回答一直都在总线上，只是被切掉了。

        ⚠ 旧的 `_fetch()` 本身已经按 `limit` 逐通道截断了，这里**不能再切一刀**。
        """
        raw = self._fetch(limit)
        out: list[dict] = []
        for payload in raw:
            text = _record_text(payload)
            if not text:
                continue
            out.append({
                "space": str(payload.get("__space") or ""),
                "kind": str(payload.get("type") or payload.get("kind")
                            or payload.get("event") or "").strip(),
                "role": str(payload.get("role") or payload.get("speaker")
                            or payload.get("sender") or "").strip(),
                "text": text,
                "ts": (payload.get("timestamp") or payload.get("ts")
                       or payload.get("created_at") or payload.get("_ts")),
            })
        self._last_count = len(out)
        return out

    def kinds(self, limit: int = 40) -> list[str]:
        """看到过哪些 ``type``/``role``。面板/日志用它自查"总线到底给了什么"。"""
        seen = set()
        for r in self._fetch(limit):
            for key in ("type", "kind", "event", "role", "speaker", "sender"):
                value = str(r.get(key) or "").strip()
                if value:
                    seen.add(value)
        return sorted(seen)

    def error(self) -> str:
        return self._error

    def _remember(self, kind: str, text: str) -> None:
        key = kind + "\x00" + text
        self._seen.pop(key, None)
        self._seen[key] = None
        while len(self._seen) > self._MAX_SEEN:
            self._seen.pop(next(iter(self._seen)), None)

    def mark_seen(self, limit: int = 40) -> None:
        """把当前存量全部吃掉：之后 ``new_texts()`` 只返回新出现的。

        同时清空本轮累积（新回合重新开始记）。
        """
        self._round = []
        self._round_records = []
        for rec in self.records(limit):
            self._remember(rec["kind"] + "/" + rec["role"], rec["text"])

    def round_texts(self) -> list[str]:
        """**本轮开始以来**收到过的她的话（``mark_seen()`` 之后累积）。"""
        return list(self._round)

    def new_records(self, limit: int = 40) -> list[dict]:
        """她新说的话（排除用户侧/推送类），**带 ts**，供调用方按时间过滤。"""
        fresh: list[dict] = []
        for rec in self.records(limit):
            if rec["kind"].lower() in _PUSH_TYPES:
                continue                    # 往对话里推的消息（含我们自己的题目）
            if rec["kind"] in _USER_SIDE_TYPES:
                continue
            if rec["role"].lower() in _USER_SIDE_ROLES:
                continue
            if is_host_notice(rec["text"]):
                continue                    # 宿主的"系统通知"，不是她说的话
            bucket = rec["kind"] + "/" + rec["role"]
            key = bucket + "\x00" + rec["text"]
            if key in self._seen:
                continue
            self._remember(bucket, rec["text"])
            self._kinds.append(bucket)
            self._round.append(rec["text"])
            self._round_records.append(rec)
            fresh.append(rec)
        return fresh

    def new_texts(self, limit: int = 40) -> list[str]:
        """她新说的话（只有正文；要时间戳请用 ``new_records``）。"""
        return [rec["text"] for rec in self.new_records(limit)]

    def round_records(self) -> list[dict]:
        """本轮开始以来收到过的她的记录（带 ts），给"补收"用。"""
        return list(self._round_records)

    def stats(self) -> dict:
        info: dict[str, Any] = {
            "available": self.available,
            "character": self.name,
            "seen": len(self._seen),
        }
        if not info["available"]:
            info["reason"] = self._error or "ctx.bus 上没有可用的 get"
            return info
        try:
            recs = self.records(40)
        except Exception as exc:
            info["reason"] = str(exc)
            return info
        info["records"] = len(recs)
        info["shapes"] = dict(self._shapes) or "（还没试出可用形状）"
        info["shape"] = ",".join(sorted(self._shapes.values())) or "（还没试出可用形状）"
        info["spaces"] = sorted({r["space"] for r in recs if r["space"]})
        info["kinds"] = sorted({(r["space"] + ":" + r["kind"] + "/" + r["role"]).strip("/:")
                                for r in recs})
        info["sample"] = [{"kind": r["kind"], "role": r["role"], "text": r["text"][:40]}
                          for r in recs[-3:]]
        return info


def bus_dump(ctx: Any, name: str = "") -> dict:
    """把**每个命名空间此刻返回的东西**原样摊开（结构化），供接口一次性取证。

    为什么要这个：排查"她答了却读不到"来回了太多轮，每次都是靠轮询日志间接推断。
    这一个接口直接把"这一刻每条通道给了几条、什么类型、尾巴上是什么原文"摆出来，
    一次就能定位，不必再猜。
    """
    bus = YuiBus(ctx, name)
    out: dict[str, Any] = {}
    for attr in _SPACE_ORDER:
        try:
            rows = bus._fetch_space(attr)
        except Exception as exc:                      # noqa: BLE001 - 取证不能因一条通道挂掉
            out[attr] = {"count": 0, "error": str(exc)}
            continue

        def _kind(row: dict) -> str:
            return str(row.get("type") or row.get("kind") or row.get("event") or "?")

        out[attr] = {
            "count": len(rows),
            "shape": bus._shapes.get(attr) or "（没试出可用形状）",
            "kinds": sorted({_kind(r) for r in rows}),
            # 字段名和**最后一条的完整原样**都要：读回时"这条到底是谁说的话"靠
            # `turn_type`/`source`/`lanlan_name` 这些字段判断，光看正文分不出来。
            "fields": sorted(str(k) for k in rows[-1]) if rows else [],
            "raw": {str(k): str(v)[:120] for k, v in (rows[-1].items() if rows else [])},
            "tail": [{"kind": _kind(r), "text": (_record_text(r) or "")[:90]}
                     for r in rows[-4:]],
        }
    out["__shapes"] = dict(bus._shapes)
    out["__error"] = bus.error()
    return out


def bus_probe(ctx: Any, name: str = "", *, sample: int = 2) -> str:
    """把**每个**总线命名空间都真调一次，报告各自返回了什么。

    宿主没有公开文档。实测教训：`ctx.bus.messages` 只返回 `MESSAGE_PUSH`（往对话里
    推的消息流，插件自己的推送也在里面），而她的回话在 `conversations` 上——
    "谁先有算谁"会把自己的题目当成她的回答。所以必须把每条通道都摊开看一眼，
    把「命名空间 → 记录类型 + 样例」写进日志，一次就能定位。
    """
    bus = YuiBus(ctx, name)
    parts: list[str] = []
    for attr in _SPACE_ORDER:
        space = bus._space(attr)
        if space is None:
            parts.append("%s：无" % attr)
            continue
        rows = bus._fetch_space(attr)
        if not rows:
            parts.append("%s：0 条" % attr)
            continue
        kinds: dict[str, int] = {}
        for row in rows:
            key = str(row.get("type") or row.get("kind") or row.get("event") or "?")
            kinds[key] = kinds.get(key, 0) + 1
        head = ",".join("%s×%d" % (k, v) for k, v in sorted(kinds.items())[:6])
        keys = ",".join(sorted(str(k) for k in rows[0])[:12])
        texts = [(_record_text(r) or "")[:26].replace("\n", " ") for r in rows[-int(sample or 2):]]
        parts.append("%s：%d 条[%s] 字段{%s} 样例%s" % (attr, len(rows), head, keys, texts))
    return "；".join(parts)


def bus_report(ctx: Any) -> str:
    """把 `ctx.bus` 的门面摊开写进日志——宿主没有公开文档，这是唯一的自查手段。

    把每个子命名空间的类名、成员名、`get` 的签名都列出来；下次遇到"读不到"，
    看这一行就够了，不用再猜。
    """
    import inspect

    def names(obj: Any) -> str:
        try:
            return ",".join(sorted(n for n in dir(obj) if not n.startswith("_"))[:26])
        except Exception:
            return "（读不出来）"

    try:
        bus = getattr(ctx, "bus", None)
    except Exception as exc:
        return "ctx.bus 读不到：%s" % exc
    if bus is None:
        return "ctx.bus 不存在"
    parts = ["ctx.bus=%s[%s]" % (type(bus).__name__, names(bus))]
    for attr in ("messages", "conversations", "events", "lifecycle", "memory"):
        space = getattr(bus, attr, None)
        if space is None:
            parts.append("%s 无" % attr)
            continue
        sig = ""
        fn = getattr(space, "get", None)
        if callable(fn):
            try:
                sig = str(inspect.signature(fn))
            except Exception:
                sig = "(签名读不出)"
        parts.append("%s=%s%s" % (attr, type(space).__name__, sig))
    return "；".join(parts)



# ── 她的对话库 ────────────────────────────────────────────────
_TS_CLEAN = re.compile(r"^\[\d{8}\s+\w{3}\s+\d{2}:\d{2}\]\s*")


def _json_objects(text: str) -> list[dict]:
    """从一段话里捞出所有**括号配对**的 JSON 对象。

    不能只靠正则：她可能写成多行、或者外面再套一层（`{"answers": {...}}`），
    所以按括号配对切块再逐个尝试解析。
    """
    out: list[dict] = []
    depth = 0
    start = -1
    for i, ch in enumerate(text or ""):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except Exception:
                        obj = None
                    if isinstance(obj, dict):
                        out.append(obj)
                    start = -1
    return out


def _pick_answers(objs: list[dict], want: int = 0) -> dict | None:
    """在解析出的对象里找 own/guess 两个序号数组（允许外面套一层）。

    ``want`` 给定时必须是**正好那么多题**——她可能先回一句半截的、
    或者回的题数对不上，那些都不能当成答案。
    """
    def norm(obj: dict) -> dict | None:
        try:
            own = [int(x) for x in (obj.get("own") or [])]
            guess = [int(x) for x in (obj.get("guess") or [])]
        except Exception:
            return None
        if not own or len(own) != len(guess):
            return None
        if want and len(own) != int(want):
            return None
        if any(x < 0 or x > 3 for x in own + guess):
            return None
        return {"own": own, "guess": guess}

    for obj in objs:
        got = norm(obj)
        if got:
            return got
        for value in obj.values():            # 外面还套了一层的情况
            if isinstance(value, dict):
                got = norm(value)
                if got:
                    return got
    return None


def _flatten(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                out.append(str(part.get("text") or ""))
            else:
                out.append(str(part))
        return "".join(out)
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return ""


# ── 解析她的自由回答 ──────────────────────────────────────────
_FW = str.maketrans("０１２３４５６７８９", "0123456789")
# 选项编号用**从 1 开始**（人自然会这么数，面板上的按钮也是这么标的），
# 所以这里只认 1..n，0 一律当没写。她的原话里往往还带"主人""本喵"之类的水词，
# 所以先按「一行里出现多少合法编号」挑行，再取该行最后 want 个编号。
_NUM_SPLIT = re.compile(r"[^0-9]+")


def _normalize_reply(text: str) -> str:
    return _TS_CLEAN.sub("", str(text or "")).translate(_FW)


def _digit_picks(line: str, n_options: int) -> list[int]:
    out = []
    for chunk in _NUM_SPLIT.split(line):
        if not chunk:
            continue
        try:
            value = int(chunk)
        except Exception:
            continue
        if 1 <= value <= n_options:
            out.append(value - 1)
    return out


def _letter_picks(line: str, n_options: int) -> list[int]:
    if n_options > 4:
        return []
    out = []
    for match in re.finditer(r"(?<![A-Za-z])([A-Da-d])(?![A-Za-z])", line):
        out.append(ord(match.group(1).upper()) - ord("A"))
    return [i for i in out if i < n_options]


# ── 题号 / 系统通知的噪声 ─────────────────────────────────────
_QREF_PAIR = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})")
_QREF_CN = re.compile(r"第\s*(\d{1,2})\s*题")

#: 宿主会往对话里塞"系统通知"（例如「======[系统通知] 来自插件「neko_assessment」：
#: 默契测试 1/10…」）。那不是她说的话，但会出现在 `conversations` 里——要是被当成
#: "她说的第一条"，整条读回链路当场作废（而且它自带 `1/10`、选项编号，能骗过解析器）。
_NOTICE_MARKS = ("[系统通知]", "========", "来自插件「")

#: 题号行（「8/10」「第4题」）不是"她选的选项"，抠编号前必须先摘掉。
_QREF_TRAIL = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})\s*[:：]?\s*")


def _strip_qref(text: str) -> str:
    """把「8/10」「第4题」这类**题号**摘掉再抠选项编号。

    不摘的话题号里的数字会混进选项编号。她真会写「8/10：①②，可以随时找你」——
    那个 8 要是被算成"她选了第 8 项"，答案就整个错位了。
    """
    body = _QREF_CN.sub(" ", str(text or ""))
    # `1/2` 也可能是普通斜杠（日期、分数），所以只认**行首附近**或**紧跟冒号**的，
    # 免得把她话里的斜杠当题号删掉。
    return "\n".join(
        _QREF_TRAIL.sub(" ", line, count=1)
        if re.match(r"^[^\d]{0,3}\d{1,2}\s*/\s*\d{1,2}\s*[:：]", line.lstrip())
        else line
        for line in body.split("\n")
    )


def is_host_notice(text: str) -> bool:
    """这段文字是不是**宿主自己发的通知**，而不是她或主人说的话。"""
    body = str(text or "")
    if not body:
        return False
    head = body.lstrip()
    return any(mark in head[:60] for mark in _NOTICE_MARKS)


#: `single_pick` 只在这种长度的短句上工作——长句里的数字多半不是选项编号。
_SINGLE_MAX = 30


def single_pick(text: str, n_options: int) -> list[int]:
    """这句话**只**说了一个选项编号时返回它（0-based），否则返回 ``[]``。

    "只"是重点。它用来判断「她这条消息是 ① 槽位还是 ② 槽位」：提示语教她
    「①你自己选的 ②猜主人选的」，于是她常把两个编号分成两条消息发；两条**各恰好
    一个**编号时才能拼成一题的答案。

    ⚠ 不能用 ``parse_picks(text, n, want=1)`` 代替——那个取的是"最后**一个**编号"，
    一句话里有两个编号时它也返回一个，拼出来的"答案"她根本没说。
    """
    n_options = int(n_options or 0)
    if n_options < 2:
        return []
    body = _strip_qref(_normalize_reply(text)).strip()
    if not body or len(body) > _SINGLE_MAX:
        return []
    nums = _digit_picks(body, n_options)
    return nums if len(nums) == 1 else []




# ── 她这句话在说第几题 ────────────────────────────────────────
def referenced_question(text: str, total: int = 10) -> int:
    """她这句话在说第几题（**1-based**）；没说就返回 0。

    实测她真会这么写：「8/10: ①②，可以随时找你。」「5/10到7/10也在前头」。
    有编号就必须按编号认——否则一条同时答了好几题的消息会被按顺序塞给最早的
    未答题（踩过：她答 8/9/10 的那条被塞到了第 3 题，面板上就是"跳题"的样子）。
    """
    body = _normalize_reply(text)
    if not body.strip():
        return 0
    for match in _QREF_PAIR.finditer(body):
        cur = int(match.group(1))
        den = int(match.group(2))
        if den in (0, int(total)) and 1 <= cur <= int(total):
            return cur
    match = _QREF_CN.search(body)
    if match and 1 <= int(match.group(1)) <= int(total):
        return int(match.group(1))
    return 0


def parse_picks(
    text: str,
    n_options: int,
    want: int = 2,
    options: list[str] | None = None,
) -> list[int]:
    """从她的一句话里抠出 ``want`` 个选项，返回 **0-based** 下标。

    认的写法，按可信度依次：
      1. 数字编号（含全角）——挑「合法编号最多」的那一行，取该行最后 ``want`` 个；
      2. ``A``-``D`` 字母；
      3. 选项原文（在整段话里定位，按出现顺序取）。

    抠不出正好 ``want`` 个就返回 ``[]``——**绝不猜、绝不补**。少一个数比编一个数强。
    """
    n_options = int(n_options or 0)
    want = max(1, int(want))
    if n_options < 2:
        return []
    body = _normalize_reply(text)
    if not body.strip():
        return []

    lines = [ln for ln in body.split("\n") if ln.strip()]
    best: list[int] = []
    best_key = (0, 0.0)
    for index, line in enumerate(lines):
        for picks in (_digit_picks(line, n_options), _letter_picks(line, n_options)):
            if len(picks) < want:
                continue
            digits = sum(len(c) for c in _NUM_SPLIT.split(line) if c)
            density = digits / max(1, len(line))
            key = (len(picks), density + index * 1e-9)
            if key > best_key:
                best_key = key
                best = picks
    if best:
        return best[-want:]

    # 逐行抠不出来时，把**整段**当成一句再抠一次。
    # 为什么必须这样：提示语教她「回两个数字：①你自己选的 ②猜主人选的」，她真就写成
    # 两行——「①3，自然醒没人吵…」「②猜你选2，…」。一行只有**一个**编号，逐行抠永远
    # 凑不满两个，于是"她明明答了、我这边却判定成没答上"。近几轮全栽在这上面。
    flat = _strip_qref(body)
    whole = _digit_picks(flat, n_options)
    if len(whole) >= want:
        return whole[-want:]
    whole_letters = _letter_picks(flat, n_options)
    if len(whole_letters) >= want:
        return whole_letters[-want:]

    if options:
        hits: list[tuple[int, int]] = []
        for idx, label in enumerate(options[:n_options]):
            needle = _normalize_reply(label).strip()
            if not needle:
                continue
            pos = body.find(needle)
            if pos >= 0:
                hits.append((pos, idx))
        hits.sort()
        seen: list[int] = []
        for _, idx in hits:
            if idx not in seen:
                seen.append(idx)
        if len(seen) >= want:
            return seen[-want:]
    return []


class YuiDialog:
    """只读她的对话库：用来把「她刚说的那句」读回来。"""

    def __init__(self, yui_dir: str = "", source: str = ""):
        self.yui_dir = str(yui_dir or "")
        self.source = str(source or "")
        self.db_path = os.path.join(self.yui_dir, "time_indexed.db") if self.yui_dir else ""

    @property
    def available(self) -> bool:
        return bool(self.db_path) and os.path.isfile(self.db_path)

    def _query(self, sql: str, params: tuple) -> list[tuple]:
        if not self.available:
            return []
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % self.db_path.replace("\\", "/"), uri=True,
                                   timeout=2.0)
        except Exception:
            return []
        try:
            return conn.execute(sql, params).fetchall()
        except Exception:
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def latest_ts(self) -> str:
        """库里最新一条消息的时间戳（任何角色）。找不到返回空串。

        :warning: 时间戳**不能**当游标用——同一批快照写入的多行共享同一时间戳。
        """
        rows = self._query("select max(timestamp) from time_indexed_original", ())
        return str(rows[0][0] or "") if rows else ""

    def mark(self) -> int:
        """当前最大 ``id``。推题前记下，之后 ``id > mark`` 的就是新增行。"""
        rows = self._query("select max(id) from time_indexed_original", ())
        try:
            return int(rows[0][0] or 0) if rows else 0
        except Exception:
            return 0

    def new_replies(self, mark: int, *, limit: int = 20) -> list[dict]:
        """``id`` 大于 ``mark`` 的她(AI)的新发言，按 id 升序。"""
        try:
            after = int(mark)
        except Exception:
            after = 0
        rows = self._query(
            "select id, message, timestamp from time_indexed_original "
            "where id > ? order by id asc limit ?", (after, int(limit)))
        out = []
        for row_id, msg, ts in rows:
            try:
                obj = json.loads(msg)
            except Exception:
                continue
            if str(obj.get("type") or "") != "ai":
                continue
            text = _TS_CLEAN.sub("", _flatten((obj.get("data") or {}).get("content"))).strip()
            if text:
                out.append({"id": int(row_id), "ts": str(ts), "text": text})
        return out

    def messages_since(self, since_ts: str, *, role: str = "", limit: int = 40) -> list[dict]:
        """取时间戳晚于 ``since_ts`` 的消息（时间戳是 ISO 字符串，字典序即时间序）。"""
        rows = self._query(
            "select message, timestamp from time_indexed_original "
            "where timestamp > ? order by id asc limit ?", (str(since_ts or ""), int(limit))
        )
        out = []
        for msg, ts in rows:
            try:
                obj = json.loads(msg)
            except Exception:
                continue
            kind = str(obj.get("type") or "")
            if role and kind != role:
                continue
            text = _TS_CLEAN.sub("", _flatten((obj.get("data") or {}).get("content"))).strip()
            if text:
                out.append({"type": kind, "ts": str(ts), "text": text})
        return out

    def latest_reply(self) -> dict | None:
        """最近一条她(AI)说的话。"""
        rows = self._query(
            "select message, timestamp from time_indexed_original "
            "where json_extract(message, '$.type') = 'ai' "
            "order by id desc limit 1", ())
        if not rows:
            # 老版本 sqlite 没有 json_extract 时退回 Python 侧过滤
            items = self.messages_since("", role="ai", limit=100000)
            return items[-1] if items else None
        try:
            obj = json.loads(rows[0][0])
        except Exception:
            return None
        text = _TS_CLEAN.sub("", _flatten((obj.get("data") or {}).get("content"))).strip()
        return {"type": "ai", "ts": str(rows[0][1]), "text": text} if text else None

    def find_answers(self, since_ts: str, want: int = 0) -> dict | None:
        """在 ``since_ts`` 之后她说的话里找含有 own/guess 的那条，解析出答案。

        返回 ``{"own": [...], "guess": [...], "reply": 原文, "ts": ...}``；没找到返回 None。
        """
        for item in self.messages_since(since_ts, role="ai", limit=200):
            got = _pick_answers(_json_objects(item["text"]), want)
            if got:
                got.update({"reply": item["text"], "ts": item["ts"]})
                return got
        return None

    def stats(self) -> dict:
        if not self.yui_dir:
            return {"available": False, "reason": "没定位到她的记忆目录"}
        info: dict[str, Any] = {
            "available": self.available,
            "yui_dir": self.yui_dir,
            "db_path": self.db_path,
            "source": self.source,
        }
        if not self.available:
            info["reason"] = ("对话库不存在（%s）" % self.db_path)
            return info
        rows = self._query("select count(*), max(timestamp) from time_indexed_original limit 1", ())
        if rows:
            info["messages"] = int(rows[0][0] or 0)
            info["latest"] = str(rows[0][1] or "")
        return info
