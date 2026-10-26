"""Synthetic publisher-format fixtures, independent of the production reader."""
import hashlib
import json
import uuid
from pathlib import Path


def publish(directory: Path, document: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    metadata = {key: value for key, value in document.items() if key != "entries"}
    entries = document["entries"]
    chunks = [json.dumps(entry, ensure_ascii=False).encode("utf-8") for entry in entries]
    prefix = b'{"entries":['
    body = prefix + b','.join(chunks) + b'],' + json.dumps(metadata, ensure_ascii=False).encode("utf-8")[1:]
    payload = directory / ("snapshot-" + uuid.uuid4().hex + ".json")
    payload.write_bytes(body)
    rows = []
    offset = len(prefix)
    for entry, chunk in zip(entries, chunks):
        names = [entry.get("def_name", ""), entry.get("label", "")]
        descriptions = [entry.get("description", "")]
        for fact in entry.get("facts", []) or []:
            if not isinstance(fact, dict) or not isinstance(fact.get("name"), str) or not isinstance(fact.get("value"), str):
                continue
            leaf = fact["name"].rsplit(".", 1)[-1].casefold()
            if leaf == "label":
                names.append(fact["value"])
            elif leaf == "description":
                descriptions.append(fact["value"])
        rows.append({"key": entry.get("key"), "names": list(dict.fromkeys(name for name in names if name)),
                     "description": " ".join(text for text in descriptions if text), "offset": offset,
                     "length": len(chunk), "sha256": hashlib.sha256(chunk).hexdigest()})
        offset += len(chunk) + 1
    checksum = hashlib.sha256(body).hexdigest()
    ready = {"format": "neko.defs.index.v1", "data_file": payload.name, "data_bytes": len(body),
             "data_sha256": checksum, "snapshot": {**metadata, "sha256": checksum}, "entries": rows}
    temporary = directory / ("ready-" + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(ready, ensure_ascii=False), encoding="utf-8")
    temporary.replace(directory / "ready.json")
    return payload
