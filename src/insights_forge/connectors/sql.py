"""Connector for SQL databases: PostgreSQL, MySQL, SQLite (and other SQLAlchemy dialects).

Safety model (defense in depth, the tool must never modify a user's database):

1. **Read-only session.** PostgreSQL and MySQL transactions are set to READ ONLY;
   SQLite files are opened with ``mode=ro`` and ``PRAGMA query_only``.
2. **Statement validation.** Custom queries must be a single ``SELECT``/``WITH``
   statement, and they run wrapped in ``SELECT * FROM (...) LIMIT n``.
3. **Catalog-checked identifiers.** Tables are matched against the database
   catalog and quoted by SQLAlchemy, never interpolated as raw text.

Credentials never appear in messages: URLs are rendered with the password hidden.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
from sqlalchemy import Connection, Engine, create_engine, inspect, select, table, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, NoSuchModuleError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from insights_forge.connectors.base import Connector, ConnectorError

DEFAULT_ROW_LIMIT = 1_000_000
CONNECT_TIMEOUT_S = 10
SQLITE_EXTENSIONS = (".db", ".sqlite", ".sqlite3")

_LEADING_COMMENTS = re.compile(r"^\s*(?:--[^\n]*\n|/\*.*?\*/|\s)*", re.DOTALL)
_READ_STATEMENT = re.compile(r"^(select|with)\b", re.IGNORECASE)
_DRIVER_HINT = {
    "postgresql": "pip install 'insights-forge[postgres]'",
    "mysql": "pip install 'insights-forge[mysql]'",
}


def safe_url(url: str) -> str:
    """The URL with its password masked, safe to show or log."""
    try:
        parsed = make_url(url)
    except ArgumentError:
        return "<invalid database URL>"
    if parsed.get_backend_name() == "sqlite":
        return f"sqlite:///{parsed.database or ''}"  # a file path, not percent-encoded
    return parsed.render_as_string(hide_password=True)


def validate_query(query: str) -> str:
    """Return the query without a trailing ``;`` if it is a single read statement."""
    body = query.strip().rstrip(";").strip()
    stripped = _LEADING_COMMENTS.sub("", body)
    if not _READ_STATEMENT.match(stripped):
        msg = "Only read queries are allowed: the query must start with SELECT or WITH."
        raise ConnectorError(msg)
    if ";" in body:
        msg = "Only a single SQL statement is allowed (remove the ';' separators)."
        raise ConnectorError(msg)
    return body


def _error(exc: Exception, url: str) -> ConnectorError:
    """A short, credential-free error message."""
    detail = str(getattr(exc, "orig", None) or exc).strip().splitlines()[0]
    try:
        password = make_url(url).password
    except ArgumentError:
        password = None
    if password:
        detail = detail.replace(str(password), "***")
    return ConnectorError(f"{safe_url(url)}: {detail}")


def make_engine(url: str) -> Engine:
    """Create an engine whose connections are read-only where the database allows it."""
    try:
        parsed = make_url(url)
    except ArgumentError as exc:
        msg = "Invalid database URL. Example: postgresql+psycopg://user:pass@host:5432/db"
        raise ConnectorError(msg) from exc

    try:
        if parsed.get_backend_name() == "sqlite":
            database = parsed.database or ""
            if not database or database == ":memory:":
                msg = "SQLite needs a database file path, e.g. sqlite:///data/shop.db"
                raise ConnectorError(msg)
            path = Path(database)
            if not path.is_file():
                msg = f"SQLite file not found: {path}"
                raise ConnectorError(msg)
            uri = f"{path.resolve().as_uri()}?mode=ro".replace("file:///", "file:/", 1)
            return create_engine(
                "sqlite://",
                creator=lambda: sqlite3.connect(uri, uri=True, check_same_thread=False),
                poolclass=NullPool,
            )
        # Fail fast on a wrong host or port instead of waiting for the OS TCP timeout
        # (over two minutes on Windows).
        connect_args = (
            {"connect_timeout": CONNECT_TIMEOUT_S}
            if parsed.get_backend_name()
            in {
                "postgresql",
                "mysql",
                "mariadb",
            }
            else {}
        )
        return create_engine(parsed, poolclass=NullPool, connect_args=connect_args)
    except NoSuchModuleError as exc:
        msg = f"Unsupported database type in {safe_url(url)}."
        raise ConnectorError(msg) from exc
    except ModuleNotFoundError as exc:
        hint = _DRIVER_HINT.get(parsed.get_backend_name(), "install its Python driver")
        msg = f"Missing database driver '{exc.name}': {hint}."
        raise ConnectorError(msg) from exc


def decimals_to_float(df: pl.DataFrame) -> pl.DataFrame:
    """NUMERIC/DECIMAL columns as Float64: exact decimals add nothing to analysis and
    mixing ``Decimal`` with ``float`` in Python arithmetic raises errors."""
    return df.with_columns(pl.col(pl.Decimal).cast(pl.Float64))


_ISO_FORMATS: tuple[tuple[str, str], ...] = (
    ("date", "%Y-%m-%d"),
    ("datetime", "%Y-%m-%d %H:%M:%S%.f"),
    ("datetime", "%Y-%m-%dT%H:%M:%S%.f"),
)


def parse_text_dates(df: pl.DataFrame) -> pl.DataFrame:
    """SQLite has no date type: convert text columns whose values are all ISO dates or
    timestamps so trends and time axes work. Only unambiguous ISO formats are tried
    (``01/02/2025`` could be January or February)."""
    converted = []
    for name in df.columns:
        column = df[name]
        if column.dtype != pl.String or column.null_count() == column.len():
            continue
        for kind, fmt in _ISO_FORMATS:
            parse = column.str.to_date if kind == "date" else column.str.to_datetime
            parsed = parse(format=fmt, strict=False)
            if parsed.null_count() == column.null_count():
                converted.append(parsed.alias(name))
                break
    return df.with_columns(converted) if converted else df


def _set_read_only(conn: Connection) -> None:
    match conn.dialect.name:
        case "postgresql":
            conn.execute(text("SET TRANSACTION READ ONLY"))
        case "mysql" | "mariadb":
            conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
        case "sqlite":
            conn.execute(text("PRAGMA query_only = ON"))
        case _:  # other dialects rely on statement validation only
            pass


def _relations(conn: Connection, schema: str | None) -> list[str]:
    insp = inspect(conn)
    return sorted({*insp.get_table_names(schema=schema), *insp.get_view_names(schema=schema)})


def list_tables(url: str, schema: str | None = None) -> list[str]:
    """Tables and views visible to the connection, sorted by name."""
    engine = make_engine(url)
    try:
        with engine.connect() as conn:
            return _relations(conn, schema)
    except SQLAlchemyError as exc:
        raise _error(exc, url) from exc
    finally:
        engine.dispose()


@dataclass(frozen=True, slots=True)
class SQLConnector(Connector):
    """Read a table/view or the result of a SELECT query from a SQL database.

    Args:
        url: SQLAlchemy URL, e.g. ``postgresql+psycopg://user:pass@localhost:5432/shop``,
            ``mysql+pymysql://user:pass@localhost:3306/shop`` or ``sqlite:///shop.db``.
        table: Table or view to read. Exactly one of ``table`` and ``query`` is required.
        query: A single ``SELECT``/``WITH`` statement.
        schema: Schema of ``table`` (PostgreSQL), defaults to the connection's schema.
        limit: Maximum rows to read, protects memory on very large tables.
    """

    url: str
    table: str | None = None
    query: str | None = None
    schema: str | None = None
    limit: int | None = DEFAULT_ROW_LIMIT

    def __post_init__(self) -> None:
        if (self.table is None) == (self.query is None):
            msg = "Give exactly one of `table` or `query`."
            raise ValueError(msg)

    @classmethod
    def sqlite(cls, path: Path, **kwargs: Any) -> SQLConnector:
        return cls(url=f"sqlite:///{path.as_posix()}", **kwargs)

    @property
    def name(self) -> str:
        """Display name such as ``sales (postgresql://app@localhost:5432/shop)``."""
        what = self.table if self.table is not None else "query"
        return f"{what} ({safe_url(self.url)})"

    def read(self, con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyRelation:
        engine = make_engine(self.url)
        try:
            with engine.connect() as conn:
                _set_read_only(conn)
                df = pl.read_database(self._statement(conn), conn, infer_schema_length=None)
                if conn.dialect.name == "sqlite":
                    df = parse_text_dates(df)
        except SQLAlchemyError as exc:
            raise _error(exc, self.url) from exc
        finally:
            engine.dispose()
        return con.from_arrow(decimals_to_float(df).to_arrow())

    def _statement(self, conn: Connection) -> Any:
        if self.query is not None:
            body = validate_query(self.query)
            limit = f" LIMIT {int(self.limit)}" if self.limit else ""
            return text(f"SELECT * FROM ({body}) AS insights_forge_query{limit}")  # noqa: S608

        assert self.table is not None  # noqa: S101 - guaranteed by __post_init__
        available = _relations(conn, self.schema)
        if self.table not in available:
            where = f" in schema {self.schema!r}" if self.schema else ""
            shown = ", ".join(available[:10]) or "none"
            msg = f"Table {self.table!r} not found{where}. Available: {shown}."
            raise ConnectorError(msg)
        stmt = select(text("*")).select_from(table(self.table, schema=self.schema))
        return stmt.limit(self.limit) if self.limit else stmt
