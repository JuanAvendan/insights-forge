"""Remote files: HTTPS URLs, Google Sheets links and cloud object storage.

- ``https://...``: downloaded (size and time limited) and read like a local file,
  so every file format works, Excel included.
- Google Sheets: a sharing link (``docs.google.com/spreadsheets/d/<id>/...``) of a
  sheet shared as "anyone with the link" is turned into its CSV export URL.
- ``s3://``, ``gs://``, ``az://``...: read directly by Polars (no extra
  dependencies). Credentials come from the standard environment variables
  (``AWS_ACCESS_KEY_ID``, ``AWS_ENDPOINT_URL``, ``GOOGLE_APPLICATION_CREDENTIALS``,
  ``AZURE_STORAGE_ACCOUNT_NAME``...) or from ``storage_options``.

SSRF protection: a server that fetches user-supplied URLs could be tricked into
reading internal services (for example the cloud metadata endpoint at
169.254.169.254). By default, hosts that resolve to private, loopback, link-local
or reserved addresses are refused, and every redirect is checked again. This does
not defend against DNS rebinding between the check and the request; deploy behind
an egress firewall if the app is public.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import Message
from http.client import HTTPMessage
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import duckdb
import polars as pl

from insights_forge.connectors.base import Connector, ConnectorError
from insights_forge.connectors.files import FileConnector

HTTP_SCHEMES = frozenset({"http", "https"})
CLOUD_SCHEMES = frozenset({"s3", "s3a", "gs", "gcs", "az", "abfs", "abfss", "azure"})
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
TIMEOUT_S = 30
USER_AGENT = "insights-forge (+https://github.com/JuanAvendan/insights-forge)"

_SHEET = re.compile(r"^https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)")
_CONTENT_TYPES = {
    "text/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "application/json": ".json",
    "application/x-ndjson": ".jsonl",
    "application/vnd.apache.parquet": ".parquet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
}
_KNOWN_SUFFIXES = (".csv", ".tsv", ".txt", ".json", ".jsonl", ".ndjson", ".parquet", ".xlsx")


def is_remote(location: str) -> bool:
    """True for URLs this module can read (as opposed to local paths)."""
    scheme = urlparse(location).scheme.lower()
    return scheme in HTTP_SCHEMES | CLOUD_SCHEMES


def google_sheet_export_url(url: str) -> str | None:
    """CSV export URL for a Google Sheets link, keeping the selected tab (``gid``)."""
    match = _SHEET.match(url)
    if not match:
        return None
    parsed = urlparse(url)
    gid = (parse_qs(parsed.query).get("gid") or parse_qs(parsed.fragment).get("gid") or [""])[0]
    export = f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=csv"
    return f"{export}&gid={gid}" if gid else export


def check_public_host(url: str, *, allow_private: bool = False) -> None:
    """Refuse URLs whose host resolves to a non-public address (SSRF protection)."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in HTTP_SCHEMES:
        msg = f"Only http(s) URLs can be downloaded, got {parsed.scheme or 'no'} scheme."
        raise ConnectorError(msg)
    host = parsed.hostname
    if not host:
        msg = f"No host in URL: {url}"
        raise ConnectorError(msg)
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        msg = f"Cannot resolve host {host!r}."
        raise ConnectorError(msg) from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            msg = (
                f"Refusing to fetch {host}: it resolves to the internal address {address}. "
                "Only public internet addresses are allowed."
            )
            raise ConnectorError(msg)


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Re-applies the host check to every redirect target."""

    def __init__(self, *, allow_private: bool) -> None:
        self.allow_private = allow_private

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        check_public_host(newurl, allow_private=self.allow_private)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _filename(url: str, headers: Message) -> str:
    """A file name with an extension FileConnector understands."""
    disposition = headers.get_filename()
    name = disposition or unquote(PurePosixPath(urlparse(url).path).name) or "download"
    name = Path(name).name  # never trust a path from the server
    if Path(name).suffix.lower() in _KNOWN_SUFFIXES:
        return name
    content_type = headers.get_content_type()
    return f"{Path(name).stem or 'download'}{_CONTENT_TYPES.get(content_type, '.csv')}"


def _fix_suffix(path: Path) -> Path:
    """Trust the file's magic bytes over a mislabelled Content-Type or extension."""
    with path.open("rb") as fh:
        head = fh.read(4)
    suffix = path.suffix.lower()
    if head == b"PK" and suffix in {".xls", ".csv", ".txt"}:
        target = ".xlsx"  # Office Open XML is a ZIP archive
    elif head == b"PAR1" and suffix != ".parquet":
        target = ".parquet"
    else:
        return path
    return path.rename(path.with_suffix(target))


def download(
    url: str,
    directory: Path,
    *,
    allow_private: bool = False,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> Path:
    """Download ``url`` into ``directory`` with host, size and time limits."""
    check_public_host(url, allow_private=allow_private)
    opener = urllib.request.build_opener(_CheckedRedirects(allow_private=allow_private))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with opener.open(request, timeout=TIMEOUT_S) as response:
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > max_bytes:
                msg = f"File too large: {declared:,} bytes (limit {max_bytes:,})."
                raise ConnectorError(msg)
            path = directory / _filename(response.geturl(), response.headers)
            received = 0
            with path.open("wb") as out:
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > max_bytes:
                        msg = f"File too large: over {max_bytes:,} bytes."
                        raise ConnectorError(msg)
                    out.write(chunk)
        path = _fix_suffix(path)
    except urllib.error.HTTPError as exc:
        hint = " Is the sheet shared as 'anyone with the link'?" if "docs.google" in url else ""
        msg = f"Download failed: HTTP {exc.code} {exc.reason}.{hint}"
        raise ConnectorError(msg) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        msg = f"Download failed: {reason}."
        raise ConnectorError(msg) from exc
    return path


def _read_cloud(url: str, storage_options: dict[str, str] | None) -> pl.DataFrame:
    suffix = PurePosixPath(urlparse(url).path).suffix.lower()
    options: dict[str, Any] = {"storage_options": storage_options} if storage_options else {}
    match suffix:
        case ".parquet":
            return pl.scan_parquet(url, **options).collect()
        case ".csv" | ".tsv" | ".txt":
            separator = "\t" if suffix == ".tsv" else ","
            return pl.scan_csv(url, separator=separator, **options).collect()
        case ".jsonl" | ".ndjson":
            return pl.scan_ndjson(url, **options).collect()
        case _:
            msg = (
                f"Unsupported cloud file type {suffix or '(none)'!r}: use CSV, TSV, Parquet or "
                "JSON Lines, or share the file over https."
            )
            raise ConnectorError(msg)


@dataclass(frozen=True, slots=True)
class URLConnector(Connector):
    """Read a file from an https URL, a Google Sheets link or cloud object storage.

    Args:
        url: ``https://...``, a Google Sheets link, or ``s3://``/``gs://``/``az://`` path.
        sheet: Excel sheet name for downloaded workbooks.
        storage_options: Cloud credentials/endpoint, overriding environment variables.
        allow_private: Allow downloads from private or local addresses (CLI and tests;
            never enable it in a publicly deployed app).
    """

    url: str
    sheet: str | None = None
    storage_options: dict[str, str] | None = None
    allow_private: bool = False

    @property
    def name(self) -> str:
        if _SHEET.match(self.url):
            return "Google Sheet"
        return unquote(PurePosixPath(urlparse(self.url).path).name) or self.url

    def read(self, con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyRelation:
        scheme = urlparse(self.url).scheme.lower()
        if scheme in HTTP_SCHEMES:
            source = google_sheet_export_url(self.url) or self.url
            with tempfile.TemporaryDirectory() as tmp:
                path = download(source, Path(tmp), allow_private=self.allow_private)
                # Materialize before the temporary file disappears.
                df: pl.DataFrame = FileConnector(path, sheet=self.sheet).read(con).pl()
            return con.from_arrow(df.to_arrow())
        if scheme in CLOUD_SCHEMES:
            try:
                df = _read_cloud(self.url, self.storage_options)
            except ConnectorError:
                raise
            except Exception as exc:  # polars/object_store raise many error types
                detail = str(exc).strip().splitlines()[0][:300]
                msg = f"Could not read {self.url}: {detail}"
                raise ConnectorError(msg) from exc
            return con.from_arrow(df.to_arrow())
        msg = f"Unsupported URL scheme {scheme or '(none)'!r}: use https://, s3://, gs:// or az://."
        raise ConnectorError(msg)
