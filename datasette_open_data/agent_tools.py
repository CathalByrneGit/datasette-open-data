from __future__ import annotations

import html
import json
import logging

from .loader import LoadError, load_resource, safe_table_name
from .registry import get_provider
from .views import LOAD_PERMISSION, _can_load, _fts_query

logger = logging.getLogger(__name__)

# suggest_open_data_joins compares every column of every table against every
# column of every other table, so breadth is capped rather than left unbounded.
MAX_JOIN_TABLES = 12


try:
    from datasette_agent.tools import AgentTool

    AGENT_AVAILABLE = True
except ImportError:
    AgentTool = None
    AGENT_AVAILABLE = False


def register_open_data_agent_tools(datasette):
    if not AGENT_AVAILABLE:
        return []

    return [
        AgentTool(
            name="list_open_data_providers",
            description=(
                "List the configured open data providers (CKAN, Socrata, PxStat, etc.). "
                "Call this first to discover available provider names to pass to other tools."
            ),
            input_schema={"type": "object", "properties": {}},
            fn=_tool_list_open_data_providers,
        ),
        AgentTool(
            name="search_open_data_catalog",
            description=(
                "Search for open data datasets by keyword. "
                "Uses the local catalog.db FTS index when available (fast), "
                "otherwise falls back to a live provider API search (slower). "
                "Returns dataset IDs you can pass to show_open_data_dataset."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword(s) to search for, e.g. 'mortgage' or 'consumer price'",
                    },
                    "provider": {
                        "type": "string",
                        "description": "Provider name from list_open_data_providers. Omit to use the default.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results to return (1–50, default 10).",
                    },
                },
                "required": ["query"],
            },
            fn=_tool_search_open_data_catalog,
        ),
        AgentTool(
            name="show_open_data_dataset",
            description=(
                "Show metadata and available resources for a dataset. "
                "Returns the title, description, tags, and a list of resources with their "
                "load and preview URLs. Use the resource IDs with load_open_data_resource."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "dataset_id": {
                        "type": "string",
                        "description": "Dataset ID from search_open_data_catalog results.",
                    },
                    "provider": {
                        "type": "string",
                        "description": "Provider name. Omit to use the default.",
                    },
                },
                "required": ["dataset_id"],
            },
            fn=_tool_show_open_data_dataset,
        ),
        AgentTool(
            name="load_open_data_resource",
            description=(
                "Load an open data resource into the 'data' SQLite database as a table. "
                "Works with CKAN DataStore resources, CKAN CSV resources, Socrata datasets, "
                "and PxStat tables. Returns the table name and row count on success."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "resource_id": {
                        "type": "string",
                        "description": "Resource ID from show_open_data_dataset results.",
                    },
                    "provider": {
                        "type": "string",
                        "description": "Provider name. Omit to use the default.",
                    },
                    "table": {
                        "type": "string",
                        "description": "Destination table name. Defaults to the resource name.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum rows to load (default 50,000).",
                    },
                },
                "required": ["resource_id"],
            },
            fn=_tool_load_open_data_resource,
        ),
        AgentTool(
            name="list_loaded_open_data_tables",
            description=(
                "List tables currently loaded in the 'data' database. "
                "Use this to see what data is available to query or join."
            ),
            input_schema={"type": "object", "properties": {}},
            fn=_tool_list_loaded_tables,
        ),
        AgentTool(
            name="describe_loaded_open_data_table",
            description=(
                "Show column names and types for a loaded table. "
                "Call this before writing SQL queries to understand the schema."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "Table name from list_loaded_open_data_tables.",
                    },
                },
                "required": ["table"],
            },
            fn=_tool_describe_loaded_table,
        ),
        AgentTool(
            name="sample_loaded_open_data_table",
            description=(
                "Return sample rows from a loaded table. "
                "Use this to understand the data before writing SQL queries."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "Table name from list_loaded_open_data_tables.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of rows to return (1–50, default 10).",
                    },
                },
                "required": ["table"],
            },
            fn=_tool_sample_loaded_table,
        ),
        AgentTool(
            name="suggest_open_data_joins",
            description=(
                "Suggest how to join the currently loaded tables in the 'data' database. "
                "Compares column names and samples values using Jaccard similarity to find "
                "columns that likely refer to the same entities. "
                "Call this after loading multiple tables to discover join keys."
            ),
            input_schema={"type": "object", "properties": {}},
            fn=_tool_suggest_open_data_joins,
        ),
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _can_preview(resource) -> bool:
    """True if the resource supports datastore_preview."""
    if resource.datastore_active:
        return True
    fmt = (resource.format or "").lower()
    return fmt == "csv" and bool(resource.url)


def _esc(value) -> str:
    """Escape a value for interpolation into an _html payload.

    Titles, notes and names come from third-party portal APIs, so they are
    never trusted as markup.
    """
    return html.escape(str(value)) if value is not None else ""


async def _loaded_tables(db) -> list[str]:
    return [t for t in await db.table_names() if not t.startswith("_")]


async def _resolve_table(db, table: str) -> str:
    """Return `table` only if it really exists, else raise.

    Table names reach these tools as free text from the model, and they end up
    interpolated into SQL identifiers. Matching against the real table list
    means a name can never be anything but an existing table.
    """
    names = await db.table_names()
    if table in names:
        return table

    lowered = {name.lower(): name for name in names}
    if table.lower() in lowered:
        return lowered[table.lower()]

    raise KeyError(
        f"No table named {table!r} in the 'data' database. "
        f"Available tables: {', '.join(sorted(names)) or '(none)'}"
    )


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def _tool_list_open_data_providers(datasette, actor):
    try:
        from .registry import plugin_config, providers_from_config

        providers = providers_from_config(plugin_config(datasette))
        return json.dumps(
            {
                "providers": [
                    {
                        "name": name,
                        "title": provider.title,
                        "type": provider.type,
                        "base_url": provider.base_url,
                    }
                    for name, provider in providers.items()
                ]
            }
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})


async def _tool_search_open_data_catalog(
    datasette,
    actor,
    query: str,
    provider: str | None = None,
    limit: int = 10,
):
    limit = min(max(int(limit or 10), 1), 50)

    try:
        provider_obj = get_provider(datasette, provider)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    catalog_error = None

    # Fast path: FTS search via catalog.db
    if "catalog" in datasette.databases:
        try:
            db = datasette.get_database("catalog")
            rows = await db.execute(
                """
                SELECT
                    p.provider,
                    p.id,
                    p.name,
                    p.title,
                    p.notes,
                    p.organization_title,
                    COUNT(DISTINCT r.id) AS resource_count
                FROM packages_fts fts
                JOIN packages_fts_map m
                  ON m.fts_rowid = fts.rowid
                JOIN packages p
                  ON p.provider = m.provider
                 AND p.id = m.package_id
                LEFT JOIN resources r
                  ON r.provider = p.provider
                 AND r.package_id = p.id
                WHERE packages_fts MATCH :query
                  AND p.provider = :provider
                GROUP BY p.provider, p.id
                ORDER BY rank
                LIMIT :limit
                """,
                {
                    "query": _fts_query(query),
                    "provider": provider_obj.name,
                    "limit": limit,
                },
            )

            results = [
                {
                    "provider": row["provider"],
                    "dataset_id": row["id"],
                    "title": row["title"] or row["name"] or row["id"],
                    "notes": row["notes"],
                    "organization": row["organization_title"],
                    "resource_count": row["resource_count"],
                    "url": f"/-/open-data/dataset/{row['id']}?provider={row['provider']}",
                }
                for row in rows.rows
            ]

            return json.dumps(
                {
                    "query": query,
                    "provider": provider_obj.name,
                    "source": "catalog",
                    "count": len(results),
                    "results": results,
                    "_html": _search_results_html(results),
                }
            )
        except Exception as exc:
            # catalog.db is present but unusable (not yet built, stale schema).
            # Live search still works, so degrade rather than fail — but say why.
            logger.warning(
                "Catalog FTS search failed for provider %r, falling back to live search: %s",
                provider_obj.name,
                exc,
            )
            catalog_error = str(exc)

    # Slow path: live provider search
    try:
        live_results = await provider_obj.search(query, rows=limit)
    except Exception as exc:
        return json.dumps({"error": f"Search failed: {exc}"})

    results = [
        {
            "provider": provider_obj.name,
            "dataset_id": r.id,
            "title": r.title,
            "notes": r.notes,
            "organization": r.organization,
            "resource_count": len(r.resources),
            "url": f"/-/open-data/dataset/{r.id}?provider={provider_obj.name}",
        }
        for r in live_results
    ]

    payload = {
        "query": query,
        "provider": provider_obj.name,
        "source": "live",
        "count": len(results),
        "results": results,
        "_html": _search_results_html(results),
    }
    if catalog_error:
        payload["catalog_error"] = catalog_error

    return json.dumps(payload)


async def _tool_show_open_data_dataset(
    datasette,
    actor,
    dataset_id: str,
    provider: str | None = None,
):
    try:
        provider_obj = get_provider(datasette, provider)
        dataset = await provider_obj.dataset(dataset_id)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    resources = [
        {
            "id": resource.id,
            "name": resource.name,
            "format": resource.format,
            "datastore_active": resource.datastore_active,
            "url": resource.url,
            "preview_url": (
                f"/-/open-data/resource/{resource.id}/preview?provider={provider_obj.name}"
                if _can_preview(resource)
                else None
            ),
            "load_url": (f"/-/open-data/resource/{resource.id}/load?provider={provider_obj.name}"),
            # Spelled out so the model doesn't present load_url as a plain link.
            "load_method": "POST",
        }
        for resource in dataset.resources
    ]

    return json.dumps(
        {
            "provider": provider_obj.name,
            "dataset_id": dataset.id,
            "title": dataset.title,
            "notes": dataset.notes,
            "organization": dataset.organization,
            "tags": dataset.tags,
            "resources": resources,
            "_html": _dataset_html(provider_obj.name, dataset, resources),
        }
    )


async def _tool_load_open_data_resource(
    datasette,
    actor,
    resource_id: str,
    provider: str | None = None,
    table: str | None = None,
    limit: int = 50_000,
):
    # The tool writes to the database without going through load_resource_view,
    # so it has to enforce the same permission itself or it becomes a bypass.
    if not await _can_load(datasette, actor):
        return json.dumps(
            {
                "error": (
                    f"Permission denied: loading a resource requires the "
                    f"{LOAD_PERMISSION!r} permission on the 'data' database."
                )
            }
        )

    try:
        provider_obj = get_provider(datasette, provider)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    if "data" not in datasette.databases:
        return json.dumps(
            {
                "error": "No database named 'data' is loaded.",
                "hint": "Start Datasette with data.db: datasette serve data.db catalog.db -m metadata.yml",
            }
        )

    db = datasette.databases["data"]
    if db.path is None:
        return json.dumps(
            {
                "error": "The 'data' database is not file-backed. Resource loading requires a file database.",
            }
        )

    try:
        resource = await provider_obj.resource(resource_id)
    except Exception as exc:
        return json.dumps({"error": f"Could not fetch resource metadata: {exc}"})

    table_name = safe_table_name(table or resource.name or resource.id)

    try:
        rows_loaded = await load_resource(
            provider=provider_obj,
            resource=resource,
            # The Datasette database, not its path: writes go through its
            # serialised write connection rather than a second one.
            destination=db,
            table=table_name,
            limit=int(limit or 50_000),
        )
    except LoadError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        return json.dumps({"error": f"Load failed: {exc}"})

    return json.dumps(
        {
            "ok": True,
            "provider": provider_obj.name,
            "resource_id": resource.id,
            "table": table_name,
            "rows_loaded": rows_loaded,
            "browse_url": f"/data/{table_name}",
            "_html": (
                f"<p>Loaded <strong>{table_name}</strong> "
                f"&mdash; {rows_loaded:,} rows.</p>"
                f'<p><a href="/data/{table_name}">Browse {table_name} &rarr;</a></p>'
            ),
        }
    )


async def _tool_list_loaded_tables(datasette, actor):
    if "data" not in datasette.databases:
        return json.dumps({"error": "No database named 'data' is loaded."})

    try:
        db = datasette.get_database("data")
        return json.dumps({"database": "data", "tables": await _loaded_tables(db)})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


async def _tool_describe_loaded_table(datasette, actor, table: str):
    if "data" not in datasette.databases:
        return json.dumps({"error": "No database named 'data' is loaded."})

    try:
        db = datasette.get_database("data")
        table = await _resolve_table(db, table)
        rows = await db.execute(f'PRAGMA table_info("{table}")')
        columns = [
            {
                "name": row["name"] if "name" in row.keys() else row[1],
                "type": row["type"] if "type" in row.keys() else row[2],
            }
            for row in rows.rows
        ]
        return json.dumps({"database": "data", "table": table, "columns": columns})
    except KeyError as exc:
        return json.dumps({"error": exc.args[0]})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


async def _tool_sample_loaded_table(
    datasette,
    actor,
    table: str,
    limit: int = 10,
):
    if "data" not in datasette.databases:
        return json.dumps({"error": "No database named 'data' is loaded."})

    limit = min(max(int(limit or 10), 1), 50)

    try:
        db = datasette.get_database("data")
        table = await _resolve_table(db, table)
        result = await db.execute(f'SELECT * FROM "{table}" LIMIT ?', [limit])
        rows = [dict(row) for row in result.rows]
        return json.dumps({"database": "data", "table": table, "count": len(rows), "rows": rows})
    except KeyError as exc:
        return json.dumps({"error": exc.args[0]})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


async def _tool_suggest_open_data_joins(datasette, actor):
    if "data" not in datasette.databases:
        return json.dumps({"error": "No database named 'data' is loaded."})

    try:
        db = datasette.get_database("data")
        tables = await _loaded_tables(db)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    if len(tables) < 2:
        return json.dumps(
            {
                "message": "Need at least 2 loaded tables to suggest joins.",
                "tables": tables,
            }
        )

    # Comparison is quadratic in tables and in columns, so cap the breadth.
    truncated = len(tables) > MAX_JOIN_TABLES
    tables = tables[:MAX_JOIN_TABLES]

    # Sample values per column for each table
    table_cols: dict[str, dict[str, set[str]]] = {}
    for table in tables:
        try:
            col_rows = await db.execute(f'PRAGMA table_info("{table}")')
            columns = [(row["name"] if "name" in row.keys() else row[1]) for row in col_rows.rows]
            sample = await db.execute(f'SELECT * FROM "{table}" LIMIT 200')
            rows = [dict(r) for r in sample.rows]

            table_cols[table] = {
                col: {str(r[col]) for r in rows if r.get(col) is not None} for col in columns
            }
        except Exception:
            continue

    suggestions = []
    table_list = list(table_cols.keys())

    for i in range(len(table_list)):
        for j in range(i + 1, len(table_list)):
            t1, t2 = table_list[i], table_list[j]
            for col1, vals1 in table_cols[t1].items():
                for col2, vals2 in table_cols[t2].items():
                    name_match = col1.lower() == col2.lower()
                    name_similar = not name_match and (
                        col1.lower() in col2.lower() or col2.lower() in col1.lower()
                    )

                    if not (name_match or name_similar):
                        continue

                    jaccard = 0.0
                    if vals1 and vals2:
                        union = vals1 | vals2
                        jaccard = len(vals1 & vals2) / len(union) if union else 0.0

                    if name_match or jaccard > 0.05:
                        suggestions.append(
                            {
                                "table1": t1,
                                "column1": col1,
                                "table2": t2,
                                "column2": col2,
                                "name_match": name_match,
                                "jaccard": round(jaccard, 3),
                                "sql": (
                                    f'SELECT * FROM "{t1}" '
                                    f'JOIN "{t2}" ON "{t1}"."{col1}" = "{t2}"."{col2}" '
                                    f"LIMIT 10"
                                ),
                            }
                        )

    suggestions.sort(key=lambda x: (x["name_match"], x["jaccard"]), reverse=True)

    payload = {
        "tables": table_list,
        "count": len(suggestions),
        "suggestions": suggestions[:10],
    }
    if truncated:
        payload["note"] = (
            f"Only the first {MAX_JOIN_TABLES} tables were compared. "
            f"Drop unused tables to widen the search."
        )

    return json.dumps(payload)


# ---------------------------------------------------------------------------
# HTML helpers for _html fields
# ---------------------------------------------------------------------------


def _search_results_html(results: list[dict]) -> str:
    if not results:
        return "<p>No datasets found.</p>"

    items = [
        f"""<li>
          <strong><a href="{_esc(r["url"])}">{_esc(r["title"])}</a></strong><br>
          <small>{_esc(r["provider"])} &middot; {r["resource_count"]} resources</small>
        </li>"""
        for r in results
    ]
    return "<ul>" + "\n".join(items) + "</ul>"


def _dataset_html(provider: str, dataset, resources: list[dict]) -> str:
    items = []
    for resource in resources:
        # Loading is a POST, so it is a form button rather than a link.
        actions = [
            f'<form method="POST" action="{_esc(resource["load_url"])}" '
            f'style="display:inline">'
            f'<button type="submit">Load</button>'
            f"</form>"
        ]
        if resource["preview_url"]:
            actions.insert(0, f'<a href="{_esc(resource["preview_url"])}">Preview</a>')
        items.append(
            f"""<li>
              <strong>{_esc(resource["name"] or resource["id"])}</strong><br>
              <small>{_esc(resource["format"] or "unknown format")}</small><br>
              {" &middot; ".join(actions)}
            </li>"""
        )

    return (
        f"<h3>{_esc(dataset.title)}</h3>"
        f"<p>{_esc(dataset.notes)}</p>"
        f"<p><small>{_esc(provider)}</small></p>"
        f"<ul>{''.join(items)}</ul>"
    )
