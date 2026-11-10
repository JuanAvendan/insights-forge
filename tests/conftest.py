from __future__ import annotations

import csv
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import duckdb
import pytest

from insights_forge.profiling import DatasetProfile, profile_table

MakeProfile = Callable[[str], DatasetProfile]
SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "sales.csv"


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


@pytest.fixture
def make_profile(con: duckdb.DuckDBPyConnection) -> MakeProfile:
    """Create table ``dataset`` from a SELECT statement and profile it."""

    def _make(select_sql: str) -> DatasetProfile:
        con.execute(f"CREATE OR REPLACE TABLE dataset AS {select_sql}")
        return profile_table(con)

    return _make


@pytest.fixture(scope="session")
def shop_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """SQLite copy of the sample sales data, plus a view."""
    path = tmp_path_factory.mktemp("sql") / "shop.db"
    # sqlite3's context manager commits but does not close: closing() does both.
    with SAMPLE.open(encoding="utf-8") as fh, closing(sqlite3.connect(path)) as db, db:
        reader = csv.reader(fh)
        header = next(reader)
        db.execute(
            "CREATE TABLE sales (order_id TEXT, order_date TEXT, region TEXT, channel TEXT, "
            "product TEXT, category TEXT, unit_price REAL, quantity INTEGER, discount REAL, "
            "total REAL, customer_email TEXT)"
        )
        rows = [[v if v != "" else None for v in row] for row in reader]
        db.executemany(f"INSERT INTO sales VALUES ({', '.join('?' * len(header))})", rows)
        db.execute(
            "CREATE VIEW revenue_by_region AS SELECT region, SUM(total) AS revenue "
            "FROM sales GROUP BY region"
        )
    return path
