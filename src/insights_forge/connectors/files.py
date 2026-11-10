"""Connector for local files: CSV/TSV, Excel, Parquet and JSON."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import duckdb
import polars as pl

from insights_forge.connectors.base import Connector, ConnectorError


class FileFormat(StrEnum):
    CSV = "csv"
    EXCEL = "excel"
    PARQUET = "parquet"
    JSON = "json"


_EXTENSIONS: dict[str, FileFormat] = {
    ".csv": FileFormat.CSV,
    ".tsv": FileFormat.CSV,
    ".txt": FileFormat.CSV,
    ".xlsx": FileFormat.EXCEL,
    ".xlsm": FileFormat.EXCEL,
    ".xls": FileFormat.EXCEL,
    ".parquet": FileFormat.PARQUET,
    ".json": FileFormat.JSON,
    ".jsonl": FileFormat.JSON,
    ".ndjson": FileFormat.JSON,
}

SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(ext.lstrip(".") for ext in _EXTENSIONS)


def detect_format(path: Path) -> FileFormat:
    """Infer the file format from its extension."""
    try:
        return _EXTENSIONS[path.suffix.lower()]
    except KeyError:
        msg = f"Unsupported file type {path.suffix!r}. Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
        raise ConnectorError(msg) from None


@dataclass(frozen=True, slots=True)
class FileConnector(Connector):
    """Read a local file into DuckDB.

    Args:
        path: File to read.
        sheet: Excel sheet name. Defaults to the first sheet. Ignored for other formats.
    """

    path: Path
    sheet: str | None = None

    def read(self, con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyRelation:
        if not self.path.is_file():
            msg = f"File not found: {self.path}"
            raise ConnectorError(msg)

        source = str(self.path)
        match detect_format(self.path):
            case FileFormat.CSV:
                # DuckDB's sniffer auto-detects delimiter, header, quoting and types.
                return con.read_csv(source)
            case FileFormat.PARQUET:
                return con.read_parquet(source)
            case FileFormat.JSON:
                return con.read_json(source)
            case FileFormat.EXCEL:
                return con.from_arrow(self._read_excel().to_arrow())

    def _read_excel(self) -> pl.DataFrame:
        # Polars + fastexcel (Rust "calamine" engine) is much faster than openpyxl
        # and avoids downloading DuckDB's excel extension at runtime.
        try:
            if self.sheet is None:
                return pl.read_excel(self.path, sheet_id=1)
            return pl.read_excel(self.path, sheet_name=self.sheet)
        except Exception as exc:  # fastexcel raises its own exception hierarchy
            msg = f"Could not read Excel file {self.path.name}: {exc}"
            raise ConnectorError(msg) from exc
