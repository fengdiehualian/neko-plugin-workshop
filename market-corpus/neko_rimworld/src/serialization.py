"""Canonical JSON encoding and text digests for persisted and model-facing data."""

import hashlib
import json


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
