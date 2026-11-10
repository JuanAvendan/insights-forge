"""Data-quality checks.

Each check is a small pure function ``(profile, config) -> Iterable[Issue]`` that
only reads the already-computed profile (no extra queries). Adding a new check
means writing one function and appending it to :data:`CHECKS`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from insights_forge.formatting import fmt_number
from insights_forge.profiling.config import ProfileConfig
from insights_forge.profiling.models import DatasetProfile, Issue, Severity

Check = Callable[[DatasetProfile, ProfileConfig], Iterable[Issue]]


def _pct(part: int, whole: int) -> float:
    return 100 * part / whole if whole else 0.0


def check_duplicate_rows(profile: DatasetProfile, _cfg: ProfileConfig) -> Iterable[Issue]:
    n = profile.duplicate_rows
    if n:
        yield Issue(
            check="duplicate_rows",
            severity=Severity.WARNING,
            message=f"{n:,} duplicate rows ({_pct(n, profile.rows):.1f}% of the data).",
            affected_rows=n,
        )


def check_missing_values(profile: DatasetProfile, cfg: ProfileConfig) -> Iterable[Issue]:
    for col in profile.columns:
        if col.nulls == 0:
            continue
        if col.count == 0:
            yield Issue(
                "empty_column",
                Severity.CRITICAL,
                "Column is completely empty.",
                col.name,
                col.nulls,
            )
            continue
        if col.null_pct >= cfg.missing_critical_pct:
            severity = Severity.CRITICAL
        elif col.null_pct >= cfg.missing_warning_pct:
            severity = Severity.WARNING
        else:
            severity = Severity.INFO
        yield Issue(
            "missing_values",
            severity,
            f"{col.nulls:,} missing values ({col.null_pct:.1f}%).",
            col.name,
            col.nulls,
        )


def check_constant_columns(profile: DatasetProfile, _cfg: ProfileConfig) -> Iterable[Issue]:
    if profile.rows < 2:
        return
    for col in profile.columns:
        if col.count > 0 and col.distinct == 1:
            yield Issue(
                "constant_column",
                Severity.WARNING,
                "Every non-missing value is identical; the column carries no information.",
                col.name,
            )


_ID_SNAKE = re.compile(r"(^|[_\s-])(id|uuid|guid|key|code)$|^id[_\s-]", re.IGNORECASE)
_ID_CAMEL = re.compile(r"[a-z](Id|ID|Uuid|Key|Code)$")  # case-sensitive: "paid" is not an ID


def looks_like_identifier(name: str) -> bool:
    """Heuristic on the column name: ``id``, ``order_id``, ``customerId``, ``sku_code``..."""
    return bool(_ID_SNAKE.search(name) or _ID_CAMEL.search(name))


def check_duplicate_identifiers(profile: DatasetProfile, _cfg: ProfileConfig) -> Iterable[Issue]:
    for col in profile.columns:
        if col.count == 0 or col.distinct == col.count or not looks_like_identifier(col.name):
            continue
        repeated = col.count - col.distinct
        yield Issue(
            "duplicate_identifiers",
            Severity.WARNING,
            f"Looks like an identifier but {repeated:,} values are repeated "
            f"({col.distinct:,} distinct out of {col.count:,}).",
            col.name,
            repeated,
        )


def check_outliers(profile: DatasetProfile, cfg: ProfileConfig) -> Iterable[Issue]:
    for col in profile.columns:
        stats = col.numeric
        if stats is None or stats.outliers == 0:
            continue
        if _pct(stats.outliers, col.count) > cfg.max_outlier_pct:
            continue  # heavy-tailed distribution, not isolated outliers
        yield Issue(
            "outliers",
            Severity.INFO,
            f"{stats.outliers:,} extreme values outside [{fmt_number(stats.lower_fence)}, "
            f"{fmt_number(stats.upper_fence)}] (IQR x {cfg.iqr_factor:g}). "
            f"Max is {fmt_number(stats.max)} vs. median {fmt_number(stats.median)}.",
            col.name,
            stats.outliers,
        )


def check_suspicious_negatives(profile: DatasetProfile, cfg: ProfileConfig) -> Iterable[Issue]:
    for col in profile.columns:
        stats = col.numeric
        if stats is None or stats.negatives == 0:
            continue
        if _pct(stats.negatives, col.count) <= cfg.suspicious_negative_pct:
            yield Issue(
                "suspicious_negatives",
                Severity.WARNING,
                f"{stats.negatives:,} negative values in an otherwise positive column "
                f"(min {fmt_number(stats.min)}). Possible data-entry errors.",
                col.name,
                stats.negatives,
            )


def check_text_values(profile: DatasetProfile, _cfg: ProfileConfig) -> Iterable[Issue]:
    for col in profile.columns:
        stats = col.text
        if stats is None:
            continue
        if stats.blank:
            yield Issue(
                "blank_text",
                Severity.WARNING,
                f"{stats.blank:,} blank values (empty or only spaces). Treat them as missing.",
                col.name,
                stats.blank,
            )
        if stats.padded:
            yield Issue(
                "padded_text",
                Severity.INFO,
                f"{stats.padded:,} values have leading/trailing spaces, which breaks "
                "grouping and joins. Trim them.",
                col.name,
                stats.padded,
            )
        real_values = col.count - stats.blank
        if real_values and stats.date_like == real_values:
            yield Issue(
                "dates_as_text",
                Severity.WARNING,
                "All values are dates stored as text. Convert to a DATE type.",
                col.name,
            )
        elif real_values and stats.numeric_like == real_values:
            yield Issue(
                "numbers_as_text",
                Severity.INFO,
                "All values look numeric but are stored as text. Fine for codes "
                "(IDs, zip codes); convert if they are quantities.",
                col.name,
            )


CHECKS: tuple[Check, ...] = (
    check_duplicate_rows,
    check_duplicate_identifiers,
    check_missing_values,
    check_constant_columns,
    check_suspicious_negatives,
    check_text_values,
    check_outliers,
)


def run_checks(profile: DatasetProfile, cfg: ProfileConfig) -> tuple[Issue, ...]:
    """Run every check and return issues, most severe first."""
    issues = [issue for check in CHECKS for issue in check(profile, cfg)]
    return tuple(sorted(issues, key=lambda i: -i.severity.rank))
