from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from openpyxl import Workbook

from insights_forge.connectors import ConnectorError, FileConnector, FileFormat
from insights_forge.connectors.files import detect_format

ROWS = [(1, "Bogotá", 10.5), (2, "Medellín", None), (3, "Cali", 7.25)]
HEADER = ("id", "city", "amount")


def _write_csv(path: Path, sep: str = ",") -> Path:
    lines = [sep.join(HEADER)]
    lines += [sep.join("" if v is None else str(v) for v in row) for row in ROWS]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_excel(path: Path, sheet: str = "Data") -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Cover"
    ws.append(["not the data"])
    data_ws = wb.create_sheet(sheet)
    data_ws.append(HEADER)
    for row in ROWS:
        data_ws.append(row)
    wb.save(path)
    return path


def _assert_loaded(rel: duckdb.DuckDBPyRelation) -> None:
    assert rel.columns == list(HEADER)
    assert rel.fetchall() == [tuple(r) for r in ROWS]


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("a.csv", FileFormat.CSV),
        ("a.TSV", FileFormat.CSV),
        ("a.xlsx", FileFormat.EXCEL),
        ("a.parquet", FileFormat.PARQUET),
        ("a.jsonl", FileFormat.JSON),
    ],
)
def test_detect_format(filename: str, expected: FileFormat) -> None:
    assert detect_format(Path(filename)) is expected


def test_detect_format_rejects_unknown_extension() -> None:
    with pytest.raises(ConnectorError, match="Unsupported file type"):
        detect_format(Path("a.docx"))


def test_load_csv(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    _assert_loaded(FileConnector(_write_csv(tmp_path / "data.csv")).load(con))


def test_load_csv_sniffs_semicolon_delimiter(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    _assert_loaded(FileConnector(_write_csv(tmp_path / "data.csv", sep=";")).load(con))


def test_load_parquet(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    src = _write_csv(tmp_path / "data.csv")
    out = tmp_path / "data.parquet"
    con.read_csv(str(src)).write_parquet(str(out))
    _assert_loaded(FileConnector(out).load(con))


def test_load_json(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_text(json.dumps([dict(zip(HEADER, row, strict=True)) for row in ROWS]))
    _assert_loaded(FileConnector(path).load(con))


def test_load_excel_named_sheet(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = _write_excel(tmp_path / "data.xlsx")
    _assert_loaded(FileConnector(path, sheet="Data").load(con))


def test_load_excel_defaults_to_first_sheet(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = _write_excel(tmp_path / "data.xlsx")
    assert FileConnector(path).load(con).columns == ["not the data"]


def test_load_excel_unknown_sheet_raises(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = _write_excel(tmp_path / "data.xlsx")
    with pytest.raises(ConnectorError, match="Could not read Excel"):
        FileConnector(path, sheet="Missing").load(con)


def test_table_survives_source_deletion(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = _write_csv(tmp_path / "data.csv")
    FileConnector(path).load(con, table_name="sales")
    path.unlink()
    assert con.table("sales").shape == (3, 3)


def test_load_replaces_existing_table(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = _write_csv(tmp_path / "data.csv")
    FileConnector(path).load(con)
    FileConnector(path).load(con)
    assert con.table("dataset").shape == (3, 3)


def test_missing_file_raises(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    with pytest.raises(ConnectorError, match="File not found"):
        FileConnector(tmp_path / "nope.csv").load(con)


def test_malformed_file_raises_connector_error(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = tmp_path / "broken.parquet"
    path.write_bytes(b"this is not parquet")
    with pytest.raises(ConnectorError):
        FileConnector(path).load(con)


@pytest.mark.parametrize("bad_name", ["1table", "drop table x;--", "a-b", ""])
def test_invalid_table_name_rejected(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, bad_name: str
) -> None:
    with pytest.raises(ValueError, match="Invalid table name"):
        FileConnector(_write_csv(tmp_path / "data.csv")).load(con, table_name=bad_name)
