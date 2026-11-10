# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/).

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

[0.2.0]: https://github.com/JuanAvendan/insights-forge/compare/163596d...HEAD
[0.1.0]: https://github.com/JuanAvendan/insights-forge/commit/163596d
