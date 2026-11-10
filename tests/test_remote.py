"""URL connector tests: a local HTTP server stands in for the internet.

The S3 integration test reads ``INSIGHTS_FORGE_TEST_S3_URL`` (e.g. ``s3://shop/sales.csv``)
through the S3 gateway in ``compose.yaml``, configured with the standard AWS_* variables.
"""

from __future__ import annotations

import os
import threading
import urllib.request
from collections.abc import Iterator
from email.message import Message
from http.client import HTTPMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import duckdb
import polars as pl
import pytest
from openpyxl import Workbook

from insights_forge.connectors import ConnectorError, URLConnector, is_remote
from insights_forge.connectors.remote import (
    _CheckedRedirects,
    _filename,
    check_public_host,
    download,
    google_sheet_export_url,
)
from insights_forge.pipeline import analyze

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "sales.csv"


class _Handler(BaseHTTPRequestHandler):
    files: ClassVar[dict[str, tuple[bytes, str, str | None]]] = {}

    def do_GET(self) -> None:
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
            return
        if self.path not in self.files:
            self.send_error(404)
            return
        body, content_type, disposition = self.files[self.path]
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if disposition:
            self.send_header("Content-Disposition", f'attachment; filename="{disposition}"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # keep test output clean
        pass


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    book = tmp_path_factory.mktemp("xl") / "data.xlsx"
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["city", "amount"])
    for i in range(20):
        ws.append([f"city {i % 4}", 10.0 + i])
    wb.save(book)
    _Handler.files = {
        "/sales.csv": (SAMPLE.read_bytes(), "text/csv", None),
        "/export": (SAMPLE.read_bytes(), "text/csv", "My Sheet - Tab.csv"),
        "/data": (book.read_bytes(), "application/vnd.ms-excel", None),
    }
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


# --------------------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    ("location", "remote"),
    [
        ("https://example.com/a.csv", True),
        ("s3://bucket/a.parquet", True),
        ("gs://bucket/a.csv", True),
        ("az://container/a.csv", True),
        ("data/sales.csv", False),
        ("C:/data/sales.csv", False),
    ],
)
def test_is_remote(location: str, remote: bool) -> None:
    assert is_remote(location) is remote


@pytest.mark.parametrize(
    ("url", "export"),
    [
        (
            "https://docs.google.com/spreadsheets/d/1AbC_x-9/edit?usp=sharing",
            "https://docs.google.com/spreadsheets/d/1AbC_x-9/export?format=csv",
        ),
        (
            "https://docs.google.com/spreadsheets/d/1AbC_x-9/edit#gid=42",
            "https://docs.google.com/spreadsheets/d/1AbC_x-9/export?format=csv&gid=42",
        ),
        (
            "https://docs.google.com/spreadsheets/d/1AbC_x-9/edit?gid=7",
            "https://docs.google.com/spreadsheets/d/1AbC_x-9/export?format=csv&gid=7",
        ),
        ("https://example.com/sheet.csv", None),
    ],
)
def test_google_sheet_export_url(url: str, export: str | None) -> None:
    assert google_sheet_export_url(url) == export


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        "http://127.0.0.1:8501/",
        "http://[::1]/",
        "http://10.1.2.3/data.csv",
        "http://192.168.0.10/data.csv",
        "http://0.0.0.0/",
    ],
)
def test_internal_addresses_are_refused(url: str) -> None:
    with pytest.raises(ConnectorError, match="internal address"):
        check_public_host(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/a.csv", "gopher://x"])
def test_only_http_downloads(url: str) -> None:
    with pytest.raises(ConnectorError, match=r"Only http\(s\) URLs"):
        check_public_host(url)


def test_public_ip_literal_is_allowed() -> None:
    check_public_host("https://8.8.8.8/data.csv")  # no DNS lookup needed for a literal


def test_private_allowed_when_opted_in() -> None:
    check_public_host("http://127.0.0.1/data.csv", allow_private=True)


def test_redirects_are_checked_again() -> None:
    handler = _CheckedRedirects(allow_private=False)
    request = urllib.request.Request("https://8.8.8.8/a.csv")
    with pytest.raises(ConnectorError, match="internal address"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            HTTPMessage(),
            "http://169.254.169.254/",
        )


@pytest.mark.parametrize(
    ("url", "content_type", "disposition", "name"),
    [
        ("https://x.org/files/sales.csv", "text/plain", None, "sales.csv"),
        ("https://x.org/export?format=csv", "text/csv", None, "export.csv"),
        ("https://x.org/download", "application/json", None, "download.json"),
        ("https://x.org/d", "text/csv", "../../etc/evil.csv", "evil.csv"),
        ("https://x.org/", "application/octet-stream", None, "download.csv"),
    ],
)
def test_download_filename(url: str, content_type: str, disposition: str | None, name: str) -> None:
    headers = Message()
    headers["Content-Type"] = content_type
    if disposition:
        headers["Content-Disposition"] = f'attachment; filename="{disposition}"'
    assert _filename(url, headers) == name


# --------------------------------------------------------------------------- downloads


def test_local_server_is_refused_by_default(server: str) -> None:
    with pytest.raises(ConnectorError, match="internal address"):
        URLConnector(f"{server}/sales.csv").load(duckdb.connect())


def test_csv_over_http(server: str) -> None:
    analysis = analyze(URLConnector(f"{server}/sales.csv", allow_private=True), "web")
    assert analysis.profile.rows == 1012
    assert analysis.insights.roles.measure == "total"


def test_excel_with_wrong_content_type(server: str, tmp_path: Path) -> None:
    """Served as legacy .xls but really .xlsx: the file's magic bytes decide."""
    assert download(f"{server}/data", tmp_path, allow_private=True).suffix == ".xlsx"
    rel = URLConnector(f"{server}/data", allow_private=True).load(duckdb.connect())
    assert rel.shape == (20, 2)


def test_download_uses_content_disposition(server: str, tmp_path: Path) -> None:
    path = download(f"{server}/export", tmp_path, allow_private=True)
    assert path.name == "My Sheet - Tab.csv"


def test_download_size_limit(server: str, tmp_path: Path) -> None:
    with pytest.raises(ConnectorError, match="too large"):
        download(f"{server}/sales.csv", tmp_path, allow_private=True, max_bytes=1000)


def test_http_errors(server: str, tmp_path: Path) -> None:
    with pytest.raises(ConnectorError, match="HTTP 404"):
        download(f"{server}/missing.csv", tmp_path, allow_private=True)


def test_redirect_to_metadata_endpoint_is_blocked(server: str, tmp_path: Path) -> None:
    # Even with the first hop allowed, a redirect to the metadata IP is refused...
    # unless private addresses are explicitly allowed for every hop.
    with pytest.raises(ConnectorError):
        download(f"{server}/moved", tmp_path)


def test_connector_name() -> None:
    assert URLConnector("https://x.org/data/sales%202025.csv").name == "sales 2025.csv"
    assert URLConnector("https://docs.google.com/spreadsheets/d/abc/edit").name == "Google Sheet"


def test_unsupported_scheme() -> None:
    with pytest.raises(ConnectorError, match="Unsupported URL scheme"):
        URLConnector("ftp://example.com/a.csv").load(duckdb.connect())


# --------------------------------------------------------------------------- cloud


def test_cloud_unsupported_format() -> None:
    with pytest.raises(ConnectorError, match="Unsupported cloud file type"):
        URLConnector("s3://bucket/book.xlsx").load(duckdb.connect())


@pytest.mark.parametrize(
    ("url", "reader"),
    [
        ("s3://b/k.parquet", "scan_parquet"),
        ("gs://b/k.csv", "scan_csv"),
        ("az://b/k.jsonl", "scan_ndjson"),
    ],
)
def test_cloud_reader_dispatch(url: str, reader: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake(source: str, **kwargs: object) -> pl.LazyFrame:
        calls.append((source, kwargs))
        return pl.LazyFrame({"x": [1, 2, 3]})

    monkeypatch.setattr(pl, reader, fake)
    options = {"aws_region": "us-east-1"}
    rel = URLConnector(url, storage_options=options).load(duckdb.connect())
    assert rel.shape == (3, 1)
    assert calls[0][0] == url
    assert calls[0][1]["storage_options"] == options


def test_cloud_errors_become_connector_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(source: str, **kwargs: object) -> pl.LazyFrame:
        msg = "403 Forbidden: access denied"
        raise OSError(msg)

    monkeypatch.setattr(pl, "scan_csv", boom)
    with pytest.raises(ConnectorError, match=r"Could not read s3://b/k\.csv: 403 Forbidden"):
        URLConnector("s3://b/k.csv").load(duckdb.connect())


@pytest.mark.integration
def test_s3_integration() -> None:
    url = os.environ.get("INSIGHTS_FORGE_TEST_S3_URL")
    if not url:
        pytest.skip("INSIGHTS_FORGE_TEST_S3_URL not set")
    analysis = analyze(URLConnector(url), "s3")
    assert analysis.profile.rows == 1012
    assert analysis.profile.duplicate_rows == 12
