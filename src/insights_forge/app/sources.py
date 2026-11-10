"""Sidebar data-source pickers: files (incl. SQLite), the sample, databases (SQL and
MongoDB) and remote URLs (https, Google Sheets, S3/GCS/Azure).

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
from urllib.parse import quote_plus

import streamlit as st
from sqlalchemy.engine import URL

from insights_forge.connectors import (
    SQLITE_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    ConnectorError,
    MongoConnector,
    SQLConnector,
    URLConnector,
    is_remote,
    list_collections,
    list_tables,
    safe_url,
)
from insights_forge.connectors.mongo import safe_uri
from insights_forge.connectors.sql import DEFAULT_ROW_LIMIT
from insights_forge.pipeline import Analysis, analyze, analyze_file

SAMPLE_FILE = Path(__file__).resolve().parents[3] / "data" / "sample" / "sales.csv"
DB_URL_ENV = "INSIGHTS_FORGE_DB_URL"
DB_ENGINES = {
    "PostgreSQL": ("postgresql+psycopg", 5432),
    "MySQL": ("mysql+pymysql", 3306),
    "MongoDB": ("mongodb", 27017),
}
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


@st.cache_data(show_spinner="Reading the collection...", max_entries=5, ttl=600)
def analyze_mongo(url: str, collection: str, filter_json: str, limit: int) -> Analysis:
    connector = MongoConnector(url, collection, filter=filter_json, limit=limit)
    return analyze(connector, connector.name)


@st.cache_data(show_spinner="Downloading and analyzing...", max_entries=5, ttl=600)
def analyze_url(url: str, storage_options: dict[str, str] | None) -> Analysis:
    # allow_private stays False: the app may be deployed publicly, and a user-supplied
    # URL must never reach internal services (SSRF).
    connector = URLConnector(url, storage_options=storage_options)
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
        port_number = int(port) if port.strip() else default_port
        if driver == "mongodb":
            auth = f"{quote_plus(user)}:{quote_plus(password)}@" if user else ""
            url = f"mongodb://{auth}{host or 'localhost'}:{port_number}/{database}"
        else:
            url = URL.create(
                driver,
                username=user or None,
                password=password or None,
                host=host or None,
                port=port_number,
                database=database or None,
            ).render_as_string(hide_password=False)
    is_mongo = url.startswith(("mongodb://", "mongodb+srv://"))
    try:
        tables = list_collections(url) if is_mongo else list_tables(url)
    except ConnectorError as exc:
        st.session_state.pop("db", None)
        st.error(f"Could not connect: {exc}")
        return
    st.session_state.db = {"url": url, "tables": tables, "mongo": is_mongo}


def database_source() -> Source | None:
    _connect_form()
    db = st.session_state.get("db")
    if not db:
        st.caption(
            "Try it locally: `docker compose up -d --wait` starts PostgreSQL (port 55432), "
            "MySQL (53306) and MongoDB (57017) with the sample data. Database `shop`, user "
            "and password `analyst`."
        )
        return None

    url: str = db["url"]
    shown = safe_uri(url) if db.get("mongo") else safe_url(url)
    st.caption(f"Connected to `{shown}` · read-only")
    if st.button("Disconnect"):
        st.session_state.pop("db", None)
        st.rerun()
    if db.get("mongo"):
        return _mongo_picker(url, db["tables"])
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
    limit = _row_limit()
    return (lambda: analyze_database(url, table, query, limit)), _digest(url, table, query, limit)


def _row_limit() -> int:
    return int(
        st.number_input(
            "Row limit",
            min_value=1_000,
            max_value=5_000_000,
            value=DEFAULT_ROW_LIMIT,
            step=100_000,
            help="Protects memory on very large tables.",
        )
    )


def _mongo_picker(url: str, collections: list[str]) -> Source | None:
    if not collections:
        st.warning("No collections are visible to this user.")
        return None
    collection = st.selectbox("Collection", collections, key="mongo_collection")
    filter_json = st.text_area(
        "Filter (JSON, optional)",
        key="mongo_filter",
        placeholder='{"region": "East", "total": {"$gt": 100}}',
        help="A MongoDB query filter. Operators that run JavaScript ($where, $function) are "
        "not allowed. Press Ctrl+Enter to apply.",
    ).strip()
    limit = _row_limit()
    load = lambda: analyze_mongo(url, collection, filter_json, limit)  # noqa: E731
    return load, _digest(url, collection, filter_json, limit)


def url_source() -> Source | None:
    url = st.text_input(
        "File URL",
        key="remote_url",
        placeholder="https://example.com/data.csv",
        help="An https link to a CSV/Excel/Parquet/JSON file, a Google Sheets link shared as "
        "'anyone with the link', or s3://, gs://, az:// paths. For safety, addresses on "
        "private networks are refused.",
    ).strip()
    with st.expander("Cloud credentials (S3-compatible)"):
        st.caption(
            "Optional. By default the server's AWS_*, GOOGLE_APPLICATION_CREDENTIALS or "
            "AZURE_* environment variables are used."
        )
        endpoint = st.text_input("Endpoint URL", key="s3_endpoint", placeholder="https://...")
        key_id = st.text_input("Access key ID", key="s3_key")
        secret = st.text_input("Secret access key", key="s3_secret", type="password")
        region = st.text_input("Region", key="s3_region", placeholder="us-east-1")
    if not url:
        st.caption(
            "Try: `https://raw.githubusercontent.com/JuanAvendan/insights-forge/main/"
            "data/sample/sales.csv`"
        )
        return None
    if not is_remote(url):
        st.error("Enter an https://, s3://, gs:// or az:// URL.")
        return None
    options = {
        name: value
        for name, value in {
            "aws_endpoint_url": endpoint,
            "aws_access_key_id": key_id,
            "aws_secret_access_key": secret,
            "aws_region": region,
        }.items()
        if value
    } or None
    if options and endpoint.startswith("http://"):
        options["aws_allow_http"] = "true"
    return (lambda: analyze_url(url, options)), _digest(url, sorted((options or {}).items()))


def render_sidebar() -> Source | None:
    st.header("Data source")
    kind = st.segmented_control(
        "Source", ["File", "Database", "URL"], default="File", key="source_kind"
    )
    if kind == "Database":
        return database_source()
    if kind == "URL":
        return url_source()
    return file_source()
