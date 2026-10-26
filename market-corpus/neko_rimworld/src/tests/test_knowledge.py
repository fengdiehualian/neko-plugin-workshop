from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue

import pytest
from plugin.plugins.neko_rimworld import knowledge
from plugin.plugins.neko_rimworld.knowledge import IndexedKnowledgeView, KnowledgeLibrary

from .knowledge_fixtures import publish


def make_entry(name: str, label: str = "steel", *, description: str = "A definition") -> dict:
    return {
        "key": f"Verse.ThingDef/{name}",
        "type": "Verse.ThingDef",
        "def_name": name,
        "label": label,
        "description": description,
        "source_mod": "核心",
        "package_id": "ludeon.rimworld",
        "facts": [{"name": "stackLimit", "value": "75", "type": "System.Int32"}],
        "related": [],
        "truncated": False,
    }


def write_source(directory: Path, entries: list[dict], *, metadata: dict | None = None) -> Path:
    root = {
        "entries": entries,
        "format": "neko.defs.v1",
        "game_version": "1.6",
        "language": "中文",
        "generated_at": "2026-09-01T00:00:00Z",
        "complete": True,
        "mods": [{"package_id": "ludeon.rimworld", "name": "核心"}],
        "issues": [],
    }
    if metadata:
        root.update(metadata)
    return publish(directory, root)


def replace_source(directory: Path, entries: list[dict], *, metadata: dict | None = None) -> None:
    write_source(directory, entries, metadata=metadata)


def test_ready_view_preserves_metadata_and_unicode(tmp_path):
    source_entry = make_entry("钢铁", "钢铁", description="用于建造的材料")
    write_source(tmp_path, [source_entry], metadata={"warnings": ["缺少来源"]})

    library = KnowledgeLibrary(tmp_path)
    assert library.refresh() is True
    view = library.open_view()
    assert view is not None
    assert view.metadata["language"] == "中文"
    assert view.metadata["warnings"] == ["缺少来源"]
    assert view.read_entry(source_entry["key"]) == source_entry
    assert view.search_rows() == [
        {
            "key": source_entry["key"],
            "names": ["钢铁"],
            "description": "用于建造的材料",
        }
    ]


def test_duplicate_directory_keys_and_corrupt_ready_are_rejected(tmp_path):
    duplicate = make_entry("Steel")
    write_source(tmp_path, [duplicate, dict(duplicate, label="duplicate")])
    library = KnowledgeLibrary(tmp_path)
    assert library.refresh() is False
    assert library.open_view() is None

    (tmp_path / "ready.json").write_text('{"entries": [', encoding="utf-8")
    assert library.refresh() is False
    assert library.open_view() is None


def test_failed_refresh_unpublishes_current_view_while_pinned_views_survive(tmp_path):
    old_entry = make_entry("Old", "旧")
    new_entry = make_entry("New", "新")
    write_source(tmp_path, [old_entry])
    library = KnowledgeLibrary(tmp_path)
    assert library.refresh() is True
    old_view = library.open_view()
    assert old_view is not None

    replace_source(tmp_path, [new_entry])
    assert library.refresh() is True
    new_view = library.open_view()
    assert new_view is not None
    assert old_view.metadata["sha256"] != new_view.metadata["sha256"]
    assert old_view.read_entry(old_entry["key"]) == old_entry
    assert old_view.read_entry(new_entry["key"]) is None
    assert new_view.read_entry(new_entry["key"]) == new_entry
    assert new_view.read_entry(old_entry["key"]) is None

    replace_source(tmp_path, [make_entry("Broken")])
    (tmp_path / "ready.json").write_text("not json", encoding="utf-8")
    assert library.refresh() is False
    assert library.open_view() is None
    assert new_view.read_entry(new_entry["key"]) == new_entry






def test_concurrent_views_read_selected_payload_records(tmp_path):
    entries = [make_entry("Steel", "钢铁"), make_entry("Gold", "黄金")]
    write_source(tmp_path, entries)
    library = KnowledgeLibrary(tmp_path)
    assert library.refresh() is True
    views = [library.open_view() for _ in range(8)]
    assert all(view is not None for view in views)

    def read(view):
        return view.read_entry("Verse.ThingDef/Steel"), view.search_rows()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(read, views))
    assert all(result[0] == entries[0] for result in results)
    assert all({row["key"] for row in result[1]} == {entry["key"] for entry in entries} for result in results)


def test_background_refresh_attaches_ready_created_after_first_missing_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "REFRESH_INTERVAL_SECONDS", 0.02)
    library = KnowledgeLibrary(tmp_path)
    attempts = Queue()
    refresh = library.refresh

    def observed_refresh():
        result = refresh()
        attempts.put((result, threading.current_thread()))
        return result

    monkeypatch.setattr(library, "refresh", observed_refresh)
    worker = None
    try:
        library.request_refresh()
        succeeded, worker = attempts.get(timeout=2)
        assert succeeded is False
        assert library.open_view() is None
        assert worker.daemon

        source_entry = make_entry("Later", "后来出现")
        # Publish a ready directory atomically, without another refresh request.
        replace_source(tmp_path, [source_entry])
        for _ in range(20):
            succeeded, running_worker = attempts.get(timeout=2)
            assert running_worker is worker
            if succeeded:
                break
        assert succeeded is True
        view = library.open_view()
        assert view is not None
        assert view.read_entry(source_entry["key"]) == source_entry
    finally:
        library.close()
        if worker is not None:
            worker.join(timeout=2)
            assert not worker.is_alive()


def test_repeated_requests_start_one_daemon_and_close_wakes_interval_wait(tmp_path, monkeypatch):
    # A long interval proves shutdown wakes the worker rather than waiting for it.
    monkeypatch.setattr(knowledge, "REFRESH_INTERVAL_SECONDS", 60)
    library = KnowledgeLibrary(tmp_path)
    finished_refresh = threading.Event()
    workers = []
    start = threading.Thread.start
    refresh = library.refresh

    def observed_start(thread):
        workers.append(thread)
        return start(thread)

    def observed_refresh():
        result = refresh()
        finished_refresh.set()
        return result

    monkeypatch.setattr(threading.Thread, "start", observed_start)
    monkeypatch.setattr(library, "refresh", observed_refresh)
    try:
        library.request_refresh()
        assert finished_refresh.wait(timeout=2)
        for _ in range(20):
            library.request_refresh()
        assert len(workers) == 1
        assert workers[0].daemon
        assert workers[0].is_alive()
        library.close()
        workers[0].join(timeout=2)
        assert not workers[0].is_alive()
        library.request_refresh()
        assert len(workers) == 1
        assert library.refresh() is False
    finally:
        library.close()
        for worker in workers:
            worker.join(timeout=2)
            assert not worker.is_alive()


def test_concurrent_searches_share_lightweight_directory_for_one_generation(tmp_path):
    source_entry = make_entry("Steel", "钢铁")
    source_entry["facts"] = [{"name": "custom", "value": "large value" * 1000}]
    write_source(tmp_path, [source_entry])
    library = KnowledgeLibrary(tmp_path)
    assert library.refresh()
    old_view = library.open_view()
    assert old_view is not None
    start = threading.Barrier(8)

    def search(_):
        start.wait(timeout=2)
        view = library.open_view()
        return view.search_rows()

    with ThreadPoolExecutor(max_workers=8) as executor:
        directories = list(executor.map(search, range(8)))
    assert all(rows is directories[0] for rows in directories)
    assert directories[0] == [
        {
            "key": source_entry["key"],
            "names": ["steel", "钢铁"],
            "description": "a definition",
        }
    ]
    assert old_view.read_entry(source_entry["key"]) == source_entry

    replacement = make_entry("Gold", "黄金")
    replace_source(tmp_path, [replacement])
    assert library.refresh()
    new_view = library.open_view()
    assert new_view is not None
    assert [row["key"] for row in new_view.search_rows()] == [replacement["key"]]
    assert old_view.search_rows() is directories[0]
    assert old_view.read_entry(source_entry["key"]) == source_entry
    library.close()


@pytest.mark.parametrize("content", ["", "[]", '{"entries": []}', '{"entries": [], "format": "future"}'])
def test_invalid_source_has_no_view(tmp_path, content):
    (tmp_path / "ready.json").write_text(content, encoding="utf-8")
    library = KnowledgeLibrary(tmp_path)
    assert library.refresh() is False
    assert library.open_view() is None


@pytest.mark.parametrize("kwargs", [{"allow_legacy_import": False}, {"allow_legacy_import": True}, {"cache_directory": None}])
def test_removed_import_and_cache_parameters_are_rejected(tmp_path, kwargs):
    with pytest.raises(TypeError):
        KnowledgeLibrary(tmp_path, **kwargs)


def test_legacy_payload_and_sqlite_cache_cannot_restore_a_view(tmp_path):
    (tmp_path / "current.json").write_text("{}", encoding="utf-8")
    cache = tmp_path / ".index"
    cache.mkdir()
    (cache / "current.json").write_text('{"database":"old.sqlite3","index_version":2}', encoding="utf-8")
    library = KnowledgeLibrary(tmp_path)
    assert not library.refresh()
    assert library.open_view() is None
    assert library.last_error == "knowledge_index_not_ready"
    assert not list(tmp_path.rglob("*.sqlite3"))


def test_path_constructor_is_not_a_wiki_import_api(tmp_path):
    from plugin.plugins.neko_rimworld.agent.reference.wiki_tools import WikiStore
    with pytest.raises(TypeError, match="IndexedKnowledgeView"):
        WikiStore(tmp_path)


def test_open_view_rejects_replaced_or_missing_ready_before_background_refresh(tmp_path):
    first = make_entry("Steel")
    write_source(tmp_path, [first])
    library = KnowledgeLibrary(tmp_path)
    pinned = library.open_view()
    assert isinstance(pinned, IndexedKnowledgeView)
    (tmp_path / "ready.json").write_text("broken", encoding="utf-8")
    assert library.open_view() is None
    assert pinned.read_entry(first["key"]) == first
    assert not library.refresh()
    assert library.open_view() is None
    write_source(tmp_path, [make_entry("Gold")])
    assert library.refresh()
    (tmp_path / "ready.json").unlink()
    assert library.open_view() is None
    assert pinned.read_entry(first["key"]) == first
