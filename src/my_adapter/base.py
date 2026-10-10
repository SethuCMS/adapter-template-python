"""The contract your adapter implements.

You write plain Python against these types. ``servicer.py`` turns them into the gRPC
protocol, so you never touch protobuf.

Rules every adapter must follow (the gateway relies on them):

1. **Never build queries by pasting text together.** Use your database driver's
   parameters/placeholders. Treat every field name and value as hostile data.
2. **Only use names you learned from introspection.** Refuse a field or collection you did
   not introspect with ``AdapterError("VALIDATION_FAILED", ...)``.
3. **Be honest in** ``capabilities()``. If the database cannot do something, say so; the
   gateway refuses the query. Never approximate silently (an approximate filter returns the
   wrong rows without anyone noticing).
4. **Never put the database's own error text into** ``AdapterError.message``. It can contain
   hostnames, user names and SQL. Use a short fixed sentence.
5. **Never log credentials.** They are passed to ``connect`` only and live in memory.
6. **Honour** ``read_only``. When it is true, ``create``/``update``/``delete`` must fail with
   ``READ_ONLY``, and where the database supports it, open a read-only session too.
7. **Return at most** ``query["page"]["limit"]`` **records,** and a ``next_cursor`` only when
   there are more. The gateway signs and verifies cursors; yours can be any string.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

Record = Dict[str, Any]

#: The codes the gateway understands.
ERROR_CODES = frozenset({
    "CONNECTION_FAILED", "AUTH_FAILED", "NOT_FOUND", "CONFLICT", "VALIDATION_FAILED",
    "CAPABILITY_UNSUPPORTED", "RATE_LIMITED", "TIMEOUT", "READ_ONLY", "INTERNAL",
})


class AdapterError(Exception):
    """Raise this for any failure you expect. Anything else becomes a generic INTERNAL error."""

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code {code!r}")
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass
class FieldInfo:
    native_name: str
    native_type: str
    #: One of: string, integer, number, boolean, datetime, json, bytes, ...
    canonical_type: str
    required: bool = False
    is_primary_key: bool = False
    #: True when the native type cannot be represented exactly.
    lossy: bool = False


@dataclass
class RelationInfo:
    name: str
    target: str
    kind: str  # "one" | "many"
    mapping: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectionInfo:
    native_name: str
    fields: List[FieldInfo] = field(default_factory=list)
    relations: List[RelationInfo] = field(default_factory=list)


@dataclass
class Capabilities:
    """What this database can do. The defaults are the most restrictive; turn things on only when true."""

    joins: bool = False
    transactions: str = "none"            # none | single-document | multi-document
    filter_or: bool = False
    filter_inequality: str = "none"       # full | single-field | none
    full_text: bool = False
    sort: str = "key-only"                # any-field | indexed-only | key-only
    offset_pagination: bool = False       # cursor pagination is mandatory regardless
    aggregations: bool = False
    realtime: bool = False
    schema: str = "inferred"              # enforced | inferred
    writes: bool = False


@dataclass
class FindResult:
    records: List[Record]
    next_cursor: str = ""                 # empty string means "last page"
    notes: List[str] = field(default_factory=list)


class Adapter(ABC):
    """One instance per connection. The server creates it with your factory function."""

    @abstractmethod
    def connect(self, config: Dict[str, Any], credentials: Dict[str, Any], read_only: bool) -> None:
        """Open the connection. Raise ``AdapterError("CONNECTION_FAILED" | "AUTH_FAILED", ...)`` on failure."""

    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    @abstractmethod
    def introspect(self) -> List[CollectionInfo]:
        """List the collections you can safely serve. Skip anything you cannot handle safely."""

    @abstractmethod
    def find(self, query: Dict[str, Any]) -> FindResult:
        """Run a portable query (see ``query-ast.schema.json``): collection, filter, sort, projection, page."""

    @abstractmethod
    def get(self, collection: str, id: str) -> Optional[Record]:
        """One record by primary key, or None."""

    @abstractmethod
    def create(self, collection: str, values: Record) -> Record: ...

    @abstractmethod
    def update(self, collection: str, id: str, values: Record) -> Record:
        """Raise ``NOT_FOUND`` when there is no such record."""

    @abstractmethod
    def delete(self, collection: str, id: str) -> None:
        """Raise ``NOT_FOUND`` when there is no such record."""

    def close(self) -> None:
        """Release the connection. Called once; must not raise."""
