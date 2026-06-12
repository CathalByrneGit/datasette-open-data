from __future__ import annotations

import json

from .loader import load_resource, safe_table_name
from .registry import get_provider


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
            description="List configured open data providers.",
            input_schema={"type": "object", "properties": {}},
            fn=_tool_list_open_data_providers,
        ),
        AgentTool(
            name="search_open_data_catalog",
            description=(
                "Search the local open data catalog for datasets by keyword. "
                "Use this to find relevant datasets before loading resources."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "provider": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
            fn=_tool_search_open_data_catalog,
        ),
        AgentTool(
            name="show_open_data_dataset",
            description="Show metadata and resources for an open data dataset.",
            input_schema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "provider": {"type": "string"},
                },
                "required": ["dataset_id"],
            },
            fn=_tool_show_open_data_dataset,
        ),
        AgentTool(
            name="load_open_data_resource",
            description=(
                "Load a CKAN resource into the Datasette data database. "
                "Supports CKAN DataStore resources and CSV resources."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "resource_id": {"type": "string"},
                    "provider": {"type": "string"},
                    "table": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["resource_id"],
            },
            fn=_tool_load_open_data_resource,
        ),
        AgentTool(
            name="list_loaded_open_data_tables",
            description="List tables currently loaded in the Datasette data database.",
            input_schema={"type": "object", "properties": {}},
            fn=_tool_list_loaded_tables,
        ),
        AgentTool(
            name="describe_loaded_open_data_table",
            description="Describe columns for a loaded table in the data database.",
            input_schema={
                "type": "object",
                "properties": {
                    "table": {"type": "string", "description": "Table name"},
                },
                "required": ["table"],
            },
            fn=_tool_describe_loaded_table,
        ),
        AgentTool(
            name="sample_loaded_open_data_table",
            description="Return sample rows from a loaded table in the data database.",
            input_schema={
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["table"],
            },
            fn=_tool_sample_loaded_table,
        ),
    ]


def _fts_query(q: str) -> str:
    terms = [
        term.strip()
        for term in q.replace('"', " ").split()
        if term.strip()
    ]
    return " ".join(f"{term}*" for term in terms)


async def _tool_list_open_data_providers(datasette, actor):
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


async def _tool_search_open_data_catalog(
    datasette,
    actor,
    query: str,
    provider: str | None = None,
    limit: int = 10,
):
    limit = min(max(int(limit or 10), 1), 50)
    provider_obj = get_provider(datasette, provider)

    if "catalog" not in datasette.databases:
        return json.dumps(
            {
                "error": "catalog.db is not loaded.",
                "hint": "Run: uv run datasette data.db catalog.db -m examples/metadata.yml",
            }
        )

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
            "count": len(results),
            "results": results,
            "_html": _search_results_html(results),
        }
    )


async def _tool_show_open_data_dataset(
    datasette,
    actor,
    dataset_id: str,
    provider: str | None = None,
):
    provider_obj = get_provider(datasette, provider)
    dataset = await provider_obj.dataset(dataset_id)

    resources = [
        {
            "id": resource.id,
            "name": resource.name,
            "format": resource.format,
            "datastore_active": resource.datastore_active,
            "url": resource.url,
            "preview_url": (
                f"/-/open-data/resource/{resource.id}/preview?provider={provider_obj.name}"
                if resource.datastore_active
                else None
            ),
            "load_url": f"/-/open-data/resource/{resource.id}/load?provider={provider_obj.name}",
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
    provider_obj = get_provider(datasette, provider)
    resource = await provider_obj.resource(resource_id)

    if "data" not in datasette.databases:
        return json.dumps(
            {
                "error": "No database named 'data' is loaded.",
                "hint": "Run: uv run datasette data.db catalog.db -m examples/metadata.yml",
            }
        )

    db_path = datasette.databases["data"].path

    if db_path is None:
        return json.dumps(
            {
                "error": "The 'data' database is not file-backed. Resource loading does not work with --memory.",
            }
        )

    table_name = safe_table_name(table or resource.name or resource.id)

    rows_loaded = await load_resource(
        provider=provider_obj,
        resource=resource,
        db_path=db_path,
        table=table_name,
        limit=int(limit or 50_000),
    )

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

    db = datasette.get_database("data")
    tables = [
        table
        for table in await db.table_names()
        if not table.startswith("_")
    ]

    return json.dumps({"database": "data", "tables": tables})


async def _tool_describe_loaded_table(datasette, actor, table: str):
    if "data" not in datasette.databases:
        return json.dumps({"error": "No database named 'data' is loaded."})

    db = datasette.get_database("data")

    try:
        rows = await db.execute(f'PRAGMA table_info("{table}")')
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    columns = [
        {
            "name": row["name"] if "name" in row.keys() else row[1],
            "type": row["type"] if "type" in row.keys() else row[2],
        }
        for row in rows.rows
    ]

    return json.dumps(
        {
            "database": "data",
            "table": table,
            "columns": columns,
        }
    )


async def _tool_sample_loaded_table(
    datasette,
    actor,
    table: str,
    limit: int = 10,
):
    if "data" not in datasette.databases:
        return json.dumps({"error": "No database named 'data' is loaded."})

    limit = min(max(int(limit or 10), 1), 50)
    db = datasette.get_database("data")

    try:
        result = await db.execute(
            f'SELECT * FROM "{table}" LIMIT ?',
            [limit],
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    rows = [dict(row) for row in result.rows]

    return json.dumps(
        {
            "database": "data",
            "table": table,
            "count": len(rows),
            "rows": rows,
        }
    )


def _search_results_html(results: list[dict]) -> str:
    if not results:
        return "<p>No datasets found.</p>"

    items = []

    for result in results:
        items.append(
            f"""
            <li>
              <strong><a href="{result["url"]}">{result["title"]}</a></strong>
              <br>
              <small>{result["provider"]} · {result["resource_count"]} resources</small>
            </li>
            """
        )

    return "<ul>" + "\n".join(items) + "</ul>"


def _dataset_html(provider: str, dataset, resources: list[dict]) -> str:
    items = []

    for resource in resources:
        actions = [f'<a href="{resource["load_url"]}">Load</a>']

        if resource["preview_url"]:
            actions.insert(0, f'<a href="{resource["preview_url"]}">Preview</a>')

        items.append(
            f"""
            <li>
              <strong>{resource["name"] or resource["id"]}</strong>
              <br>
              <small>{resource["format"] or "unknown format"}</small>
              <br>
              {" · ".join(actions)}
            </li>
            """
        )

    return f"""
    <h3>{dataset.title}</h3>
    <p>{dataset.notes or ""}</p>
    <p><small>{provider}</small></p>
    <ul>
      {"".join(items)}
    </ul>
    """