from __future__ import annotations

from collections.abc import Callable

import duckdb
import pytest

from insights_forge.profiling import DatasetProfile, profile_table

MakeProfile = Callable[[str], DatasetProfile]


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
