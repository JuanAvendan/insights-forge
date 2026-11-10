from __future__ import annotations

import pytest

from insights_forge.profiling import ColumnKind, ColumnProfile, Correlation, Severity
from tests.conftest import MakeProfile


@pytest.mark.parametrize(
    ("r", "strength"),
    [(0.95, "strong"), (-0.7, "strong"), (0.5, "moderate"), (-0.25, "weak"), (0.05, "negligible")],
)
def test_correlation_strength(r: float, strength: str) -> None:
    assert Correlation("a", "b", r).strength == strength


def test_severity_rank_order() -> None:
    assert Severity.INFO.rank < Severity.WARNING.rank < Severity.CRITICAL.rank


def test_percentages_handle_empty_columns() -> None:
    col = ColumnProfile(
        name="x", dtype="DOUBLE", kind=ColumnKind.NUMERIC, count=0, nulls=0, distinct=0
    )
    assert col.null_pct == 0.0
    assert col.distinct_pct == 0.0


def test_profile_filters(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT range AS n, 'x' || range AS s, NULL::INT AS empty FROM range(10)")
    assert [c.name for c in p.columns_of(ColumnKind.NUMERIC)] == ["n", "empty"]
    assert [c.name for c in p.columns_of(ColumnKind.TEXT)] == ["s"]
    assert p.column("s").distinct_pct == 100.0
    assert len(p.issues_by_severity(Severity.CRITICAL)) == 1
