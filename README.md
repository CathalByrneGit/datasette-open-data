# datasette-open-data

A provider-based Datasette plugin for discovering, cataloging, searching and loading datasets from open data portals.

`datasette-open-data` combines:

* Live provider APIs (CKAN, Socrata, PxStat)
* A local SQLite metadata catalog
* Datasette browsing and search
* Resource loading into SQLite
* Agent tooling for dataset discovery and analysis

Supported portals out of the box:

* **Central Bank of Ireland** — CKAN
* **data.gov.ie** — CKAN
* **CSO Ireland** — PxStat

Any CKAN, Socrata, or PxStat portal can be added with two lines in `providers.yml`.

---

## Why this exists

Most open data portals provide APIs for searching and downloading datasets, but they do not provide a unified experience for:

```text
discover dataset
      ↓
inspect metadata
      ↓
preview resources
      ↓
load into SQLite
      ↓
query with Datasette
      ↓
analyze with agents
```

`datasette-open-data` aims to become a generic open-data layer for Datasette.

---

## Architecture

The project is built around two SQLite databases:

### catalog.db

A local metadata warehouse generated from provider APIs.

Contains:

* Providers
* Datasets
* Resources
* Organizations
* Groups / Themes
* Tags / Subjects
* FTS search index

Purpose:

```text
Discovery
Search
Navigation
Catalog browsing
```

### data.db

Contains datasets loaded from open data portals.

Purpose:

```text
Analysis
SQL queries
Charts
Agent workflows
```

---

## Features

### Catalog

* Multi-provider support (CKAN, Socrata, PxStat)
* Local metadata warehouse
* Full-text search (FTS5)
* Browse by organization
* Browse by group / theme
* Browse by tag / subject
* Recently updated datasets

### CKAN

* Package search
* Dataset metadata
* Organizations
* Groups
* Tags
* DataStore preview

### Socrata

* Catalog search via Discovery API
* Dataset metadata via Views API
* SODA data preview
* Category and tag browsing
* CSV export for loading

### PxStat

* Table catalog (`ReadCollection`)
* Dataset metadata (`ReadMetadata`)
* Theme and subject navigation
* CSV data preview and loading
* Language-configurable (default: English)

### Resource Loading

* CKAN DataStore resources (paginated)
* CSV resources (via HTTP download)
* Socrata SODA CSV exports
* PxStat CSV exports
* Automatic format detection
* Automatic schema creation
* Incremental column discovery
* `LoadError` for clean error reporting
* Writes run off the event loop, so large loads don't block the server

### Datasette

* Open Data homepage
* Search interface
* Dataset pages
* Resource previews
* Resource loading
* Catalog browsing

### Agents

* Search catalog (falls back to live provider search when `catalog.db` is absent)
* Inspect datasets
* Load resources
* Inspect loaded tables
* Sample data
* Suggest joins between loaded tables

---

## Installation

Install dependencies:

```bash
uv sync
```

Development install:

```bash
uv pip install -e .
```

---

## Configuration

Provider definitions live in `providers.yml`. Three provider types are supported.

### CKAN

```yaml
providers:
  centralbank:
    type: ckan
    title: Central Bank of Ireland Open Data Portal
    base_url: https://opendata.centralbank.ie
    api_base_url: https://opendata.centralbank.ie/api/3
    datastore_api_base_url: https://opendata.centralbank.ie/en_GB/api/3

  datagovie:
    type: ckan
    title: data.gov.ie
    base_url: https://data.gov.ie
    api_base_url: https://data.gov.ie/api/3
```

`api_base_url` and `datastore_api_base_url` are optional; they default to `{base_url}/api/3`.

### PxStat

```yaml
providers:
  cso:
    type: pxstat
    title: Central Statistics Office Ireland
    base_url: https://ws.cso.ie
    language: en   # optional, defaults to en
```

Derives `{base_url}/public/api.jsonrpc` for metadata and `{base_url}/public/api.restful` for CSV downloads automatically.

**Note:** live PxStat `search()` fetches the full table catalog (~12,600 tables for CSO) and filters in-memory. Build `catalog.db` to get fast FTS search instead — see below.

### Socrata

```yaml
providers:
  nycopendata:
    type: socrata
    title: NYC Open Data
    base_url: https://data.cityofnewyork.us
```

Uses the Socrata Discovery API for search and SODA for data preview and CSV export.

---

## Building the Catalog

Generate catalog metadata for any configured provider, regardless of type:

```bash
uv run python scripts/build_catalog.py --provider centralbank
uv run python scripts/build_catalog.py --provider cso
uv run python scripts/build_catalog.py --provider datagovie --limit 500
uv run python scripts/build_catalog.py --all
```

This creates `catalog.db` and enables fast FTS search. Without it, search falls back to live provider APIs.

Each provider implements `iter_catalog()`, which yields records in the CKAN package shape that `catalog.db` stores. How it is sourced differs per type:

| Type | Crawl strategy |
|------|----------------|
| `ckan` | Pages `package_search`, then `package_show` per dataset |
| `socrata` | Pages the Discovery API; metadata comes back inline |
| `pxstat` | One `ReadCollection` call for all tables, plus `Navigation_API.Read` for themes |

PxStat deliberately does not fetch per-table notes during the crawl — that would be one `ReadMetadata` call per table (~12,600 for CSO). Notes are filled in on demand when you open a dataset page.

To add a new provider type, implement `iter_catalog()` alongside the rest of the `OpenDataProvider` protocol and catalog building works with no changes to the script.

---

## Creating a Data Database

Create an empty database for imported datasets:

```bash
uv run python scripts/create_db.py
```

This creates `data.db`.

---

## Running Datasette

```bash
uv run datasette serve data.db catalog.db \
  -m metadata.yml \
  --template-dir datasette_open_data/templates \
  --static static:static \
  --internal internal.db \
  --port 8001 \
  --root \
  --reload
```

```text
http://127.0.0.1:8001/-/open-data
```

---

## Available Routes

| Route | Description |
|-------|-------------|
| `/-/open-data` | Open Data explorer homepage |
| `/-/open-data/search?q=mortgage` | Search datasets |
| `/-/open-data/dataset/{id}` | Dataset metadata and resources |
| `/-/open-data/resource/{id}/preview` | Preview resource records |
| `/-/open-data/resource/{id}/load` | Load resource into `data.db` |
| `/-/open-data/groups` | Browse groups / themes |
| `/-/open-data/organizations` | Browse organizations |
| `/-/open-data/tags` | Browse tags / subjects |

---

## CLI Loading

Load a resource by provider:

```bash
open-data-load \
  --provider centralbank \
  --resource-id RESOURCE_ID \
  --database data.db \
  --table my_table
```

Load a CSV directly:

```bash
open-data-load \
  --csv-url https://example.com/file.csv \
  --database data.db \
  --table my_table
```

---

## Agent Tools

The plugin exposes eight agent tools:

| Tool | Purpose |
|------|---------|
| `list_open_data_providers` | Discover configured provider names |
| `search_open_data_catalog` | Keyword search via FTS, falling back to live provider search |
| `show_open_data_dataset` | Dataset metadata plus per-resource preview/load URLs |
| `load_open_data_resource` | Load a resource into `data.db` |
| `list_loaded_open_data_tables` | List tables already loaded |
| `describe_loaded_open_data_table` | Column names and types |
| `sample_loaded_open_data_table` | Sample rows |
| `suggest_open_data_joins` | Find join keys across loaded tables by column name and Jaccard overlap |

Every tool returns structured JSON and reports failures as `{"error": ...}` rather than raising, so a failed portal call does not end the agent's turn.

---

## Project Structure

```text
datasette-open-data/
├── datasette_open_data/
│   ├── __init__.py          # Datasette hooks and route registration
│   ├── models.py            # Resource, DatasetSummary, Dataset dataclasses
│   ├── registry.py          # Provider instantiation from config
│   ├── loader.py            # DataStore and CSV loading (LoadError)
│   ├── views.py             # Route handlers
│   ├── agent_tools.py       # LLM agent tool definitions
│   ├── cli.py               # CLI entry point
│   └── providers/
│       ├── base.py          # OpenDataProvider protocol
│       ├── ckan.py          # CKAN provider
│       ├── socrata.py       # Socrata / SODA provider
│       └── pxstat.py        # PxStat provider (CSO Ireland)
├── scripts/
│   ├── build_catalog.py     # Populate catalog.db from provider APIs
│   └── create_db.py         # Create empty data.db
├── tests/
│   ├── conftest.py               # Fake Datasette / Database / Request
│   ├── test_ckan_provider.py
│   ├── test_socrata_provider.py
│   ├── test_pxstat_provider.py
│   ├── test_iter_catalog.py      # Catalog crawl for all three provider types
│   ├── test_build_catalog.py     # End-to-end catalog builds
│   ├── test_agent_tools.py
│   ├── test_loader.py
│   ├── test_registry.py
│   ├── test_views.py             # Pure helpers
│   └── test_view_handlers.py     # Route handlers and error paths
├── .github/
│   └── workflows/
│       └── ci.yml           # CI: Python 3.10 / 3.11 / 3.12
├── templates/
├── static/
├── providers.yml
└── metadata.yml
```

---

## Testing

```bash
uv sync --group dev
uv run pytest tests/ -v
```

Linting and formatting:

```bash
uv run ruff check .
uv run ruff format .
```

CI runs the suite across Python 3.10, 3.11, and 3.12, plus a `ruff check` / `ruff format --check` job, on every push and pull request.

---

## Providers

### Implemented

| Type | Example portals |
|------|----------------|
| `ckan` | Central Bank of Ireland, data.gov.ie, any CKAN portal |
| `socrata` | NYC Open Data, Chicago Data Portal, any Socrata portal |
| `pxstat` | CSO Ireland, any PxStat-compatible portal |

### Planned

* PxWeb (Statistics Sweden, Statistics Finland, Statistics Norway — different API to PxStat)
* ArcGIS Hub
* Generic DCAT/JSON-LD catalogs

---

## Roadmap

### Near Term

* Richer resource previews
* Incremental catalog refresh (currently each build resets the provider's rows)
* Surface catalog build status in the UI

### Future

* PxWeb provider (Nordic statistical offices)
* XLSX loading
* JSON-stat loading
* Geospatial normalization
* Background imports
* Scheduled catalog refresh
* Hybrid semantic search

---

## License

MIT
