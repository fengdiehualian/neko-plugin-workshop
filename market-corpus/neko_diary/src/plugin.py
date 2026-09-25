"""
猫娘日记插件 - 核心业务逻辑

提供日记的增删改查、心情标记、搜索、统计等功能。
使用 SQLite 存储数据。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# 心情类型定义
MOOD_TYPES = {
    "happy": {"label": "开心", "emoji": "😊", "color": "#FFD700"},
    "sad": {"label": "难过", "emoji": "😢", "color": "#6B8E9B"},
    "angry": {"label": "生气", "emoji": "😠", "color": "#CD5C5C"},
    "anxious": {"label": "焦虑", "emoji": "😰", "color": "#9370DB"},
    "calm": {"label": "平静", "emoji": "😌", "color": "#87CEEB"},
    "excited": {"label": "兴奋", "emoji": "🤩", "color": "#FF69B4"},
    "tired": {"label": "疲惫", "emoji": "😴", "color": "#A9A9A9"},
    "neutral": {"label": "一般", "emoji": "😐", "color": "#D3D3D3"},
}


class NekoDiaryPlugin:
    """猫娘日记插件核心类"""

    def __init__(
        self,
        db_path: str = "",
        master_name: str = "主人",
        catgirl_name: str = "喵喵",
        default_mood: str = "neutral",
        page_size: int = 20,
    ):
        self.db_path = db_path
        self.master_name = master_name
        self.catgirl_name = catgirl_name
        self.default_mood = default_mood if default_mood in MOOD_TYPES else "neutral"
        self.page_size = page_size
        self._conn: Optional[sqlite3.Connection] = None

    def initialize(self) -> None:
        """初始化数据库连接和表结构"""
        # 确定数据库路径
        if not self.db_path:
            # 使用默认路径：插件目录下的 data/neko_diary.db
            plugin_dir = Path(__file__).parent
            data_dir = plugin_dir / "data"
            data_dir.mkdir(exist_ok=True)
            self.db_path = str(data_dir / "neko_diary.db")

        # 连接数据库
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        
        # 创建表
        self._create_tables()

    def _create_tables(self) -> None:
        """创建数据库表"""
        if self._conn is None:
            raise RuntimeError("数据库未初始化")

        cursor = self._conn.cursor()

        # 日记条目表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS diary_entries (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                title TEXT,
                content TEXT NOT NULL,
                mood TEXT,
                tags TEXT,
                attachments TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                deleted INTEGER DEFAULT 0,
                visibility TEXT DEFAULT 'public'
            )
        """)
        self._conn.commit()

        # 迁移：用 PRAGMA table_info 检查列是否存在（比 SELECT 更可靠）
        cursor.execute("PRAGMA table_info(diary_entries)")
        existing_cols = {row[1] for row in cursor.fetchall()}
        if "visibility" not in existing_cols:
            cursor.execute("ALTER TABLE diary_entries ADD COLUMN visibility TEXT DEFAULT 'public'")
            # 把已有旧数据全部标记为 public
            cursor.execute("UPDATE diary_entries SET visibility = 'public' WHERE visibility IS NULL")
            self._conn.commit()

        # 创建索引（此时 visibility 列必定存在）
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_diary_date ON diary_entries(date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_diary_mood ON diary_entries(mood)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_diary_deleted ON diary_entries(deleted)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_diary_visibility ON diary_entries(visibility)")
        self._conn.commit()

    def close(self) -> None:
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _now_iso(self) -> str:
        """获取当前时间的 ISO 格式字符串"""
        return datetime.now().isoformat(timespec="seconds")

    def _today_str(self) -> str:
        """获取今天的日期字符串"""
        return datetime.now().strftime("%Y-%m-%d")

    # ── 日记写入 ─────────────────────────────────────────────────────

    def write_entry(
        self,
        content: str,
        title: Optional[str] = None,
        mood: Optional[str] = None,
        tags: Optional[List[str]] = None,
        attachments: Optional[List[str]] = None,
        visibility: str = "public",
    ) -> Dict[str, Any]:
        """写一篇日记"""
        if not content or not content.strip():
            return {"error": "日记内容不能为空"}

        # 验证心情类型
        if mood and mood not in MOOD_TYPES:
            mood = self.default_mood
        elif not mood:
            mood = self.default_mood

        # 验证可见性
        if visibility not in ("public", "private"):
            visibility = "public"

        entry_id = uuid.uuid4().hex[:12]
        now = self._now_iso()
        today = self._today_str()

        # 序列化标签和附件
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        attachments_json = json.dumps(attachments or [], ensure_ascii=False)

        try:
            if self._conn is None:
                return {"error": "数据库未初始化"}

            cursor = self._conn.cursor()
            cursor.execute("""
                INSERT INTO diary_entries (id, date, title, content, mood, tags, attachments, created_at, visibility)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (entry_id, today, title, content.strip(), mood, tags_json, attachments_json, now, visibility))
            self._conn.commit()

            # 构建返回结果
            mood_info = MOOD_TYPES.get(mood, MOOD_TYPES["neutral"])
            vis_label = "仅自己可见" if visibility == "private" else "公开"
            return {
                "success": True,
                "entry_id": entry_id,
                "date": today,
                "title": title,
                "mood": mood,
                "mood_label": mood_info["label"],
                "mood_emoji": mood_info["emoji"],
                "tags": tags or [],
                "visibility": visibility,
                "visibility_label": vis_label,
                "created_at": now,
                "message": f"日记已保存喵~ 今天的心情是{mood_info['emoji']}{mood_info['label']}哦 ({vis_label})",
            }
        except Exception as e:
            return {"error": f"保存日记失败: {e}"}

    # ── 日记浏览 ─────────────────────────────────────────────────────

    def browse_entries(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        visibility: Optional[str] = None,
    ) -> Dict[str, Any]:
        """浏览日记时间线

        Args:
            visibility: None=全部(含私有), 'public'=仅公开, 'private'=仅私有
        """
        if self._conn is None:
            return {"error": "数据库未初始化"}

        limit = limit or self.page_size
        offset = offset or 0

        # 构建查询条件
        conditions = ["deleted = 0"]
        params: List[Any] = []

        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)
        if visibility in ("public", "private"):
            conditions.append("visibility = ?")
            params.append(visibility)

        where_clause = " AND ".join(conditions)

        try:
            cursor = self._conn.cursor()

            # 获取总数
            cursor.execute(f"SELECT COUNT(*) FROM diary_entries WHERE {where_clause}", params)
            total = cursor.fetchone()[0]

            # 获取分页数据
            cursor.execute(f"""
                SELECT id, date, title, content, mood, tags, attachments, created_at, visibility
                FROM diary_entries
                WHERE {where_clause}
                ORDER BY date DESC, created_at DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])

            entries = []
            for row in cursor.fetchall():
                entry = self._row_to_entry(row)
                entries.append(entry)

            return {
                "entries": entries,
                "total": total,
                "offset": offset,
                "count": len(entries),
                "limit": limit,
            }
        except Exception as e:
            return {"error": f"浏览日记失败: {e}"}

    def get_today_entries(self) -> Dict[str, Any]:
        """获取今天的日记"""
        today = self._today_str()
        return self.browse_entries(start_date=today, end_date=today, limit=100)

    def _row_to_entry(self, row: sqlite3.Row) -> Dict[str, Any]:
        """将数据库行转换为日记条目字典"""
        mood = row["mood"] or "neutral"
        mood_info = MOOD_TYPES.get(mood, MOOD_TYPES["neutral"])

        # 解析 JSON 字段
        try:
            tags = json.loads(row["tags"] or "[]")
        except Exception:
            tags = []
        try:
            attachments = json.loads(row["attachments"] or "[]")
        except Exception:
            attachments = []

        # 获取可见性字段，默认为 public
        visibility = row["visibility"] if "visibility" in row.keys() else "public"
        visibility_label = "仅自己可见" if visibility == "private" else "公开"

        return {
            "id": row["id"],
            "date": row["date"],
            "title": row["title"],
            "content": row["content"],
            "mood": mood,
            "mood_label": mood_info["label"],
            "mood_emoji": mood_info["emoji"],
            "mood_color": mood_info["color"],
            "tags": tags,
            "attachments": attachments,
            "created_at": row["created_at"],
            "visibility": visibility,
            "visibility_label": visibility_label,
        }

    # ── 日记搜索 ─────────────────────────────────────────────────────

    def search_entries(self, keyword: str, limit: Optional[int] = None) -> Dict[str, Any]:
        """搜索日记"""
        if not keyword or not keyword.strip():
            return {"error": "搜索关键词不能为空"}

        if self._conn is None:
            return {"error": "数据库未初始化"}

        limit = limit or self.page_size
        keyword = keyword.strip()

        try:
            cursor = self._conn.cursor()

            # 搜索标题、内容、标签
            search_pattern = f"%{keyword}%"
            cursor.execute("""
                SELECT id, date, title, content, mood, tags, attachments, created_at, visibility
                FROM diary_entries
                WHERE deleted = 0
                  AND (title LIKE ? OR content LIKE ? OR tags LIKE ?)
                ORDER BY date DESC, created_at DESC
                LIMIT ?
            """, (search_pattern, search_pattern, search_pattern, limit))

            entries = []
            for row in cursor.fetchall():
                entry = self._row_to_entry(row)
                entries.append(entry)

            return {
                "entries": entries,
                "total": len(entries),
                "count": len(entries),
                "keyword": keyword,
            }
        except Exception as e:
            return {"error": f"搜索日记失败: {e}"}

    # ── 心情统计 ─────────────────────────────────────────────────────

    def get_mood_stats(
        self,
        days: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """获取心情统计"""
        if self._conn is None:
            return {"error": "数据库未初始化"}

        # 确定日期范围
        if start_date and end_date:
            date_from = start_date
            date_to = end_date
        elif days:
            date_to = self._today_str()
            date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        else:
            # 默认最近7天
            date_to = self._today_str()
            date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        try:
            cursor = self._conn.cursor()

            # 获取日期范围内的心情数据
            cursor.execute("""
                SELECT date, mood, COUNT(*) as count
                FROM diary_entries
                WHERE deleted = 0 AND date >= ? AND date <= ?
                GROUP BY date, mood
                ORDER BY date
            """, (date_from, date_to))

            # 构建统计结果
            mood_distribution: Dict[str, int] = {}
            daily_moods: Dict[str, str] = {}

            for row in cursor.fetchall():
                date = row["date"]
                mood = row["mood"] or "neutral"
                count = row["count"]

                # 心情分布
                mood_distribution[mood] = mood_distribution.get(mood, 0) + count

                # 每日心情（取当天第一条）
                if date not in daily_moods:
                    daily_moods[date] = mood

            # 找出主导心情
            dominant_mood = max(mood_distribution.items(), key=lambda x: x[1])[0] if mood_distribution else "neutral"
            dominant_info = MOOD_TYPES.get(dominant_mood, MOOD_TYPES["neutral"])

            # 构建带标签的心情分布
            mood_distribution_labeled = []
            for mood_key, count in mood_distribution.items():
                info = MOOD_TYPES.get(mood_key, MOOD_TYPES["neutral"])
                mood_distribution_labeled.append({
                    "mood": mood_key,
                    "label": info["label"],
                    "emoji": info["emoji"],
                    "color": info["color"],
                    "count": count,
                })

            return {
                "date_range": {"start": date_from, "end": date_to},
                "mood_distribution": mood_distribution_labeled,
                "daily_moods": daily_moods,
                "dominant_mood": {
                    "mood": dominant_mood,
                    "label": dominant_info["label"],
                    "emoji": dominant_info["emoji"],
                },
                "total_entries": sum(mood_distribution.values()),
            }
        except Exception as e:
            return {"error": f"获取心情统计失败: {e}"}

    # ── 历史上的今天 ─────────────────────────────────────────────────

    def get_throwback(self, years: Optional[int] = None) -> Dict[str, Any]:
        """历史上的今天"""
        if self._conn is None:
            return {"error": "数据库未初始化"}

        today = datetime.now()
        month = today.month
        day = today.day
        current_year = today.year

        try:
            cursor = self._conn.cursor()

            if years:
                # 查看指定年前
                target_year = current_year - years
                date_pattern = f"{target_year:04d}-{month:02d}-{day:02d}"
                cursor.execute("""
                    SELECT id, date, title, content, mood, tags, attachments, created_at, visibility
                    FROM diary_entries
                    WHERE deleted = 0 AND date = ?
                    ORDER BY created_at DESC
                """, (date_pattern,))
            else:
                # 查看所有年份的同月同日（排除今年）
                cursor.execute("""
                    SELECT id, date, title, content, mood, tags, attachments, created_at, visibility
                    FROM diary_entries
                    WHERE deleted = 0
                      AND strftime('%m', date) = ?
                      AND strftime('%d', date) = ?
                      AND strftime('%Y', date) != ?
                    ORDER BY date DESC, created_at DESC
                """, (f"{month:02d}", f"{day:02d}", str(current_year)))

            entries = []
            for row in cursor.fetchall():
                entry = self._row_to_entry(row)
                entries.append(entry)

            return {
                "entries": entries,
                "count": len(entries),
                "date": f"{month:02d}-{day:02d}",
                "message": f"找到了 {len(entries)} 篇历史上的今天喵~" if entries else "今天还没有回忆呢，快写点东西吧~",
            }
        except Exception as e:
            return {"error": f"获取历史上的今天失败: {e}"}

    # ── 日记删除 ─────────────────────────────────────────────────────

    def delete_entry(self, entry_id: str, permanent: bool = False) -> Dict[str, Any]:
        """删除日记"""
        if not entry_id:
            return {"error": "日记ID不能为空"}

        if self._conn is None:
            return {"error": "数据库未初始化"}

        try:
            cursor = self._conn.cursor()

            if permanent:
                # 永久删除
                cursor.execute("DELETE FROM diary_entries WHERE id = ?", (entry_id,))
            else:
                # 软删除
                cursor.execute("""
                    UPDATE diary_entries SET deleted = 1, updated_at = ? WHERE id = ?
                """, (self._now_iso(), entry_id))

            self._conn.commit()

            if cursor.rowcount == 0:
                return {"error": f"未找到日记: {entry_id}"}

            return {
                "success": True,
                "entry_id": entry_id,
                "permanent": permanent,
                "message": "日记已永久删除喵~" if permanent else "日记已放入回收站喵~",
            }
        except Exception as e:
            return {"error": f"删除日记失败: {e}"}
