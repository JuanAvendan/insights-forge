"""Connector for MongoDB collections.

Documents are flattened into columns (``{"customer": {"email": ...}}`` becomes
``customer.email``) so nested data can be profiled like a table.

Safety: only ``find()`` is used (no aggregation pipelines, so no ``$out``/``$merge``
writes), filters that run JavaScript on the server (``$where``, ``$function``,
``$accumulator``) are rejected, connections time out after 10 seconds, and the
password is masked in every message. Use a user with the built-in ``read`` role.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from typing import Any

import duckdb
import polars as pl

from insights_forge.connectors.base import Connector, ConnectorError

DEFAULT_ROW_LIMIT = 1_000_000
TIMEOUT_MS = 10_000
FORBIDDEN_OPERATORS = frozenset({"$where", "$function", "$accumulator"})

_CREDENTIALS = re.compile(r"(mongodb(?:\+srv)?://[^:/@]+:)[^@]*@")
_DRIVER_HINT = "pip install 'insights-forge[mongo]'"


def safe_uri(uri: str) -> str:
    """The URI with its password masked, safe to show or log."""
    return _CREDENTIALS.sub(r"\1***@", uri)


def parse_filter(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    """A MongoDB filter from JSON text or a dict, rejecting server-side JavaScript."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            msg = f'The filter must be valid JSON, e.g. {{"region": "East"}} ({exc.msg}).'
            raise ConnectorError(msg) from exc
    else:
        value = raw
    if not isinstance(value, dict):
        msg = 'The filter must be a JSON object, e.g. {"region": "East"}.'
        raise ConnectorError(msg)
    _reject_javascript(value)
    return value


def _reject_javascript(value: Any) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            if key in FORBIDDEN_OPERATORS:
                msg = f"The {key} operator runs JavaScript on the server and is not allowed."
                raise ConnectorError(msg)
            _reject_javascript(inner)
    elif isinstance(value, list):
        for inner in value:
            _reject_javascript(inner)


def _scalar(value: Any) -> Any:
    """BSON values as plain Python values Polars understands."""
    from bson import Decimal128, ObjectId

    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Decimal128):
        return float(value.to_decimal())
    if isinstance(value, list):
        # Arrays don't fit one cell; keep them readable instead of dropping them.
        return json.dumps(value, default=str, ensure_ascii=False)
    if isinstance(value, bytes):
        return None
    if isinstance(value, dt.datetime | dt.date | str | int | float | bool) or value is None:
        return value
    return str(value)


def flatten(document: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Nested sub-documents become dotted column names; arrays become JSON text."""
    row: dict[str, Any] = {}
    for key, value in document.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            row.update(flatten(value, f"{name}."))
        else:
            row[name] = _scalar(value)
    return row


def _client(uri: str) -> Any:
    try:
        from pymongo import MongoClient
    except ModuleNotFoundError as exc:
        msg = f"Missing MongoDB driver: {_DRIVER_HINT}."
        raise ConnectorError(msg) from exc
    try:
        return MongoClient(
            uri,
            serverSelectionTimeoutMS=TIMEOUT_MS,
            connectTimeoutMS=TIMEOUT_MS,
            socketTimeoutMS=10 * TIMEOUT_MS,
        )
    except Exception as exc:  # invalid URI raises ConfigurationError / InvalidURI
        raise _error(exc, uri) from exc


def _error(exc: Exception, uri: str) -> ConnectorError:
    detail = str(exc).strip().splitlines()[0].split(", full error:")[0][:300]
    return ConnectorError(f"{safe_uri(uri)}: {safe_uri(detail)}")


def _database(client: Any, uri: str, name: str | None) -> Any:
    from pymongo.errors import ConfigurationError

    if name:
        return client.get_database(name)
    try:
        return client.get_default_database()
    except ConfigurationError as exc:
        msg = (
            f"{safe_uri(uri)}: no database given. Add it to the URI "
            "(mongodb://host:27017/shop) or pass it explicitly."
        )
        raise ConnectorError(msg) from exc


def list_collections(uri: str, database: str | None = None) -> list[str]:
    """Collections and views of the database, sorted by name."""
    from pymongo.errors import PyMongoError

    client = _client(uri)
    try:
        return sorted(_database(client, uri, database).list_collection_names())
    except PyMongoError as exc:
        raise _error(exc, uri) from exc
    finally:
        client.close()


@dataclass(frozen=True, slots=True)
class MongoConnector(Connector):
    """Read (part of) a MongoDB collection.

    Args:
        uri: Connection string, e.g. ``mongodb://analyst:analyst@localhost:57017/shop``.
        collection: Collection or view to read.
        database: Database name; defaults to the one in the URI.
        filter: Query filter as a dict or JSON text, e.g. ``{"region": "East"}``.
        limit: Maximum documents to read.
        include_id: Keep the ``_id`` column. Off by default: every document has a unique
            ``_id``, which would hide documents that are otherwise exact duplicates.
    """

    uri: str
    collection: str
    database: str | None = None
    filter: str | dict[str, Any] | None = field(default=None)
    limit: int | None = DEFAULT_ROW_LIMIT
    include_id: bool = False

    @property
    def name(self) -> str:
        return f"{self.collection} ({safe_uri(self.uri)})"

    def read(self, con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyRelation:
        from pymongo.errors import PyMongoError

        query = parse_filter(self.filter)
        client = _client(self.uri)
        try:
            db = _database(client, self.uri, self.database)
            available = sorted(db.list_collection_names())
            if self.collection not in available:
                shown = ", ".join(available[:10]) or "none"
                msg = f"Collection {self.collection!r} not found. Available: {shown}."
                raise ConnectorError(msg)
            projection = None if self.include_id else {"_id": 0}
            cursor = db[self.collection].find(query, projection, limit=self.limit or 0)
            rows = [flatten(doc) for doc in cursor]
        except PyMongoError as exc:
            raise _error(exc, self.uri) from exc
        finally:
            client.close()
        if not rows:
            msg = f"No documents in {self.collection!r} match the filter."
            raise ConnectorError(msg)
        # strict=False: a field holding ints in some documents and text in others
        # becomes text instead of failing.
        df = pl.DataFrame(rows, infer_schema_length=None, strict=False)
        return con.from_arrow(df.to_arrow())
