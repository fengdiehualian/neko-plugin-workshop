import json
import threading
import time
from typing import Annotated

from pydantic import BaseModel, Field

from plugin.sdk.plugin import NekoPluginBase, Ok, Err, SdkError, neko_plugin, plugin_entry, lifecycle


class NoteAdd(BaseModel):
    text: str = Field(min_length=1, max_length=2000, description="笔记内容")
    tags: list[str] = Field(default_factory=list, description="标签列表")


@neko_plugin
class {{CLASS_NAME}}Plugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._file = self.data_path("notes.json")
        self._lock = threading.Lock()
        self._notes: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if not self._file.exists():
            return []
        try:
            data = json.loads(self._file.read_text("utf-8"))
        except Exception as exc:
            # 损坏文件不能静默当成空列表(下一次保存会把旧笔记全部清空):
            # 原样改名备份保留原始字节,再从空列表开始
            backup = self._file.with_name(f"notes.json.corrupt-{int(time.time())}")
            try:
                self._file.replace(backup)
            except OSError:
                pass
            self.logger.warning("notes.json 解析失败(%s),已备份为 %s", exc, backup.name)
            return []
        return data if isinstance(data, list) else []

    def _save(self) -> None:
        # 建目录 + 临时文件原子替换:失败由入口转成 Err(R3),不会写到一半损坏原文件
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._notes, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self._file)

    @lifecycle(id="startup")
    async def startup(self, **_):
        return Ok({"status": "ready", "notes": len(self._notes)})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "bye"})

    @plugin_entry(id="add", name="添加笔记", description="新增一条笔记")
    async def add(self, note: NoteAdd, **_):
        item = {"text": note.text, "tags": note.tags}
        with self._lock:
            self._notes.append(item)
            try:
                self._save()
            except Exception as exc:
                self._notes.pop()  # 落盘失败回滚,内存与磁盘保持一致
                return Err(SdkError(f"保存失败:{exc}"))
        return Ok({"total": len(self._notes)})

    @plugin_entry(
        id="search",
        name="搜索笔记",
        description="按关键词搜索笔记",
        llm_result_fields=["matches", "count"],
    )
    async def search(self, keyword: Annotated[str, "搜索关键词"], **_):
        if not keyword.strip():
            return Err(SdkError("关键词不能为空"))
        with self._lock:
            matches = [n for n in self._notes if keyword in n.get("text", "")]
        return Ok({"matches": matches[:20], "count": len(matches)})
