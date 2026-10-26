"""SQLite 持久化层。

对应 P2 契约 §1 的 7 张表 + P3 契约 §4.3 新增的第 8 张表。设计要点：

* 所有 SQL 都用 ``?`` 占位符绑定参数，**禁止**字符串拼接。
* 外键由 SDK 开启（``PluginDatabase._init_db`` 会执行
  ``PRAGMA foreign_keys = ON``），这里不重复设置。
* 行工厂是 ``sqlite3.Row``，可以按列名访问。
* 连接是 ``check_same_thread=False``，跨线程安全；每次 :meth:`Repo.reader`
  拿到的 session 都会在退出时关闭。
* ``self.db`` 的 ``session()`` **每次新建连接**，所以插件层不需要自己管连接池；
  需要防的是"读-改-写"的丢失更新，那由插件层的全局锁负责（P3 契约 §1.3）。

教育目的声明
------------
这里存的是**纯虚构**对局的状态。所有标的都是 ``FAKE-xxx``，价格与手续费
都是游戏数值，无现金价值，也不涉及任何真实支付渠道。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Sequence

__all__ = ["DbUnavailableError", "Repo", "SCHEMA_STATEMENTS"]


class DbUnavailableError(RuntimeError):
    """数据库不可用（未启用、无法打开会话或执行失败）。"""


SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS players (
        id           TEXT    PRIMARY KEY,
        name         TEXT    NOT NULL,
        cash         REAL    NOT NULL DEFAULT 0,
        mode         TEXT    NOT NULL,
        day          INTEGER NOT NULL DEFAULT 1,
        alive        INTEGER NOT NULL DEFAULT 1,
        initial_cash REAL    NOT NULL DEFAULT 0,
        trade_count  INTEGER NOT NULL DEFAULT 0,
        heat         REAL    NOT NULL DEFAULT 0,
        total_fee_paid REAL  NOT NULL DEFAULT 0,
        tokens       INTEGER NOT NULL DEFAULT 0,
        strikes      INTEGER NOT NULL DEFAULT 0,
        loan_debt    REAL    NOT NULL DEFAULT 0,
        death_reason TEXT,
        death_title  TEXT,
        created_at   REAL    NOT NULL,
        updated_at   REAL    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        player_id        TEXT    NOT NULL,
        stock_id         TEXT    NOT NULL,
        amount           REAL    NOT NULL,
        avg_cost         REAL    NOT NULL,
        today_bought     REAL    NOT NULL DEFAULT 0,
        today_bought_day INTEGER NOT NULL DEFAULT 0,
        opened_day       INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (player_id, stock_id),
        FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trades (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id  TEXT    NOT NULL,
        stock_id   TEXT    NOT NULL,
        side       TEXT    NOT NULL CHECK (side IN ('buy', 'sell')),
        amount     REAL    NOT NULL,
        price      REAL    NOT NULL,
        fee        REAL    NOT NULL,
        tick       INTEGER NOT NULL,
        day        INTEGER NOT NULL,
        reason     TEXT    NOT NULL DEFAULT 'manual',
        created_at REAL    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trades_player_tick ON trades(player_id, tick)",
    """
    CREATE TABLE IF NOT EXISTS heat_history (
        player_id TEXT    NOT NULL,
        tick      INTEGER NOT NULL,
        day       INTEGER NOT NULL,
        heat      REAL    NOT NULL,
        PRIMARY KEY (player_id, tick),
        FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_state (
        id         INTEGER PRIMARY KEY CHECK (id = 1),
        tick       INTEGER NOT NULL DEFAULT 0,
        day        INTEGER NOT NULL DEFAULT 1,
        phase      TEXT    NOT NULL DEFAULT 'honeymoon',
        seed       INTEGER NOT NULL,
        player_id  TEXT    NOT NULL DEFAULT '',
        fee_pool   REAL    NOT NULL DEFAULT 0,
        total_fees REAL    NOT NULL DEFAULT 0,
        updated_at REAL    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS news_log (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        day       INTEGER NOT NULL,
        news_id   TEXT    NOT NULL,
        news_type TEXT    NOT NULL CHECK (news_type IN ('true','fake','vague','thunder','noise')),
        stock_id  TEXT,
        content   TEXT    NOT NULL,
        accuracy  REAL,
        UNIQUE (day, news_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_news_day ON news_log(day)",
    """
    CREATE TABLE IF NOT EXISTS pawnbroker_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id  TEXT    NOT NULL,
        tick       INTEGER NOT NULL,
        day        INTEGER NOT NULL,
        action     TEXT    NOT NULL CHECK (action IN ('forced_sell', 'forced_buy')),
        stock_id   TEXT    NOT NULL,
        price      REAL    NOT NULL,
        reason     TEXT    NOT NULL,
        created_at REAL    NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pawnbroker_player_tick ON pawnbroker_log(player_id, tick)",
    # ── P5.5 改动 5：称号解锁表（**独立表**，P5.5 拍板细节 B）──────────
    # 主键 (player_id, title) → 同一称号只解锁一次（天然幂等）。
    # ``unlocked_at`` 存 **tick 序号**（P5.5 拍板：tick 序号，不是时间戳）。
    """
    CREATE TABLE IF NOT EXISTS achievements (
        player_id   TEXT    NOT NULL,
        title       TEXT    NOT NULL,
        unlocked_at INTEGER NOT NULL,
        PRIMARY KEY (player_id, title)
    )
    """,
    # ── 第 8 张表：收割流水（P3 契约 §4.3）─────────────────────────────
    # 只记**收割**（强制平仓）。二道贩子的主动/被动卖出记在 pawnbroker_log，
    # 两张表不混用。
    """
    CREATE TABLE IF NOT EXISTS liquidation_log (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id              TEXT    NOT NULL,
        tick                   INTEGER NOT NULL,
        day                    INTEGER NOT NULL,
        phase                  TEXT    NOT NULL,
        trigger                TEXT    NOT NULL CHECK (trigger IN ('phase', 'peak')),
        stock_id               TEXT    NOT NULL,
        severity               REAL    NOT NULL,
        player_heat_at_harvest REAL    NOT NULL,
        concentration_at_harvest REAL  NOT NULL,
        death_reason           TEXT,
        created_at             REAL    NOT NULL,
        FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_liquidation_player_tick ON liquidation_log(player_id, tick)",
    # ── P10：插件级偏好设置（键值对）──────────────────────────────────
    # 这里只放**不参与任何游戏规则**的偏好：游戏速度、自动/手动推进、
    # 新手引导已读标记。全部是"玩家怎么玩"的设置，不是"游戏怎么算"的参数，
    # 引擎层（engine/）与模式层（modes/）一行不改。
    """
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
)

#: ``players`` 表在 P3 新增的列（幂等补齐，见 :meth:`Repo.ensure_schema`）。
#:
#: P2 建表时还没有这几列，而 P2 的存档可能已经存在，所以**不能**重建表，
#: 只能用 ``ALTER TABLE ... ADD COLUMN`` 补。
_PLAYER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("pending_action", "TEXT"),
    ("death_reason", "TEXT"),
    ("death_title", "TEXT"),
    ("is_catgirl", "INTEGER NOT NULL DEFAULT 0"),
    # ── P4：低保与高利贷的三列 ──────────────────────────────────────
    # welfare_missed_days：连续未领低保的天数（超过上限 → welfare_neglect）
    ("welfare_missed_days", "INTEGER NOT NULL DEFAULT 0"),
    # loan_taken_day：最近一次借款发生在第几天（每天限借一次）
    ("loan_taken_day", "INTEGER NOT NULL DEFAULT 0"),
    # welfare_claimed_day：最近一次领取低保发生在第几天（周期内领取幂等）
    ("welfare_claimed_day", "INTEGER NOT NULL DEFAULT 0"),
    # ── P5：分析次数的两列 ────────────────────────────────────────────
    # analyses_day：最近一次分析发生在第几天（每天重置分析机会）
    ("analyses_day", "INTEGER NOT NULL DEFAULT 0"),
    # analyses_used：今天已经用掉的分析次数
    ("analyses_used", "INTEGER NOT NULL DEFAULT 0"),
    # ── P5.5：入场身份（一列，纯叙事，不参与任何判定）──────────────────
    # identity：入场时选的身份（debt / prove / quick）
    ("identity", "TEXT NOT NULL DEFAULT 'quick'"),
    # ── P5.5 改动 6：跳过本日 + 无为而治解锁标记 ────────────────────
    # skip_day_used：最近一次「跳过本日」发生在第几天（每天限跳一次）
    ("skip_day_used", "INTEGER NOT NULL DEFAULT 0"),
    # inaction_unlocked：是否已解锁「无为而治」称号
    ("inaction_unlocked", "INTEGER NOT NULL DEFAULT 0"),
    # ── P10：新手引导已读标记（**记在这个 player_id 行上**）────────────
    # 玩家点掉【玩法说明】弹窗后置 1；同一局不再弹，开了新局再看一次说明。
    ("onboarding_seen", "INTEGER NOT NULL DEFAULT 0"),
    # ── P18.0：分档次动态教程的进度（一列，JSON 文本）─────────────────
    # 形如 ``{"classic_arcade": {"phase": "step", "index": 2}}`` ——
    # **按档位分别记录**：换了难度就是另一条进度，互不干扰。
    ("tutorial_state", "TEXT"),
)

#: ``market_state`` 表在 P4 新增的列（幂等补齐）。
#:
#: ``reversal_days`` 存反转日集合的 JSON 文本（``"[3, 11, 24]"``）。
_MARKET_STATE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("reversal_days", "TEXT"),
)

#: ``news_log`` 表在 P4 新增的列（幂等补齐）。
#:
#: ``direction`` 存新闻的多空方向（``bullish`` / ``bearish`` / ``NULL``）。
#: 有了它，插件重启后重建出来的当天新闻才与落库的那一批**完全一致** ——
#: 否则重新生成的新闻可能因为阶段不同而对不上。
_NEWS_LOG_COLUMNS: tuple[tuple[str, str], ...] = (
    ("direction", "TEXT"),
)


class Repo:
    """对 ``self.db`` 的最小封装：读、写、事务。"""

    def __init__(self, db: Any) -> None:
        self._db = db
        #: schema 是否已经建好（进程内缓存）。
        #:
        #: **懒建表**：宿主可能在插件实例构造**之后**才把有效配置下发到插件
        #: 进程 —— 那时 ``db.enabled`` 还是 False，启动时的建表会失败。
        #: 等配置到了、DB 能用了，必须**自动补建**，否则表永远缺着，
        #: 之后每次查询都会撞 "no such table"，插件卡在降级状态出不来。
        self._schema_ready = False

    @property
    def enabled(self) -> bool:
        """``[plugin.database].enabled`` 是否打开。"""
        return bool(getattr(self._db, "enabled", False))

    async def _open_session(self) -> Any:
        if not self.enabled:
            raise DbUnavailableError("plugin database is disabled")
        result = await self._db.session()
        if result.is_err():
            raise DbUnavailableError(str(result.err()))
        return result.unwrap()

    @asynccontextmanager
    async def reader(self) -> AsyncIterator[Any]:
        """只读会话；退出时自动关闭。

        读之前**先确保 schema 存在**（懒建表自愈，见 ``ensure_schema``）。
        """
        await self.ensure_schema()
        session = await self._open_session()
        try:
            yield session
        finally:
            await session.close()

    async def ensure_schema(self) -> None:
        """幂等地建表建索引；已建好就直接返回。

        **懒建表 + 自愈**：

        * 只在第一次真正用数据库时建表（``CREATE TABLE IF NOT EXISTS`` 开销是
          毫秒级，用 ``_schema_ready`` 标记免掉后续每次读写的开销）；
        * 宿主把有效配置补发下来之后，下一次读写会自动补建 schema ——
          不再像"只在 startup 建一次"那样，失败一次就永远缺表；
        * 数据库被删掉也能自愈（``CREATE TABLE IF NOT EXISTS``）。

        **刻意不加锁**：宿主的定时器每次心跳都用 ``asyncio.run()`` 开一个
        **新事件循环**，跨循环的 ``asyncio.Lock`` 不可靠；而这里的语句本身
        就是幂等的（``CREATE TABLE IF NOT EXISTS``），并发补列的竞争由
        ``_ensure_columns`` 自己吞掉。
        """
        if self._schema_ready:
            return
        session = await self._open_session()
        try:
            for statement in SCHEMA_STATEMENTS:
                await session.execute(statement)
            await self._ensure_columns(session, "players", _PLAYER_COLUMNS)
            await self._ensure_columns(session, "market_state", _MARKET_STATE_COLUMNS)
            await self._ensure_columns(session, "news_log", _NEWS_LOG_COLUMNS)
            await session.commit()
        finally:
            await session.close()
        self._schema_ready = True

    @staticmethod
    async def _ensure_columns(
        session: Any, table: str, columns: tuple[tuple[str, str], ...]
    ) -> None:
        """幂等地给指定表补齐新增的列。

        先查 ``PRAGMA table_info``，缺了才 ``ALTER TABLE ADD COLUMN``。
        这样 P2/P3 留下的老存档不需要迁移，也不会丢数据。
        """
        cursor = await session.execute(f"PRAGMA table_info({table})")
        existing = {row["name"] for row in cursor.fetchall()}
        for name, ddl in columns:
            if name in existing:
                continue
            await session.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    async def fetch_all(
        self,
        sql: str,
        params: Sequence[Any] = (),
    ) -> list[Any]:
        """查询多行。"""
        async with self.reader() as session:
            cursor = await session.execute(sql, params)
            return list(cursor.fetchall())

    async def fetch_one(
        self,
        sql: str,
        params: Sequence[Any] = (),
    ) -> Any | None:
        """查询单行；没有则返回 ``None``。"""
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None

    async def transaction(
        self,
        actions: Sequence[tuple[str, Sequence[Any]]],
    ) -> list[Any]:
        """在一个事务里执行多条语句。

        返回每个语句产生的 cursor（最后一个 INSERT 的 ``lastrowid`` 即自增主键）。
        任何一步失败都会整体回滚。

        写之前同样**先确保 schema 存在**（懒建表自愈）。
        """
        await self.ensure_schema()
        session = await self._open_session()
        cursors: list[Any] = []
        try:
            for sql, params in actions:
                cursors.append(await session.execute(sql, params))
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
        return cursors

    # ------------------------------------------------------------------
    # P10：插件级偏好设置
    # ------------------------------------------------------------------

    async def get_setting(self, key: str) -> str | None:
        """读一条插件级设置；没有就返回 ``None``。

        数据库不可用时**返回 ``None`` 而不是抛异常** —— 偏好设置读不到时
        插件必须退回到"用默认值继续跑"，绝不能因为一个可选偏好把整局卡死。
        """
        try:
            rows = await self.fetch_all(
                "SELECT value FROM settings WHERE key = ?", (key,)
            )
        except DbUnavailableError:
            return None
        if not rows:
            return None
        row = rows[0]
        try:
            return str(row["value"])
        except (KeyError, TypeError, IndexError):  # pragma: no cover - 驱动差异兜底
            return str(row)

    async def get_settings_map(self) -> dict[str, str]:
        """一次查询读出**全部**插件级设置（键 → 值）。

        **一次会话读完所有键**：面板的上下文请求有 5 秒超时（宿主侧），
        而宿主数据库的每次会话往返都不便宜 —— 一个键一次查询会把上下文
        拖到超时边缘。所以这里只开**一个**会话把整张表读回来。
        """
        try:
            rows = await self.fetch_all("SELECT key, value FROM settings")
        except DbUnavailableError:
            return {}
        out: dict[str, str] = {}
        for row in rows:
            try:
                out[str(row["key"])] = str(row["value"])
            except (KeyError, TypeError, IndexError):  # pragma: no cover - 驱动差异兜底
                continue
        return out

    async def set_setting(self, key: str, value: str) -> bool:
        """写一条插件级设置；成功返回 ``True``。

        ``INSERT OR REPLACE`` 而不是 ``INSERT ... ON CONFLICT``：老版本
        SQLite 不支持 upsert 语法，而 ``OR REPLACE`` 从 SQLite 3 就有。
        """
        try:
            await self.transaction(
                [("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))]
            )
        except DbUnavailableError:
            return False
        except Exception:  # pragma: no cover - 写设置失败不该拖垮主流程
            return False
        return True

    async def cas_setting(self, key: str, expected: str, value: str) -> bool:
        """**CAS**（compare-and-swap）写一条插件级设置。

        只有当该键**当前的值**仍等于 ``expected`` 时才写进去；
        返回 ``True`` = 写成功，``False`` = 别人先改了（调用方应当重读重试）。

        **为什么要有这个**：剧情状态是一整块 JSON，"读 → 改 → 写回"。
        两个地方（心跳的剧情判定、玩家的剧情选项）都这么干的话，不串行化
        就会**丢更新**（玩家的选择被心跳那一写覆盖掉 ⇒ 钱发两次）。

        用 CAS 而不是"加锁"是有原因的：**插件是单线程事件循环**，
        持锁 ``await``（数据库往返）会把事件循环冻住 ——
        那正是 P11.1 那个 ``Failed to query plugin UI action context``
        （500 红色警告）的根因。CAS 全程**不持锁**。
        """
        try:
            await self.ensure_schema()
            session = await self._open_session()
            try:
                cursor = await session.execute(
                    "UPDATE settings SET value = ? WHERE key = ? AND value = ?",
                    (value, key, expected),
                )
                await session.commit()
                return int(getattr(cursor, "rowcount", 0) or 0) > 0
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
        except DbUnavailableError:
            return False
        except Exception:  # pragma: no cover - 写设置失败不该拖垮主流程
            return False

    async def mark_player_onboarding_seen(self, player_id: str) -> bool:
        """把某个 ``player_id`` 标记成"已经看过玩法说明"。

        **引导状态记在 player_id 上**：玩家行就是这一局的身份，读写都靠它。
        """
        if not player_id:
            return False
        try:
            await self.transaction(
                [
                    (
                        "UPDATE players SET onboarding_seen = 1 WHERE id = ?",
                        (player_id,),
                    )
                ]
            )
        except DbUnavailableError:
            return False
        except Exception:  # pragma: no cover - 写失败不该拖垮主流程
            return False
        return True

    async def player_onboarding_seen(self, player_id: str) -> bool:
        """查某个 ``player_id`` 是否已经看过玩法说明。"""
        if not player_id:
            return False
        try:
            row = await self.fetch_one(
                "SELECT onboarding_seen FROM players WHERE id = ?", (player_id,)
            )
        except DbUnavailableError:
            return False
        if row is None:
            return False
        try:
            return bool(row["onboarding_seen"])
        except (KeyError, TypeError, IndexError):  # pragma: no cover - 驱动差异兜底
            return bool(row)
