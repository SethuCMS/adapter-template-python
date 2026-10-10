"""A complete, working adapter that keeps records in memory.

Read this first, then copy it and replace the storage with your database driver.
It shows the shape of every method and the safety checks that go with them.
"""
from __future__ import annotations

import copy
import itertools
from typing import Any, Dict, List, Optional

from .base import (
    Adapter, AdapterError, Capabilities, CollectionInfo, FieldInfo, FindResult, Record,
)

#: What this example serves. A real adapter reads this from the database (introspection).
_SCHEMA = CollectionInfo(
    native_name="items",
    fields=[
        FieldInfo("id", "int", "integer", required=True, is_primary_key=True),
        FieldInfo("title", "text", "string", required=True),
        FieldInfo("score", "int", "integer"),
        FieldInfo("status", "text", "string"),
    ],
)
_FIELDS = {f.native_name: f for f in _SCHEMA.fields}


class InMemoryAdapter(Adapter):
    def __init__(self) -> None:
        self._rows: Dict[int, Record] = {}
        self._ids = itertools.count(1)
        self._read_only = True
        self._open = False

    # -- lifecycle -----------------------------------------------------------------
    def connect(self, config: Dict[str, Any], credentials: Dict[str, Any], read_only: bool) -> None:
        # A real adapter opens a pooled connection here, using `credentials`, and maps driver
        # errors to AdapterError("CONNECTION_FAILED") / AdapterError("AUTH_FAILED").
        # Never include the driver's message in the error.
        self._read_only = read_only
        self._open = True

    def close(self) -> None:
        self._open = False

    def capabilities(self) -> Capabilities:
        return Capabilities(
            filter_or=True, filter_inequality="full", sort="any-field",
            offset_pagination=True, schema="enforced", writes=not self._read_only,
        )

    def introspect(self) -> List[CollectionInfo]:
        return [copy.deepcopy(_SCHEMA)]

    # -- reads ---------------------------------------------------------------------
    def find(self, query: Dict[str, Any]) -> FindResult:
        self._require_collection(query.get("collection"))
        page = query.get("page") or {}
        limit = int(page.get("limit") or 50)
        offset = _read_cursor(page.get("cursor"))

        _check_filter(query.get("filter"))   # validate up front, even when there are no rows to test it on
        rows = sorted((r for r in self._rows.values() if _matches(query.get("filter"), r)), key=lambda r: r["id"])
        for spec in reversed(query.get("sort") or []):          # last sort key first => stable multi-key sort
            name = self._require_field(spec.get("field"))
            rows.sort(key=lambda r, n=name: _sort_key(r.get(n)), reverse=spec.get("dir") == "desc")

        window = rows[offset: offset + limit]
        more = offset + limit < len(rows)
        projection = query.get("projection")
        if projection:
            for name in projection:
                self._require_field(name)
        return FindResult(
            records=[_project(r, projection) for r in window],
            next_cursor=str(offset + limit) if more else "",
        )

    def get(self, collection: str, id: str) -> Optional[Record]:
        self._require_collection(collection)
        row = self._rows.get(_to_id(id))
        return copy.deepcopy(row) if row else None

    # -- writes --------------------------------------------------------------------
    def create(self, collection: str, values: Record) -> Record:
        self._require_writable(collection)
        record = self._validated(values, creating=True)
        record["id"] = next(self._ids)
        self._rows[record["id"]] = record
        return copy.deepcopy(record)

    def update(self, collection: str, id: str, values: Record) -> Record:
        self._require_writable(collection)
        key = _to_id(id)
        if key not in self._rows:
            raise AdapterError("NOT_FOUND", "No such record")
        self._rows[key].update(self._validated(values, creating=False))
        return copy.deepcopy(self._rows[key])

    def delete(self, collection: str, id: str) -> None:
        self._require_writable(collection)
        if self._rows.pop(_to_id(id), None) is None:
            raise AdapterError("NOT_FOUND", "No such record")

    # -- checks (every adapter needs the equivalents) -------------------------------
    def _require_collection(self, name: Any) -> None:
        if not self._open:
            raise AdapterError("CONNECTION_FAILED", "The connection is closed")
        if name != _SCHEMA.native_name:
            raise AdapterError("VALIDATION_FAILED", "Unknown collection")

    def _require_field(self, name: Any) -> str:
        if not isinstance(name, str) or name not in _FIELDS:
            raise AdapterError("VALIDATION_FAILED", "Unknown field")
        return name

    def _require_writable(self, collection: str) -> None:
        self._require_collection(collection)
        if self._read_only:
            raise AdapterError("READ_ONLY", "This connection is read-only")

    def _validated(self, values: Record, *, creating: bool) -> Record:
        clean: Record = {}
        for name, value in values.items():
            self._require_field(name)
            if name == "id":
                raise AdapterError("VALIDATION_FAILED", "The id is generated and cannot be set")
            clean[name] = value
        if creating and not clean.get("title"):
            raise AdapterError("VALIDATION_FAILED", "title is required")
        if "title" in clean and (not isinstance(clean["title"], str) or len(clean["title"]) > 200):
            raise AdapterError("VALIDATION_FAILED", "title must be text of at most 200 characters")
        if "score" in clean and clean["score"] is not None and (
            not isinstance(clean["score"], int) or isinstance(clean["score"], bool)
        ):
            raise AdapterError("VALIDATION_FAILED", "score must be a whole number")
        if creating:
            clean.setdefault("score", None)
            clean.setdefault("status", None)
        return clean


# -- helpers ------------------------------------------------------------------------
def _to_id(raw: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise AdapterError("NOT_FOUND", "No such record") from None   # a malformed id is simply not found


def _read_cursor(cursor: Any) -> int:
    if not cursor:
        return 0
    try:
        value = int(cursor)
    except (TypeError, ValueError):
        raise AdapterError("VALIDATION_FAILED", "Invalid cursor") from None
    if value < 0:
        raise AdapterError("VALIDATION_FAILED", "Invalid cursor")
    return value


def _sort_key(value: Any):
    # None sorts first; values of different types never get compared with each other.
    return (value is not None, type(value).__name__, value if value is not None else 0)


def _project(row: Record, projection: Optional[List[str]]) -> Record:
    return {k: v for k, v in row.items() if not projection or k in projection}


_OPERATORS = {"eq", "ne", "lt", "lte", "gt", "gte", "in", "nin", "contains", "startsWith", "exists"}


def _check_filter(flt: Optional[Dict[str, Any]]) -> None:
    """Refuse unknown fields and operators before touching any data."""
    if not flt:
        return
    for key in ("and", "or"):
        if key in flt:
            for child in flt[key]:
                _check_filter(child)
            return
    if "not" in flt:
        _check_filter(flt["not"])
        return
    if flt.get("field") not in _FIELDS:
        raise AdapterError("VALIDATION_FAILED", "Unknown field")
    if flt.get("op") not in _OPERATORS:
        raise AdapterError("CAPABILITY_UNSUPPORTED", "Unsupported operator")


def _matches(flt: Optional[Dict[str, Any]], row: Record) -> bool:
    if not flt:
        return True
    if "and" in flt:
        return all(_matches(f, row) for f in flt["and"])
    if "or" in flt:
        return any(_matches(f, row) for f in flt["or"])
    if "not" in flt:
        return not _matches(flt["not"], row)
    field_name, op, expected = flt.get("field"), flt.get("op"), flt.get("value")
    if field_name not in _FIELDS:
        raise AdapterError("VALIDATION_FAILED", "Unknown field")
    actual = row.get(field_name)
    if op == "exists":
        return (actual is not None) == bool(expected)
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return actual in (expected or [])
    if op == "nin":
        return actual not in (expected or [])
    if op in ("lt", "lte", "gt", "gte"):
        if actual is None or expected is None or isinstance(actual, bool) or type(actual) is not type(expected) and not (
            isinstance(actual, (int, float)) and isinstance(expected, (int, float))
        ):
            return False
        return {"lt": actual < expected, "lte": actual <= expected, "gt": actual > expected, "gte": actual >= expected}[op]
    if op in ("contains", "startsWith"):
        if not isinstance(actual, str) or not isinstance(expected, str):
            return False
        return expected.lower() in actual.lower() if op == "contains" else actual.lower().startswith(expected.lower())
    raise AdapterError("CAPABILITY_UNSUPPORTED", "Unsupported operator")
