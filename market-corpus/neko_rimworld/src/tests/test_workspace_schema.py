"""Current ordinary history storage admits no migrations and preserves originals."""

import hashlib
import sqlite3
from contextlib import closing

import pytest

from ..workspace.store import WorkspaceStore
from ..workspace.views import WorkspaceViews


def snapshot(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name").fetchall()
        rows = {
            name: db.execute(f'SELECT * FROM "{name}"').fetchall()
            for kind, name, _, _ in schema if kind == "table"
        }
    return hashlib.sha256(path.read_bytes()).hexdigest(), schema, rows


@pytest.mark.parametrize("mutation", ["DROP TRIGGER sealed_observation", "DROP INDEX workspace_turn_sequence", "ALTER TABLE workspaces RENAME COLUMN work_state TO retired_state"])
def test_missing_owned_schema_is_rejected_without_repair(tmp_path, mutation):
    path = tmp_path / "unsupported.sqlite"
    store = WorkspaceStore(path)
    store.close()
    with sqlite3.connect(path) as db:
        db.execute(mutation)
    original = snapshot(path)
    with pytest.raises(ValueError, match="Unsupported workspace schema"):
        WorkspaceStore(path)
    with pytest.raises(ValueError, match="Unsupported workspace schema"):
        WorkspaceViews(path).read("list")
    assert snapshot(path) == original


def test_old_foundation_database_and_non_sqlite_file_are_not_rewritten(tmp_path):
    path = tmp_path / "foundation.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE workspaces(id TEXT PRIMARY KEY,owner TEXT NOT NULL,focus TEXT NOT NULL)")
        db.execute("INSERT INTO workspaces VALUES ('old','caller','original focus')")
    original = snapshot(path)
    with pytest.raises(ValueError, match="Unsupported workspace schema"):
        WorkspaceStore(path)
    assert snapshot(path) == original
    invalid = tmp_path / "unknown.sqlite"
    invalid.write_bytes(b"original non-database bytes")
    with pytest.raises(ValueError, match="Unsupported workspace database"):
        WorkspaceStore(invalid)
    assert invalid.read_bytes() == b"original non-database bytes"


