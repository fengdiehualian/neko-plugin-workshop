"""MongoDB 适配器（motor，文档模型，不映射关系表）。

MongoDB 没有 SQL。为保持 ``DatabaseAdapter`` 的统一签名，``sql`` 参数传
**JSON 字符串编码的文档操作**，``params`` 忽略：

    {"op": "insert_one",  "collection": "hot_knowledge", "doc": {...}}
    {"op": "insert_many", "collection": "...", "docs": [...]}
    {"op": "update_one",  "collection": "...", "filter": {...}, "update": {...},
                          "upsert": false}
    {"op": "update_many", ...}
    {"op": "replace_one", "collection": "...", "filter": {...}, "replacement": {...},
                          "upsert": false}
    {"op": "delete_one" | "delete_many", "collection": "...", "filter": {...}}
    {"op": "create_index", "collection": "...", "keys": {"field": 1},
                           "unique": false}
    {"op": "create_collection", "collection": "..."}

    fetch_all / fetch_one 的操作：
    {"op": "find",     "collection": "...", "filter": {...}, "sort": [["f", 1]],
                       "limit": 100, "skip": 0, "projection": {...}}
    {"op": "find_one", "collection": "...", "filter": {...}}
    {"op": "count",    "collection": "...", "filter": {...}}

``execute`` 返回受影响文档数；``fetch_*`` 返回文档（``_id`` 转字符串）。
事务：``transaction()`` 返回的 CM 在进入时开启 client session +
transaction（要求副本集部署；单机 mongod 会得到结构化错误）。
"""

from __future__ import annotations

import json
from typing import Any

from .base import AdapterError, DatabaseAdapter, TransactionHandle


def _decode_op(sql: str) -> dict:
    try:
        op = json.loads(sql)
    except json.JSONDecodeError as exc:
        raise AdapterError(
            "mongo adapter expects a JSON-encoded operation as sql, e.g. "
            '{"op":"find","collection":"t","filter":{}}'
        ) from exc
    if not isinstance(op, dict) or "op" not in op:
        raise AdapterError("mongo operation JSON must be an object with an 'op' key")
    # 入站：把此前出站转成字符串的 _id 转回 ObjectId，避免 update/delete 静默匹配 0 条
    # （bson 缺失/ABI 不匹配也会在这里抛错，同样转成结构化错误）
    try:
        for key in ("filter", "update", "replacement"):
            if isinstance(op.get(key), dict):
                op[key] = _encode_objectid(op[key])
    except AdapterError:
        raise
    except Exception as exc:
        raise AdapterError(f"mongo operation decode failed: {exc}") from exc
    return op


def _is_objectid_hex(value: str) -> bool:
    import re

    return bool(re.fullmatch(r"[0-9a-f]{24}", str(value)))


def _encode_objectid(doc: Any) -> Any:
    """递归把 ``_id``/``$eq``/``$in`` 位置的 24 位 hex 字符串转回 ObjectId。"""
    if isinstance(doc, list):
        return [_encode_objectid(d) for d in doc]
    if not isinstance(doc, dict):
        return doc
    out = {}
    for k, v in doc.items():
        if k in ("_id", "$eq") and isinstance(v, str) and _is_objectid_hex(v):
            out[k] = _objectid(v)
        elif k == "$in" and isinstance(v, list):
            out[k] = [
                _objectid(x) if isinstance(x, str) and _is_objectid_hex(x) else x for x in v
            ]
        elif isinstance(v, dict):
            out[k] = _encode_objectid(v)
        else:
            out[k] = v
    return out


def _objectid(value: str) -> Any:
    from bson import ObjectId

    return ObjectId(value)


def _require(op: dict, *keys: str) -> None:
    missing = [k for k in keys if k not in op]
    if missing:
        raise AdapterError(f"mongo op {op.get('op')!r} missing keys: {', '.join(missing)}")


def _clean_doc(doc: Any) -> Any:
    """递归把 ``_id``（ObjectId/datetime 等）转成可 JSON 化的值。"""
    if isinstance(doc, list):
        return [_clean_doc(d) for d in doc]
    if isinstance(doc, dict):
        out = {}
        for k, v in doc.items():
            if k == "_id":
                out[k] = str(v)
            elif hasattr(v, "rfc3339_string") or v.__class__.__name__ == "ObjectId":
                out[k] = str(v)
            else:
                out[k] = _clean_doc(v)
        return out
    return doc


class _MongoTransaction(TransactionHandle):
    def __init__(self, client) -> None:
        self._client = client
        self._session: Any = None

    async def _begin(self) -> None:
        from pymongo.errors import ClientSessionCannotStartTransaction, OperationFailure, PyMongoError

        try:
            self._session = await self._client.start_session()
            self._session.start_transaction()
        except (ClientSessionCannotStartTransaction, OperationFailure, PyMongoError) as exc:
            self._session = None
            raise AdapterError(
                "mongo transaction requires a replica-set deployment "
                f"(standalone mongod cannot run multi-document transactions): {exc}"
            ) from exc
        except Exception as exc:
            self._session = None
            raise AdapterError(f"mongo transaction start failed: {exc}") from exc

    async def _commit(self) -> None:
        if self._session is not None:
            await self._session.commit_transaction()
            await self._session.end_session()
            self._session = None

    async def _rollback(self) -> None:
        if self._session is not None:
            try:
                await self._session.abort_transaction()
            finally:
                await self._session.end_session()
                self._session = None


class MongoDBAdapter(DatabaseAdapter):
    """motor AsyncIOMotorClient（内置连接池）。

    config 键：
        uri: ``mongodb://host:27017``
        database: 库名（必填）
    """

    driver = "mongo"

    def __init__(self) -> None:
        self._client: Any = None
        self._db: Any = None

    async def connect(self, config: dict[str, Any]) -> None:
        uri = str(config.get("uri") or "").strip()
        dbname = str(config.get("database") or "").strip()
        if not uri or not dbname:
            raise AdapterError(
                "mongo adapter requires [database.mongo] uri and database"
            )
        try:
            # 依赖导入放在保护块内：motor/bson 缺失或 ABI 不匹配时也要结构化报错。
            import motor.motor_asyncio

            self._client = motor.motor_asyncio.AsyncIOMotorClient(
                uri, serverSelectionTimeoutMS=int(config.get("server_selection_ms", 5000))
            )
            self._db = self._client[dbname]
            # motor 是惰性连接：发一个轻量命令验证可达，避免把连接错误推迟到第一次操作。
            await self._client.admin.command("ping")
        except Exception as exc:
            self._client = None
            self._db = None
            raise AdapterError(f"mongo connect failed: {exc}") from exc

    def _require_db(self):
        if self._db is None:
            raise AdapterError("mongo adapter not connected")
        return self._db

    async def execute(self, sql: str, params: tuple = ()) -> int:
        op = _decode_op(sql)
        db = self._require_db()
        name = op.get("collection")
        if not name:
            raise AdapterError("mongo op requires 'collection'")
        coll = db[name]
        try:
            kind = op["op"]
            if kind == "insert_one":
                _require(op, "doc")
                res = await coll.insert_one(op["doc"])
                return 1 if res.inserted_id is not None else 0
            if kind == "insert_many":
                _require(op, "docs")
                res = await coll.insert_many(op["docs"])
                return len(res.inserted_ids)
            if kind in ("update_one", "update_many"):
                _require(op, "filter", "update")
                res = await getattr(coll, kind)(
                    op["filter"], op["update"], upsert=bool(op.get("upsert", False))
                )
                return (res.modified_count or 0) + (res.upserted_id is not None)
            if kind == "replace_one":
                _require(op, "filter", "replacement")
                res = await coll.replace_one(
                    op["filter"], op["replacement"], upsert=bool(op.get("upsert", False))
                )
                return (res.modified_count or 0) + (res.upserted_id is not None)
            if kind in ("delete_one", "delete_many"):
                _require(op, "filter")
                res = await getattr(coll, kind)(op["filter"])
                return res.deleted_count or 0
            if kind == "create_index":
                _require(op, "keys")
                idx = await coll.create_index(list(op["keys"].items()), unique=bool(op.get("unique", False)))
                return 1 if idx else 0
            if kind == "create_collection":
                await db.create_collection(name)
                return 0
            raise AdapterError(f"unsupported mongo execute op: {kind!r}")
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"mongo execute failed: {exc}") from exc

    async def _fetch(self, sql: str, one: bool) -> Any:
        op = _decode_op(sql)
        db = self._require_db()
        _require(op, "collection")
        coll = db[op["collection"]]
        try:
            kind = op["op"]
            if one and kind == "find_one":
                doc = await coll.find_one(op.get("filter", {}), op.get("projection"))
                return _clean_doc(doc) if doc is not None else None
            if kind == "find":
                cursor = coll.find(op.get("filter", {}), op.get("projection"))
                if op.get("sort"):
                    cursor = cursor.sort(op["sort"])
                if op.get("skip"):
                    cursor = cursor.skip(int(op["skip"]))
                if one:
                    doc = await cursor.to_list(length=1)
                    return _clean_doc(doc[0]) if doc else None
                docs = await cursor.to_list(length=int(op.get("limit", 100)))
                return _clean_doc(docs)
            if kind == "count":
                return await coll.count_documents(op.get("filter", {}))
            raise AdapterError(f"unsupported mongo fetch op: {kind!r}")
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"mongo fetch failed: {exc}") from exc

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        return await self._fetch(sql, one=False)  # type: ignore[return-value]

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        return await self._fetch(sql, one=True)  # type: ignore[return-value]

    async def transaction(self) -> TransactionHandle:
        self._require_db()
        return _MongoTransaction(self._client)

    async def close(self) -> None:
        client, self._client, self._db = self._client, None, None
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                raise AdapterError(f"mongo close failed: {exc}") from exc
