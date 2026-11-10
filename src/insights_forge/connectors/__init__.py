"""Data-source connectors. Each one loads its source into a DuckDB table."""

from insights_forge.connectors.base import Connector, ConnectorError
from insights_forge.connectors.files import SUPPORTED_EXTENSIONS, FileConnector, FileFormat

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "Connector",
    "ConnectorError",
    "FileConnector",
    "FileFormat",
]
