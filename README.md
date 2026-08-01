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
* Writes go through Datasette's serialised write connection, in batches

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
  --internal internal.db \
  --port 8001 \
  --root \
  --reload
```

No `--template-dir` or `--static` flags are needed. Datasette discovers `datasette_open_data/templates/` and mounts `datasette_open_data/static/` at `/-/static-plugins/datasette-open-data/` automatically once the plugin is installed.

```text
http://127.0.0.1:8001/-/open-data
```

---

## Available Routes

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/-/open-data` | Open Data explorer homepage |
| GET | `/-/open-data/search?q=mortgage` | Search datasets |
| GET | `/-/open-data/dataset/{id}` | Dataset metadata and resources |
| GET | `/-/open-data/resource/{id}/preview` | Preview resource records |
| **POST** | `/-/open-data/resource/{id}/load` | Load resource into `data.db` |
| GET | `/-/open-data/groups` | Browse groups / themes |
| GET | `/-/open-data/organizations` | Browse organizations |
| GET | `/-/open-data/tags` | Browse tags / subjects |

---

## Permissions

Loading a resource creates a table in `data.db` and inserts rows into it, so it is a write. Two things guard it:

* It requires **POST**. A GET returns 405. Datasette's CSRF protection treats GET, HEAD and OPTIONS as safe methods, so a load endpoint reachable by GET would be triggerable cross-site by anything that makes the browser issue a request — an `<img>` tag, a link prefetcher, a crawler.
* It requires the **`insert-row`** permission on the `data` database. This is Datasette's own permission rather than a bespoke one, so existing `allow` blocks, API tokens and auth plugins govern it with no extra configuration.

`insert-row` is not allowed by default, so an anonymous instance can browse and preview but not load. To grant it:

```bash
# Simplest for local use: sign in as root via the URL printed at startup
uv run datasette serve data.db catalog.db -m metadata.yml --root
```

Or grant it explicitly in your configuration:

```yaml
databases:
  data:
    permissions:
      insert-row:
        id: alice
```

The Load button is hidden from actors who lack the permission, and the `load_open_data_resource` agent tool enforces the same check — it writes directly rather than going through the route, so it would otherwise be a way around it.

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

The CLI has no Datasette instance to route through, so it writes straight to the file. Don't point it at a database a running Datasette is also writing to — see [How loads write](#how-loads-write).

---

## How loads write

Datasette serialises every write to a database onto a single thread. Loads go through it rather than opening a connection of their own:

| Caller | Write target | Why |
|--------|--------------|-----|
| Web route, agent tool | `Database.execute_write_fn()` | Shares Datasette's write connection |
| `open-data-load` CLI | The SQLite file directly | No Datasette instance exists |

Routing through `execute_write_fn` avoids `SQLITE_BUSY` against Datasette's own writes, keeps newly created tables visible to the instance without a restart, and picks up its transaction handling and event tracking. `sqlite_utils` issues no commits of its own, so it composes with that transaction rather than fighting it — which is what lets loads keep using `insert_all(alter=True)` for schema creation and incremental column discovery.

Rows are written in batches of 5,000. The write thread is shared with the rest of the instance, so a 50,000-row load is a sequence of short writes instead of one long one that would block other writes until it finished.

`load_resource()` accepts a Datasette database, a path, or any object with an `insert_rows()` method, resolved by `resolve_writer()`.

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

Every tool returns structured JSON and reports failures as `{"error": ...}` rather than raising, so a failed portal call does not end the agent's turn. `load_open_data_resource` enforces the same `insert-row` permission as the web route — see [Permissions](#permissions).

---

## Project Structure

```text
datasette-open-data/
├── datasette_open_data/
│   ├── __init__.py          # Datasette hooks and route registration
│   ├── models.py            # Resource, DatasetSummary, Dataset dataclasses
│   ├── registry.py          # Provider instantiation from config
│   ├── loader.py            # DataStore and CSV loading; write targets
│   ├── views.py             # Route handlers
│   ├── agent_tools.py       # LLM agent tool definitions
│   ├── cli.py               # CLI entry point
│   ├── providers/
│   │   ├── base.py          # OpenDataProvider protocol
│   │   ├── ckan.py          # CKAN provider
│   │   ├── socrata.py       # Socrata / SODA provider
│   │   └── pxstat.py        # PxStat provider (CSO Ireland)
│   ├── templates/           # open_data_base.html is namespaced on purpose so
│   │                        # it cannot shadow Datasette's own base.html
│   └── static/              # Mounted at /-/static-plugins/datasette-open-data/
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
│   ├── test_loader_writers.py    # Write targets and batching
│   ├── test_registry.py
│   ├── test_views.py             # Pure helpers
│   └── test_view_handlers.py     # Route handlers and error paths
├── .github/
│   └── workflows/
│       └── ci.yml           # CI: Python 3.10 / 3.11 / 3.12
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
