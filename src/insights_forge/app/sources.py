"""Sidebar data-source pickers: files (incl. SQLite), the sample, and database servers.

Each picker returns a *loader* (a zero-argument function producing the
:class:`Analysis`) plus a cache key that identifies the source, or ``None`` when
nothing is selected yet.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import streamlit as st
from sqlalchemy.engine import URL

from insights_forge.connectors import (
    SQLITE_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    ConnectorError,
    SQLConnector,
    list_tables,
    safe_url,
)
from insights_forge.connectors.sql import DEFAULT_ROW_LIMIT
from insights_forge.pipeline import Analysis, analyze, analyze_file

SAMPLE_FILE = Path(__file__).resolve().parents[3] / "data" / "sample" / "sales.csv"
DB_URL_ENV = "INSIGHTS_FORGE_DB_URL"
DB_ENGINES = {"PostgreSQL": ("postgresql+psycopg", 5432), "MySQL": ("mysql+pymysql", 3306)}
UPLOAD_TYPES = [*SUPPORTED_EXTENSIONS, *(ext.lstrip(".") for ext in SQLITE_EXTENSIONS)]

Source = tuple[Callable[[], Analysis], str]


# --------------------------------------------------------------------------- cached loaders


@st.cache_data(show_spinner="Analyzing data...", max_entries=5)
def analyze_upload(name: str, data: bytes) -> Analysis:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(name).name
        path.write_bytes(data)
        return analyze_file(path)


@st.cache_data(show_spinner="Analyzing sample data...")
def analyze_sample() -> Analysis:
    return analyze_file(SAMPLE_FILE)


@st.cache_data(show_spinner="Reading tables...", max_entries=5)
def sqlite_tables(name: str, data: bytes) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(name).name
        path.write_bytes(data)
        return list_tables(f"sqlite:///{path.as_posix()}")


@st.cache_data(show_spinner="Analyzing table...", max_entries=5)
def analyze_sqlite_upload(name: str, data: bytes, table: str) -> Analysis:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(name).name
        path.write_bytes(data)
        return analyze(SQLConnector.sqlite(path, table=table), f"{table} ({name})")


@st.cache_data(show_spinner="Querying the database...", max_entries=5, ttl=600)
def analyze_database(url: str, table: str | None, query: str | None, limit: int) -> Analysis:
    connector = SQLConnector(url, table=table, query=query, limit=limit)
    return analyze(connector, connector.name)


def _digest(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()


# --------------------------------------------------------------------------- pickers


def file_source() -> Source | None:
    uploaded = st.file_uploader(
        "Upload a file",
        type=UPLOAD_TYPES,
        on_change=lambda: st.session_state.update(source="upload"),
        help="CSV, Excel, Parquet, JSON or a SQLite database file.",
    )
    if st.button("Try with sample data", disabled=not SAMPLE_FILE.exists()):
        st.session_state.source = "sample"

    if st.session_state.get("source") == "sample":
        return analyze_sample, "sample"
    if uploaded is None:
        return None

    name, data = uploaded.name, uploaded.getvalue()
    if Path(name).suffix.lower() not in SQLITE_EXTENSIONS:
        return (lambda: analyze_upload(name, data)), _digest(name, _digest(data))

    try:
        tables = sqlite_tables(name, data)
    except ConnectorError as exc:
        st.error(f"Could not open the database: {exc}")
        return None
    if not tables:
        st.warning("This SQLite file has no tables.")
        return None
    table = st.selectbox("Table", tables, key="sqlite_table")
    return (lambda: analyze_sqlite_upload(name, data, table)), _digest(name, _digest(data), table)


def _connect_form() -> None:
    env_url = os.environ.get(DB_URL_ENV)
    with st.form("db_connect"):
        engine = st.selectbox("Engine", list(DB_ENGINES))
        host = st.text_input("Host", "localhost")
        port = st.text_input("Port", placeholder="default for the engine")
        database = st.text_input("Database")
        user = st.text_input("User")
        password = st.text_input("Password", type="password")
        use_env = st.checkbox(
            f"Use the {DB_URL_ENV} variable instead",
            value=bool(env_url),
            disabled=not env_url,
            help="Keeps credentials out of the browser: set it before starting the app.",
        )
        submitted = st.form_submit_button("Connect", type="primary")
    if not submitted:
        return

    if use_env and env_url:
        url = env_url
    else:
        driver, default_port = DB_ENGINES[engine]
        if not port.strip().isdigit() and port.strip():
            st.error("Port must be a number.")
            return
        url = URL.create(
            driver,
            username=user or None,
            password=password or None,
            host=host or None,
            port=int(port) if port.strip() else default_port,
            database=database or None,
        ).render_as_string(hide_password=False)
    try:
        tables = list_tables(url)
    except ConnectorError as exc:
        st.session_state.pop("db", None)
        st.error(f"Could not connect: {exc}")
        return
    st.session_state.db = {"url": url, "tables": tables}


def database_source() -> Source | None:
    _connect_form()
    db = st.session_state.get("db")
    if not db:
        st.caption(
            "Try it locally: `docker compose up -d --wait` starts PostgreSQL (port 55432) and "
            "MySQL (port 53306) with the sample data. Database `shop`, user and password "
            "`analyst`."
        )
        return None

    url: str = db["url"]
    st.caption(f"Connected to `{safe_url(url)}` · read-only")
    if st.button("Disconnect"):
        st.session_state.pop("db", None)
        st.rerun()
    mode = st.radio("Read", ["Table", "Query"], horizontal=True, key="db_mode")
    table: str | None = None
    query: str | None = None
    if mode == "Table":
        if not db["tables"]:
            st.warning("No tables or views are visible to this user.")
            return None
        table = st.selectbox("Table or view", db["tables"], key="db_table")
    else:
        query = st.text_area(
            "SELECT query",
            key="db_query",
            placeholder="SELECT * FROM sales WHERE order_date >= '2025-06-01'",
            help="A single read-only SELECT or WITH statement. Press Ctrl+Enter to run.",
        ).strip()
        if not query:
            return None
    limit = int(
        st.number_input(
            "Row limit",
            min_value=1_000,
            max_value=5_000_000,
            value=DEFAULT_ROW_LIMIT,
            step=100_000,
            help="Protects memory on very large tables.",
        )
    )
    return (lambda: analyze_database(url, table, query, limit)), _digest(url, table, query, limit)


def render_sidebar() -> Source | None:
    st.header("Data source")
    kind = st.segmented_control("Source", ["File", "Database"], default="File", key="source_kind")
    if kind == "Database":
        return database_source()
    return file_source()
