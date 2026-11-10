"""Data-source connectors. Each one loads its source into a DuckDB table."""

from insights_forge.connectors.base import Connector, ConnectorError
from insights_forge.connectors.files import SUPPORTED_EXTENSIONS, FileConnector, FileFormat
from insights_forge.connectors.sql import SQLITE_EXTENSIONS, SQLConnector, list_tables, safe_url

__all__ = [
    "SQLITE_EXTENSIONS",
    "SUPPORTED_EXTENSIONS",
    "Connector",
    "ConnectorError",
    "FileConnector",
    "FileFormat",
    "SQLConnector",
    "list_tables",
    "safe_url",
]
