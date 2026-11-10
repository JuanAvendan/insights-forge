"""Human-friendly number formatting shared by reports and the UI."""

from __future__ import annotations

import math


def fmt_number(x: float | None) -> str:
    """Format a number for humans: thousands separators, sensible precision, no
    scientific notation for everyday magnitudes.

    >>> fmt_number(1234567.891), fmt_number(286.93), fmt_number(0.0508), fmt_number(3.0)
    ('1,234,568', '286.9', '0.0508', '3')
    """
    if x is None or math.isnan(x):
        return "-"
    if math.isinf(x):
        return "∞" if x > 0 else "-∞"
    if x == int(x) or abs(x) >= 1000:
        return f"{x:,.0f}"
    if abs(x) >= 100:
        return f"{x:,.1f}"
    return f"{x:.3g}"


_SUFFIXES = ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K"))


def fmt_compact(x: float | None) -> str:
    """Short form for charts and tooltips: 7_100_000 -> '7.1M', 2.35e9 -> '2.35B'.

    >>> fmt_compact(7_100_000), fmt_compact(2_350_000_000), fmt_compact(950), fmt_compact(-1500)
    ('7.1M', '2.35B', '950', '-1.5K')
    """
    if x is None or math.isnan(x) or math.isinf(x):
        return fmt_number(x)
    for size, suffix in _SUFFIXES:
        if abs(x) >= size:
            return f"{x / size:.3g}{suffix}"
    return fmt_number(x)


def sentence_case(text: str) -> str:
    """Upper-case only the first letter ('budget_usd' -> 'Budget_usd', keeps 'totalUSD')."""
    return text[:1].upper() + text[1:]
