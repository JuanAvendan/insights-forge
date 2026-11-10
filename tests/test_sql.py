"""SQL connector tests.

SQLite tests always run. PostgreSQL/MySQL integration tests run against the
containers from ``compose.yaml`` when their URLs are set:

    docker compose up -d --wait
    INSIGHTS_FORGE_TEST_POSTGRES_URL=postgresql+psycopg://analyst:analyst@localhost:55432/shop
    INSIGHTS_FORGE_TEST_MYSQL_URL=mysql+pymysql://analyst:analyst@localhost:53306/shop
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest
from sqlalchemy import text

from insights_forge.connectors import ConnectorError, SQLConnector, list_tables, safe_url
from insights_forge.connectors.sql import CONNECT_TIMEOUT_S, make_engine, validate_query
from insights_forge.pipeline import analyze, analyze_file

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "sales.csv"


@pytest.fixture
def url(shop_db: Path) -> str:
    return f"sqlite:///{shop_db.as_posix()}"


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM sales",
        "select region from sales;",
        "  WITH x AS (SELECT 1 AS a) SELECT * FROM x",
        "-- monthly totals\nSELECT 1",
        "/* note */ SELECT 1",
    ],
)
def test_read_queries_are_accepted(query: str) -> None:
    assert validate_query(query).lower().lstrip().find("select") >= 0


@pytest.mark.parametrize(
    ("query", "reason"),
    [
        ("DELETE FROM sales", "Only read queries"),
        ("UPDATE sales SET total = 0", "Only read queries"),
        ("DROP TABLE sales", "Only read queries"),
        ("SELECT 1; DROP TABLE sales", "single SQL statement"),
        ("-- SELECT\nDELETE FROM sales", "Only read queries"),
    ],
)
def test_write_or_multiple_statements_are_rejected(query: str, reason: str) -> None:
    with pytest.raises(ConnectorError, match=reason):
        validate_query(query)


def test_safe_url_hides_password() -> None:
    assert safe_url("postgresql+psycopg://bob:s3cret@db:5432/shop") == (
        "postgresql+psycopg://bob:***@db:5432/shop"
    )
    assert safe_url("sqlite:///C:/data/shop.db") == "sqlite:///C:/data/shop.db"
    assert safe_url("not a url") == "<invalid database URL>"


@pytest.mark.parametrize(
    ("bad_url", "message"),
    [
        ("not a url", "Invalid database URL"),
        ("sqlite://", "needs a database file path"),
        ("sqlite:///:memory:", "needs a database file path"),
        ("sqlite:///does/not/exist.db", "SQLite file not found"),
        ("nosuchdb://host/db", "Unsupported database type"),
    ],
)
def test_engine_errors(bad_url: str, message: str) -> None:
    with pytest.raises(ConnectorError, match=message):
        make_engine(bad_url)


def test_connector_needs_exactly_one_target() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        SQLConnector("sqlite:///x.db")
    with pytest.raises(ValueError, match="exactly one"):
        SQLConnector("sqlite:///x.db", table="t", query="SELECT 1")


def test_display_name_never_shows_password() -> None:
    connector = SQLConnector("mysql+pymysql://bob:s3cret@db/shop", table="sales")
    assert connector.name == "sales (mysql+pymysql://bob:***@db/shop)"
    assert "s3cret" not in connector.name


# --------------------------------------------------------------------------- SQLite


def test_list_tables_includes_views(url: str) -> None:
    assert list_tables(url) == ["revenue_by_region", "sales"]


def test_load_table_matches_csv(url: str) -> None:
    from_db = analyze(SQLConnector(url, table="sales"), "sales")
    from_csv = analyze_file(SAMPLE)
    assert from_db.profile.rows == from_csv.profile.rows == 1012
    assert from_db.profile.column("order_date").kind == "temporal"  # text parsed as DATE
    assert {(i.check, i.column) for i in from_db.profile.issues} == {
        (i.check, i.column) for i in from_csv.profile.issues
    }
    assert from_db.insights.roles.date == "order_date"


def test_load_view_and_query(url: str) -> None:
    con = duckdb.connect()
    assert SQLConnector(url, table="revenue_by_region").load(con).shape == (5, 2)
    rel = SQLConnector(
        url, query="SELECT channel, COUNT(*) AS n FROM sales GROUP BY channel ORDER BY n DESC;"
    ).load(con)
    assert rel.columns == ["channel", "n"]
    assert rel.shape == (3, 2)


def test_row_limit(url: str) -> None:
    con = duckdb.connect()
    assert SQLConnector(url, table="sales", limit=10).load(con).shape[0] == 10
    assert SQLConnector(url, query="SELECT * FROM sales", limit=7).load(con).shape[0] == 7


def test_unknown_table_lists_available(url: str) -> None:
    with pytest.raises(ConnectorError, match=r"'nope' not found\. Available: revenue_by_region"):
        SQLConnector(url, table="nope").load(duckdb.connect())


def test_bad_sql_is_a_connector_error(url: str) -> None:
    with pytest.raises(ConnectorError, match="no such column"):
        SQLConnector(url, query="SELECT missing_column FROM sales").load(duckdb.connect())


def test_hidden_write_inside_with_is_blocked(url: str) -> None:
    with pytest.raises(ConnectorError):
        SQLConnector(
            url, query="WITH x AS (SELECT 1) INSERT INTO sales (order_id) SELECT 'x'"
        ).load(duckdb.connect())
    assert analyze(SQLConnector(url, table="sales"), "s").profile.rows == 1012


def test_sqlite_connections_are_read_only(url: str) -> None:
    engine = make_engine(url)
    with engine.connect() as conn, pytest.raises(Exception, match="readonly"):
        conn.execute(text("INSERT INTO sales (order_id) VALUES ('hack')"))
    engine.dispose()


def test_sqlite_helper(shop_db: Path) -> None:
    connector = SQLConnector.sqlite(shop_db, table="sales")
    assert connector.url.startswith("sqlite:///")
    assert connector.load(duckdb.connect()).shape[0] == 1012


# --------------------------------------------------------------------------- servers

SERVERS = {
    "postgres": os.environ.get("INSIGHTS_FORGE_TEST_POSTGRES_URL"),
    "mysql": os.environ.get("INSIGHTS_FORGE_TEST_MYSQL_URL"),
}


@pytest.fixture(params=sorted(SERVERS))
def server_url(request: pytest.FixtureRequest) -> str:
    server_url = SERVERS[request.param]
    if not server_url:
        pytest.skip(f"INSIGHTS_FORGE_TEST_{request.param.upper()}_URL not set")
    return server_url


@pytest.mark.integration
def test_server_tables(server_url: str) -> None:
    assert set(list_tables(server_url)) >= {"sales", "monthly_sales"}


@pytest.mark.integration
def test_server_load_matches_csv(server_url: str) -> None:
    from_db = analyze(SQLConnector(server_url, table="sales"), "sales")
    from_csv = analyze_file(SAMPLE)
    assert from_db.profile.rows == 1012
    assert from_db.df["total"].dtype.is_float()  # NUMERIC/DECIMAL arrive as floats
    assert from_db.profile.column("region").nulls == 20
    assert from_db.profile.column("customer_email").nulls == 50
    assert from_db.insights.insights[0].title == from_csv.insights.insights[0].title


@pytest.mark.integration
def test_server_view_and_query(server_url: str) -> None:
    con = duckdb.connect()
    assert SQLConnector(server_url, table="monthly_sales").load(con).shape[1] == 4
    rel = SQLConnector(
        server_url, query="SELECT region, COUNT(*) AS n FROM sales GROUP BY region"
    ).load(con)
    assert rel.shape[1] == 2


@pytest.mark.integration
def test_server_session_is_read_only(server_url: str) -> None:
    with pytest.raises(ConnectorError):
        SQLConnector(server_url, query="SELECT * FROM sales FOR UPDATE").load(duckdb.connect())


@pytest.mark.integration
def test_server_wrong_password_is_masked(server_url: str) -> None:
    bad = server_url.replace("analyst:analyst@", "analyst:WrongPass123@")
    with pytest.raises(ConnectorError) as exc:
        list_tables(bad)
    assert "WrongPass123" not in str(exc.value)
    assert "***" in str(exc.value)


@pytest.mark.parametrize(
    "server",
    ["postgresql+psycopg://u:p@localhost/db", "mysql+pymysql://u:p@localhost/db"],
)
def test_servers_get_a_connect_timeout(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A wrong host must fail fast, not after the OS TCP timeout (minutes on Windows)."""
    captured: dict[str, object] = {}

    def fake_create_engine(url: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("insights_forge.connectors.sql.create_engine", fake_create_engine)
    make_engine(server)
    assert captured["connect_args"] == {"connect_timeout": CONNECT_TIMEOUT_S}
