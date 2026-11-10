# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

Fixes found by running the tool on a real 26k-row movies dataset (1896-2027).

### Added
- Aggregation override (`Auto` / `Sum` / `Average`) in the app and `--aggregation`
  in the CLI.
- Yearly granularity for histories longer than ten years.
- Trend insights warn when the measure is mostly missing (totals then track data
  coverage, not the quantity itself).

### Fixed
- Measure auto-detection works on the words of a column name: `aspect_ratio_value`
  is no longer picked as a summed amount, `box_office_usd` / `budget_usd` rank
  first, and calendar columns such as `release_year` are never measures.
- Identical insights from equivalent columns (e.g. `color` and `color_type`) are
  shown once.
- Trend chart tooltips use compact numbers (`7.1M`, not `7.1e+6`) and the fitted
  line no longer dips below zero for non-negative measures.
- Very large changes read as multipliers (`1,437x`) instead of `+143564%`.
- Titles keep the column name's casing.

## [0.3.0] - 2026-11-10

### Added
- `insights` package: automatic role detection (measure, date, dimensions; English
  and Spanish column names; sum vs. average by meaning) and ranked plain-language
  insights: trend (least-squares fit with R², incomplete trailing periods dropped),
  peak/trough with single-row dominance warning, concentration with Pareto,
  segment gaps, skew, strong correlations and duplicate impact on totals.
- Self-contained HTML report (Jinja2, light/dark, mobile-friendly) with an
  `--offline` option that embeds plotly.js.
- `insights-forge` CLI with `summary` and `report` commands.
- `pipeline` module: load, profile and generate insights in one call.
- App: new **Insights** tab with measure/date pickers, trend chart, insight cards and
  report download.

### Changed
- Chart builders moved from `insights_forge.app.charts` to `insights_forge.charts`
  so the report does not depend on the UI.

## [0.2.0] - 2026-11-10

### Added
- `profiling` package: `profile_table()` computes a typed `DatasetProfile` with
  per-column statistics (numeric, text, temporal, boolean) pushed down to DuckDB SQL.
- Data-quality checks with severity levels: duplicate rows, duplicate identifiers,
  missing values, empty and constant columns, suspicious negatives, extreme outliers
  (Tukey IQR fences), blank/untrimmed text, numbers and dates stored as text.
- Pearson correlation matrix across numeric columns.
- App: Overview, Data quality, Columns, Correlations and Preview tabs; colorblind-safe
  Plotly charts with light/dark variants; outlier-hiding zoom on distributions.
- `.streamlit/config.toml` with the palette accent color and usage stats disabled.

### Fixed
- Fully consume DuckDB results so the connection stays usable on DuckDB 2.0.
- The sample dataset stays loaded across reruns (kept in session state).

## [0.1.0] - 2026-11-10

### Added
- Project foundations: uv, Ruff, strict mypy, pytest + coverage, pre-commit, CI.
- Connector architecture and `FileConnector` (CSV/TSV, Excel, Parquet, JSON).
- Minimal Streamlit app and a synthetic sample dataset.

[0.3.0]: https://github.com/JuanAvendan/insights-forge/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/JuanAvendan/insights-forge/compare/163596d...v0.2.0
[0.1.0]: https://github.com/JuanAvendan/insights-forge/commit/163596d
