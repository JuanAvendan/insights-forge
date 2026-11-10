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
