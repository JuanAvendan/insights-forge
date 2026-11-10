<div align="center">

# 🔨 insights-forge

**Conecta cualquier fuente de datos y obtén reportes automáticos de calidad, perfilado e insights.**

[![CI](https://github.com/JuanAvendan/insights-forge/actions/workflows/ci.yml/badge.svg)](https://github.com/JuanAvendan/insights-forge/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2)](https://mypy-lang.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[🇺🇸 English](README.md) · 🇪🇸 Español

</div>

---

> 🚧 **En desarrollo.** El flujo completo funciona: archivos, bases de datos SQL y MongoDB, URLs, Google Sheets y almacenamiento en la nube → perfilado → calidad de datos → insights → reporte HTML. Mira la [hoja de ruta](#hoja-de-ruta).

## Por qué

Todo análisis empieza igual: cargar los datos, revisar qué está mal y entender su forma.
**insights-forge** automatiza esa primera hora. Apúntalo a un archivo, una base de datos SQL,
una colección NoSQL o un almacenamiento en la nube, y te dice qué hay y qué está mal.

## Funcionalidades

- **Muchas fuentes, un solo motor.** Cada fuente se carga en una tabla de [DuckDB](https://duckdb.org/) en memoria, así el código de análisis se escribe una vez y funciona con todas.
- **Carga inteligente de archivos.** CSV/TSV con detección automática de delimitador, encabezado y tipos, además de Excel (cualquier hoja), Parquet y JSON.
- **Bases de datos SQL.** PostgreSQL, MySQL y SQLite: una tabla, una vista o tu propio `SELECT`. Estrictamente de solo lectura (ver [Bases de datos](#bases-de-datos)).
- **NoSQL y nube.** Colecciones de MongoDB (documentos anidados aplanados), URLs https, enlaces de Google Sheets y almacenamiento S3 / GCS / Azure, con protección contra SSRF en las descargas.
- **Perfilado de columnas.** Estadísticas según el tipo de dato, calculadas con SQL dentro de DuckDB: cuartiles, media, desviación estándar, ceros, negativos, longitudes de texto, rangos de fechas y valores más frecuentes.
- **Chequeos de calidad de datos.** Cada hallazgo indica su severidad y cuántas filas afecta:

  | Chequeo | Ejemplo de hallazgo |
  |---|---|
  | Filas duplicadas | *12 filas duplicadas (1,2% de los datos)* |
  | Identificadores duplicados | *`order_id` parece un identificador, pero 12 valores se repiten* |
  | Valores faltantes / columnas vacías | *50 valores faltantes (4,9%)* |
  | Negativos sospechosos | *3 valores negativos en una columna que por lo demás es positiva* |
  | Outliers extremos (cercas IQR de Tukey) | *11 valores extremos fuera de [-11, 17]* |
  | Columnas constantes | *todos los valores son idénticos* |
  | Higiene de texto | textos vacíos, espacios sin recortar, números o fechas guardados como texto |

- **Correlaciones.** Matriz de Pearson entre columnas numéricas, relaciones relevantes, detección de columnas casi duplicadas y un explorador de dispersión con recta ajustada.
- **Comparaciones.** Ranking de grupos (top 15 + Other), evolución de los 5 principales en el tiempo y diagramas de caja para ver si una diferencia es típica o la producen los outliers.
- **App web interactiva.** Streamlit + Plotly: resumen, lista de problemas filtrable, explorador por columna (con zoom que oculta outliers) y mapa de calor de correlaciones. Los colores de los gráficos vienen de una paleta apta para daltonismo, con tonos propios para modo claro y oscuro.
- **Insights automáticos.** Hallazgos en lenguaje natural, ordenados por importancia. Por ejemplo:
  - *"Laptop representa el 66% del total. 2 de 7 productos suman el 80%."*
  - *"Mayo 2025 fue el mejor mes (+228%). Una sola fila aporta el 88% de ese mes: revisa que no sea un outlier."*
  - *"Las filas duplicadas inflan el total un 5,2%."*

  El motor detecta automáticamente las columnas de **medida**, **fecha** y **dimensión** (con nombres en inglés y en español) y decide si sumar o promediar según el significado. Cubre tendencias (ajuste por mínimos cuadrados con R², ignorando períodos finales incompletos), picos, concentración y Pareto, diferencias entre segmentos, sesgo, correlaciones y el impacto de los duplicados.
- **Reporte HTML para compartir.** Un solo archivo autocontenido con insights, gráfico de tendencia, tabla de calidad, resumen de columnas y correlaciones, en modo claro y oscuro. Usa `--offline` para incrustar plotly.js.
- **Línea de comandos.** `insights-forge summary` e `insights-forge report`.

## Arquitectura

```mermaid
flowchart LR
    subgraph Fuentes
        F[Archivos<br/>CSV · Excel · Parquet · JSON]
        S[SQL<br/>PostgreSQL · MySQL · SQLite]
        N[NoSQL<br/>MongoDB]
        C[Nube<br/>S3 · GCS · Google Sheets]
    end
    F & S & N & C --> K[Conectores]
    K --> D[(DuckDB<br/>en memoria)]
    D --> P[Perfilado y<br/>calidad de datos]
    P --> I[Insights]
    I --> UI[App Streamlit]
    I --> R[Reporte HTML]
```

Cada conector implementa un único método, `read(con) -> DuckDBPyRelation`. La clase base
materializa el resultado en una tabla de DuckDB, de modo que los datos siguen disponibles aunque
se borre un archivo temporal o se cierre la conexión a una base de datos.

## Inicio rápido

Requiere [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/JuanAvendan/insights-forge.git
cd insights-forge
uv sync
uv run streamlit run src/insights_forge/app/streamlit_app.py
```

Luego sube un archivo, o haz clic en **Try with sample data** en la barra lateral.

### Desde la línea de comandos

```bash
uv run insights-forge summary data/sample/sales.csv              # problemas + insights en la terminal
uv run insights-forge report data/sample/sales.csv -o reporte.html
uv run insights-forge report ventas.xlsx --sheet Datos --measure ingresos --date-column fecha
```

### Bases de datos

Levanta las bases de prueba locales (PostgreSQL 17 y MySQL 8.4 con los datos de ejemplo):

```bash
docker compose up -d --wait
```

```bash
export INSIGHTS_FORGE_DB_URL="postgresql+psycopg://analyst:analyst@localhost:55432/shop"
uv run insights-forge tables
uv run insights-forge summary --table sales
uv run insights-forge report --query "SELECT * FROM sales WHERE channel = 'Online'"
uv run insights-forge summary tienda.db --table pedidos      # un archivo SQLite
```

En la app, elige **Database** en la barra lateral, o sube un archivo `.db`/`.sqlite`.
MySQL funciona igual con `mysql+pymysql://analyst:analyst@localhost:53306/shop`.
Fuera de este repo, instala el driver que necesites: `pip install "insights-forge[postgres]"`
o `"insights-forge[mysql]"`.

**Solo lectura por diseño.** La herramienta nunca escribe en tu base de datos, y lo garantiza en tres capas:

1. Sesiones de solo lectura: `SET TRANSACTION READ ONLY` (PostgreSQL, MySQL); los archivos SQLite se abren con `mode=ro` y `PRAGMA query_only`.
2. El SQL propio debe ser una sola sentencia `SELECT`/`WITH`, y se ejecuta envuelto en `SELECT * FROM (...) LIMIT n`.
3. Los nombres de tabla se validan contra el catálogo de la base de datos y SQLAlchemy los escapa.

Las contraseñas se enmascaran (`***`) en cualquier lugar donde podrían mostrarse, la CLI lee
la URL de `INSIGHTS_FORGE_DB_URL` para que no quede en el historial de la terminal, las
conexiones expiran a los 10 segundos y las lecturas se limitan a 1.000.000 de filas por
defecto. Buena práctica: usa un usuario que solo pueda hacer `SELECT`, como el usuario
`analyst` de `compose.yaml`.

### MongoDB

```bash
export INSIGHTS_FORGE_DB_URL="mongodb://analyst:analyst@localhost:57017/shop"
uv run insights-forge tables
uv run insights-forge summary --collection sales --filter '{"channel": "Online"}'
```

Los documentos anidados se aplanan en columnas (`customer.email`, `product.name`), y `_id`
se deja fuera para que los documentos duplicados sigan siendo visibles. Solo se usa
`find()`, y se rechazan los filtros que ejecutan JavaScript en el servidor (`$where`,
`$function`).

### Archivos remotos: https, Google Sheets, S3 / GCS / Azure

```bash
uv run insights-forge summary https://raw.githubusercontent.com/JuanAvendan/insights-forge/main/data/sample/sales.csv
uv run insights-forge report "https://docs.google.com/spreadsheets/d/<id>/edit#gid=0"
uv run insights-forge summary s3://mi-bucket/exports/ventas.parquet
```

- **Google Sheets**: pega el enlace de una hoja compartida como "cualquier persona con el enlace".
- **Almacenamiento en la nube**: lo lee Polars; las credenciales salen de las variables de
  entorno habituales (`AWS_ACCESS_KEY_ID`, `AWS_ENDPOINT_URL`, `GOOGLE_APPLICATION_CREDENTIALS`,
  `AZURE_STORAGE_ACCOUNT_NAME`...). El gateway S3 local de `compose.yaml` sirve
  `s3://shop/sales.csv` (su configuración está en el comentario al inicio del archivo).
- **Protección contra SSRF**: la app nunca descarga URLs que resuelvan a direcciones
  privadas, de loopback o link-local (como el endpoint de metadatos de la nube
  `169.254.169.254`), revisa cada redirección y limita las descargas a 200 MB y 30 segundos.

En la app, elige **URL** en la barra lateral, o MongoDB en el formulario de **Database**.

### Como librería

```python
from pathlib import Path

import duckdb
from insights_forge.connectors import FileConnector

from insights_forge.insights import generate_insights
from insights_forge.profiling import profile_table

con = duckdb.connect()
FileConnector(Path("data/sample/sales.csv")).load(con, table_name="sales")

perfil = profile_table(con, "sales")
print(f"{perfil.rows:,} filas, {perfil.completeness_pct:.1f}% completo")
for issue in perfil.issues:
    print(f"[{issue.severity}] {issue.column or 'tabla'}: {issue.message}")

reporte = generate_insights(con, perfil)  # o measure="quantity", date="order_date"
for insight in reporte.insights[:3]:
    print(insight.title)
```

O todo el flujo en una sola llamada: `insights_forge.pipeline.analyze_file(ruta)`.

## Hoja de ruta

- [x] **v0.1: Fundamentos.** Configuración del proyecto, CI, arquitectura de conectores, conector de archivos, UI básica
- [x] **v0.2: Perfilado.** Estadísticas por columna, chequeos de calidad, distribuciones y correlaciones
- [x] **v0.3: Insights.** Hallazgos por reglas en lenguaje natural, tendencias temporales, reporte HTML exportable, CLI
- [x] **v0.4: Comparaciones y robustez con datos reales.** Pestaña Compare, explorador de dispersión, gráficos de cobertura; datos de panel, años enteros y medidas incompletas bien manejados
- [ ] **Demo en vivo** en Streamlit Community Cloud
- [x] **v0.5: Fuentes SQL.** PostgreSQL, MySQL, SQLite; solo lectura por diseño; bases de prueba con Docker Compose
- [x] **v0.6: NoSQL y nube.** MongoDB, URLs https, Google Sheets, S3 / GCS / Azure; protección contra SSRF
- [ ] **v0.7: Insights con IA.** Resúmenes opcionales del perfil generados por un LLM

## Stack tecnológico

| Área | Herramientas |
|---|---|
| Motor de datos | DuckDB, Polars, PyArrow, fastexcel |
| Bases de datos y nube | SQLAlchemy 2, psycopg 3, PyMySQL, PyMongo, Polars object_store, Docker Compose |
| App, gráficos y reporte | Streamlit, Plotly, Jinja2 |
| Herramientas | uv, Ruff, mypy (estricto), pytest + coverage, pre-commit |
| CI | GitHub Actions (Linux + Windows, Python 3.12 / 3.13) |

## Desarrollo

```bash
uv sync                          # instala todo, incluidas las herramientas de desarrollo
uv run pre-commit install        # lint, formato y tipos en cada commit
uv run pytest                    # tests + cobertura
uv run ruff check . && uv run mypy

# tests de integración contra PostgreSQL y MySQL reales
docker compose up -d --wait
export INSIGHTS_FORGE_TEST_POSTGRES_URL="postgresql+psycopg://analyst:analyst@localhost:55432/shop"
export INSIGHTS_FORGE_TEST_MYSQL_URL="mysql+pymysql://analyst:analyst@localhost:53306/shop"
export INSIGHTS_FORGE_TEST_MONGO_URL="mongodb://analyst:analyst@localhost:57017/shop"
# para S3 define también INSIGHTS_FORGE_TEST_S3_URL=s3://shop/sales.csv y los valores AWS_* de compose.yaml
uv run pytest -m integration
```

### Estructura del proyecto

```
insights-forge/
├── src/insights_forge/
│   ├── connectors/       # un módulo por tipo de fuente
│   ├── profiling/        # estadísticas (SQL en DuckDB) y chequeos de calidad
│   ├── insights/         # detección de roles y generadores de insights por reglas
│   ├── report/           # reporte HTML con Jinja2
│   ├── app/              # interfaz Streamlit
│   ├── charts.py         # gráficos Plotly compartidos por la app y el reporte
│   ├── pipeline.py       # cargar -> perfilar -> insights en una llamada
│   └── cli.py            # comando `insights-forge`
├── tests/
├── scripts/              # utilidades (p. ej. generador de datos de ejemplo)
├── docker/               # scripts de carga de las bases de prueba
├── compose.yaml          # PostgreSQL, MySQL, MongoDB y S3 para desarrollo y CI
└── data/sample/          # datasets sintéticos pequeños (los datos reales se ignoran en git)
```

## Licencia

[MIT](LICENSE) © Juan Avendaño
