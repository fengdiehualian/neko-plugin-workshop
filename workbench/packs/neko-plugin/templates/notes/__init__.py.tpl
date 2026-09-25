import json
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
        self._notes: list[dict] = self._load()

    def _load(self) -> list[dict]:
        try:
            return json.loads(self._file.read_text("utf-8"))
        except Exception:
            return []

    def _save(self) -> None:
        self._file.write_text(json.dumps(self._notes, ensure_ascii=False, indent=2), "utf-8")

    @lifecycle(id="startup")
    async def startup(self, **_):
        return Ok({"status": "ready", "notes": len(self._notes)})

    @plugin_entry(id="add", name="添加笔记", description="新增一条笔记")
    async def add(self, note: NoteAdd, **_):
        item = {"text": note.text, "tags": note.tags}
        self._notes.append(item)
        self._save()
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
        matches = [n for n in self._notes if keyword in n.get("text", "")]
        return Ok({"matches": matches[:20], "count": len(matches)})
