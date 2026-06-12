# datasette-open-data

A provider-based Datasette plugin for discovering, cataloging, searching and loading datasets from open data portals.

`datasette-open-data` combines:

* Live provider APIs (currently CKAN)
* A local SQLite metadata catalog
* Datasette browsing and search
* Resource loading into SQLite
* Agent tooling for dataset discovery and analysis

The project currently supports CKAN portals including:

* Central Bank of Ireland Open Data Portal
* data.gov.ie

with future support planned for:

* PxWeb / PxStat
* Socrata
* ArcGIS Hub
* Other open-data ecosystems

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
* Groups
* Tags
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

* Multi-provider support
* Local metadata warehouse
* Full-text search (FTS5)
* Browse by organization
* Browse by group
* Browse by tag
* Recently updated datasets

### CKAN

* Package search
* Dataset metadata
* Organizations
* Groups
* Tags
* DataStore preview

### Resource Loading

* CKAN DataStore resources
* CSV resources
* Automatic format detection
* Automatic schema creation
* Incremental column discovery

### Datasette

* Open Data homepage
* Search interface
* Dataset pages
* Resource previews
* Resource loading
* Catalog browsing

### Agents

* Search catalog
* Inspect datasets
* Load resources
* Inspect loaded tables
* Sample data

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

### providers.yml

Provider definitions live in:

```text
providers.yml
```

Example:

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

---

## Building the Catalog

Generate catalog metadata:

```bash
uv run python scripts/build_catalog.py --provider centralbank
```

Add data.gov.ie:

```bash
uv run python scripts/build_catalog.py --provider datagovie --limit 500
```

Build both:

```bash
uv run python scripts/build_catalog.py --provider centralbank
uv run python scripts/build_catalog.py --provider datagovie --limit 500
```

This creates:

```text
catalog.db
```

---

## Creating a Data Database

Create an empty database for imported datasets:

```bash
uv run python scripts/create_db.py
```

This creates:

```text
data.db
```

---

## Running Datasette

Start Datasette with both databases:

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

### Homepage

```text
/-/open-data
```

Open Data explorer.

### Search

```text
/-/open-data/search?q=mortgage
```

Search datasets.

### Dataset

```text
/-/open-data/dataset/{dataset_id}
```

View dataset metadata and resources.

### Resource Preview

```text
/-/open-data/resource/{resource_id}/preview
```

Preview DataStore records.

### Resource Load

```text
/-/open-data/resource/{resource_id}/load
```

Load a resource into `data.db`.

---

## CLI Loading

Load a CKAN resource:

```bash
open-data-load \
  --provider centralbank \
  --resource-id RESOURCE_ID \
  --database data.db \
  --table my_table
```

Load a CSV:

```bash
open-data-load \
  --csv-url https://example.com/file.csv \
  --database data.db \
  --table my_table
```

---

## Agent Tools

The plugin exposes agent tools for:

* Listing providers
* Searching the catalog
* Inspecting datasets
* Loading resources
* Listing loaded tables
* Describing tables
* Sampling rows

This enables workflows such as:

```text
Find mortgage datasets
        ↓
Inspect metadata
        ↓
Load dataset
        ↓
Query data
        ↓
Analyze results
```

---

## Project Structure

```text
datasette-open-data/
├── datasette_open_data/
├── templates/
├── static/
├── scripts/
│   ├── build_catalog.py
│   └── create_db.py
├── providers.yml
├── datasette.yml
├── catalog.db
└── data.db
```

---

## Current Providers

### Implemented

* CKAN

### Planned

* PxWeb / PxStat
* Socrata
* ArcGIS Hub
* OpenSpending
* Generic JSON APIs

---

## Roadmap

### Near Term

* Better catalog search
* Richer resource previews
* Agent integration
* Catalog refresh tooling

### Future

* XLSX loading
* JSON-STAT loading
* Geospatial normalization
* Background imports
* Scheduled catalog refresh
* Hybrid semantic search
* Provider-specific plugins

---

## License

MIT
