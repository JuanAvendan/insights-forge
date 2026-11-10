"""Data-source connectors. Each one loads its source into a DuckDB table."""

from insights_forge.connectors.base import Connector, ConnectorError
from insights_forge.connectors.files import SUPPORTED_EXTENSIONS, FileConnector, FileFormat
from insights_forge.connectors.mongo import MongoConnector, list_collections
from insights_forge.connectors.remote import URLConnector, is_remote
from insights_forge.connectors.sql import SQLITE_EXTENSIONS, SQLConnector, list_tables, safe_url

__all__ = [
    "SQLITE_EXTENSIONS",
    "SUPPORTED_EXTENSIONS",
    "Connector",
    "ConnectorError",
    "FileConnector",
    "FileFormat",
    "MongoConnector",
    "SQLConnector",
    "URLConnector",
    "is_remote",
    "list_collections",
    "list_tables",
    "safe_url",
]
