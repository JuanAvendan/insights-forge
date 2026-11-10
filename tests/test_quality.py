from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from insights_forge.connectors import FileConnector
from insights_forge.profiling import DatasetProfile, Issue, Severity, profile_table
from insights_forge.profiling.quality import looks_like_identifier
from tests.conftest import MakeProfile

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "sales.csv"


def _issues(p: DatasetProfile, check: str) -> list[Issue]:
    return [i for i in p.issues if i.check == check]


def test_clean_data_has_no_issues(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT range AS id, range * 1.5 AS amount FROM range(1, 101)")
    assert p.issues == ()


def test_duplicate_rows(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT * FROM (VALUES (1), (1), (1), (2)) t(x)")
    [issue] = _issues(p, "duplicate_rows")
    assert issue.affected_rows == 2
    assert issue.severity is Severity.WARNING


@pytest.mark.parametrize(
    ("nulls", "severity"),
    [(1, Severity.INFO), (10, Severity.WARNING), (60, Severity.CRITICAL)],
)
def test_missing_values_severity(make_profile: MakeProfile, nulls: int, severity: Severity) -> None:
    p = make_profile(
        f"SELECT CASE WHEN range < {nulls} THEN NULL ELSE range END AS v FROM range(100)"
    )
    [issue] = _issues(p, "missing_values")
    assert issue.severity is severity
    assert issue.affected_rows == nulls


def test_empty_column_is_critical(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT range AS id, NULL::VARCHAR AS notes FROM range(5)")
    [issue] = _issues(p, "empty_column")
    assert issue.column == "notes"
    assert issue.severity is Severity.CRITICAL
    assert not _issues(p, "missing_values")


def test_constant_column(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT range AS x, 'CO' AS country FROM range(5)")
    [issue] = _issues(p, "constant_column")
    assert issue.column == "country"


def test_suspicious_negatives(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT CASE WHEN range = 0 THEN -10 ELSE range END AS price FROM range(100)")
    [issue] = _issues(p, "suspicious_negatives")
    assert issue.affected_rows == 1


def test_many_negatives_are_not_suspicious(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT range - 50 AS delta FROM range(100)")
    assert not _issues(p, "suspicious_negatives")


def test_isolated_outliers_reported(make_profile: MakeProfile) -> None:
    p = make_profile(
        "SELECT CASE WHEN range = 0 THEN 10000 ELSE range % 10 END AS qty FROM range(100)"
    )
    [issue] = _issues(p, "outliers")
    assert issue.affected_rows == 1
    assert issue.severity is Severity.INFO


def test_text_issues(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT unnest(['ok', ' padded', '   ', 'fine']) AS v")
    assert _issues(p, "blank_text")[0].affected_rows == 1
    assert _issues(p, "padded_text")[0].affected_rows == 1


def test_numbers_and_dates_stored_as_text(make_profile: MakeProfile) -> None:
    p = make_profile(
        "SELECT unnest(['001', '002', '010']) AS zip, "
        "unnest(['2025-01-01', '2025-02-01', '2025-03-01']) AS day"
    )
    assert _issues(p, "numbers_as_text")[0].column == "zip"
    assert _issues(p, "dates_as_text")[0].column == "day"


@pytest.mark.parametrize(
    "name", ["id", "ID", "order_id", "id_cliente", "customerId", "sku code", "uuid", "user-key"]
)
def test_identifier_names_detected(name: str) -> None:
    assert looks_like_identifier(name)


@pytest.mark.parametrize("name", ["paid", "valid", "idea", "region", "codex", "monkey"])
def test_non_identifier_names_ignored(name: str) -> None:
    assert not looks_like_identifier(name)


def test_duplicate_identifiers(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT unnest([1, 2, 2, 3]) AS order_id, unnest([1, 1, 1, 1]) AS qty")
    [issue] = _issues(p, "duplicate_identifiers")
    assert issue.column == "order_id"
    assert issue.affected_rows == 1


def test_issues_sorted_by_severity(make_profile: MakeProfile) -> None:
    p = make_profile(
        "SELECT CASE WHEN range < 2 THEN NULL ELSE range END AS a, NULL::INT AS b FROM range(100)"
    )
    ranks = [i.severity.rank for i in p.issues]
    assert ranks == sorted(ranks, reverse=True)
    assert p.issues[0].severity is Severity.CRITICAL


def test_sample_dataset_issues_are_detected(con: duckdb.DuckDBPyConnection) -> None:
    """The sample data has known, intentionally injected problems."""
    FileConnector(SAMPLE).load(con)
    p = profile_table(con)
    found = {(i.check, i.column) for i in p.issues}
    assert ("duplicate_rows", None) in found
    assert ("duplicate_identifiers", "order_id") in found
    assert ("missing_values", "customer_email") in found
    assert ("missing_values", "region") in found
    assert ("suspicious_negatives", "unit_price") in found
    assert ("outliers", "quantity") in found
    assert p.duplicate_rows == 12
