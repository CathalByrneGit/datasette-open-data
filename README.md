# datasette-open-data

A provider-based Datasette plugin for finding and loading datasets from open data portals.

The first provider is **CKAN**, tested against the Central Bank of Ireland Open Data Portal:

- Portal: https://opendata.centralbank.ie
- CKAN API: https://opendata.centralbank.ie/api/3
- CKAN DataStore API: https://opendata.centralbank.ie/en_GB/api/3/action/datastore_search

## Why this exists

`datasette-open-data` is intended to become a generic open-data layer for Datasette:

```text
search portal
    ↓
inspect dataset
    ↓
load resource
    ↓
query in Datasette
```

It starts with CKAN because CKAN provides a standard catalog model:

- Packages (datasets)
- Resources
- Groups
- Organizations
- Tags
- DataStore resources

The long-term goal is to support additional open-data providers such as:

- PxStat
- Socrata
- ArcGIS
- Other catalog APIs

through a shared provider interface.

## Features

Current functionality:

- CKAN package search
- CKAN dataset details
- CKAN groups
- CKAN organizations
- CKAN tags
- CKAN DataStore preview
- Resource loading into SQLite
- Automatic DataStore vs CSV detection
- Provider registry
- Central Bank provider configuration included by default

## Installation

### Using uv (recommended)

```bash
uv sync
```

### Editable development install

```bash
uv pip install -e .
```

## Running Datasette

### Search and browse only

```bash
uv run datasette -m examples/metadata.yml --memory
```

### Search, browse and load resources

Use a file-backed SQLite database:

```bash
uv run datasette data.db -m examples/metadata.yml
```

**Important**

Resource loading requires a file-backed SQLite database.

This will NOT work:

```bash
uv run datasette --memory
```

because there is no database file available for imported resources.

## Available Routes

### Plugin Home

```text
/-/open-data
```

Lists configured providers.

---

### Search

```text
/-/open-data/search?q=card
```

JSON:

```text
/-/open-data/search?q=card&_format=json
```

Returns CKAN datasets matching the search term.

---

### Dataset Details

```text
/-/open-data/dataset/{dataset_id}
```

Example:

```text
/-/open-data/dataset/monthly-card-payment-statistics
```

Returns dataset metadata and available resources.

---

### Resource Preview

```text
/-/open-data/resource/{resource_id}/preview
```

Example:

```text
/-/open-data/resource/0c86bb22-83c4-47ee-973e-07736b36a021/preview
```

Returns a sample of rows from a CKAN DataStore resource.

---

### Resource Load

```text
/-/open-data/resource/{resource_id}/load
```

Example:

```text
/-/open-data/resource/0c86bb22-83c4-47ee-973e-07736b36a021/load
```

This will:

1. Resolve the CKAN resource
2. Detect whether it is:
   - a DataStore resource
   - a CSV resource
3. Load records into SQLite
4. Create a Datasette table
5. Redirect to the imported table

Optional parameters:

```text
?provider=centralbank
```

```text
?table=my_table
```

```text
?limit=50000
```

## Resource Loading

The loader currently supports:

### CKAN DataStore

Loads rows using:

```text
datastore_search
```

with automatic paging.

### CSV

Downloads and imports CSV resources directly into SQLite.

### Automatic Detection

```python
if resource.datastore_active:
    load_datastore_resource(...)
elif resource.format == "csv":
    load_csv_url(...)
```

## CLI Loading

You can also load resources outside Datasette.

### CKAN Resource

```bash
open-data-load \
  --provider centralbank \
  --resource-id 0c86bb22-83c4-47ee-973e-07736b36a021 \
  --database centralbank.db \
  --table centralbank_resource
```

### CSV URL

```bash
open-data-load \
  --csv-url "https://example.com/data.csv" \
  --database data.db \
  --table my_table
```

## Datasette Configuration

Example `metadata.yml`:

```yaml
plugins:
  datasette-open-data:
    providers:
      centralbank:
        type: ckan
        title: Central Bank of Ireland Open Data Portal
        base_url: https://opendata.centralbank.ie
        api_base_url: https://opendata.centralbank.ie/api/3
        datastore_api_base_url: https://opendata.centralbank.ie/en_GB/api/3
```

If no configuration is provided, the plugin automatically falls back to the Central Bank provider.

## Architecture

```text
OpenDataProvider (Protocol)
           │
           ▼
     CKANProvider
           │
           ▼
  Dataset / Resource models
           │
           ▼
         Loader
           │
           ▼
        SQLite
           │
           ▼
       Datasette
```

Providers return internal dataclasses rather than raw CKAN JSON.

This makes future providers easier to add.

## Current Scope

### Implemented

- Provider registry
- CKAN provider
- Dataset models
- Resource models
- Package search
- Dataset details
- Groups
- Organizations
- Tags
- DataStore preview
- SQLite loading
- DataStore paging
- CSV imports
- Resource auto-detection
- Datasette integration routes

### Planned

- PxStat provider
- Socrata provider
- ArcGIS provider
- XLSX loading
- JSON-STAT loading
- Geospatial normalization
- Agent tools
- Multi-database selection
- Background imports
- Cached dataset metadata

## Development Notes

Run the project using:

```bash
uv run python scripts/build_catalog.py 
uv run python scripts/dev.py
uv run datasette data.db catalog.db -m examples/metadata.yml
```

If you make code changes:

```bash
uv sync
```

or

```bash
uv pip install -e .
```

then restart Datasette.

## License

MIT