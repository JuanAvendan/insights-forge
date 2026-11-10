"""Common contract that every data-source connector implements.

Design
------
Every source (files, SQL databases, MongoDB, cloud storage...) is loaded into an
in-memory DuckDB table. Profiling and insight generation then work on that single
engine, so adding a new source never requires touching the analysis code.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

import duckdb

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_STAGING_VIEW = "__insights_forge_staging"


class ConnectorError(Exception):
    """Raised when a source cannot be read."""


class Connector(ABC):
    """Base class for all data-source connectors."""

    @abstractmethod
    def read(self, con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyRelation:
        """Return a (possibly lazy) DuckDB relation over the source data."""

    def load(
        self,
        con: duckdb.DuckDBPyConnection,
        table_name: str = "dataset",
    ) -> duckdb.DuckDBPyRelation:
        """Materialize the source into ``table_name`` and return it.

        Materializing decouples the table from the original source, so it stays
        valid even after a temporary file is deleted or a DB connection closes.
        """
        if not _IDENTIFIER.fullmatch(table_name):
            msg = f"Invalid table name: {table_name!r}"
            raise ValueError(msg)

        try:
            relation = self.read(con)
        except duckdb.Error as exc:
            raise ConnectorError(str(exc)) from exc

        relation.create_view(_STAGING_VIEW, replace=True)
        try:
            con.execute(f'CREATE OR REPLACE TABLE "{table_name}" AS FROM {_STAGING_VIEW}')
        finally:
            con.execute(f"DROP VIEW IF EXISTS {_STAGING_VIEW}")
        return con.table(f'"{table_name}"')
