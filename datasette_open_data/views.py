from __future__ import annotations

from dataclasses import asdict, is_dataclass

from datasette import Response

from .loader import load_resource, safe_table_name

from .registry import get_provider, plugin_config, providers_from_config


def _wants_json(request) -> bool:
    return (
        request.args.get("_format") == "json"
        or "application/json" in request.headers.get("accept", "")
    )


def _jsonable(value):
    if is_dataclass(value):
        return asdict(value)

    if isinstance(value, list):
        return [_jsonable(item) for item in value]

    if isinstance(value, dict):
        return {
            key: _jsonable(item)
            for key, item in value.items()
        }

    return value

def _fts_query(q: str) -> str:
    terms = [
        term.strip()
        for term in q.replace('"', " ").split()
        if term.strip()
    ]

    if not terms:
        return ""

    return " ".join(f"{term}*" for term in terms)


async def index_view(datasette, request):
    providers = providers_from_config(plugin_config(datasette))

    selected_provider = (
        request.args.get("provider")
        or next(iter(providers), None)
    )

    catalog = {
        "available": "catalog" in datasette.databases,
        "selected_provider": selected_provider,
        "providers": [],
        "recent_packages": [],
        "organizations": [],
        "groups": [],
        "tags": [],
    }

    if catalog["available"]:
        db = datasette.get_database("catalog")

        try:
            catalog["providers"] = (
                await db.execute(
                    """
                    SELECT
                        p.id,
                        p.title,
                        p.type,
                        p.base_url,
                        COUNT(pkg.id) AS package_count
                    FROM providers p
                    LEFT JOIN packages pkg ON pkg.provider = p.id
                    GROUP BY p.id
                    ORDER BY p.title
                    """
                )
            ).rows

            catalog["recent_packages"] = (
                await db.execute(
                    """
                    SELECT
                        provider,
                        id,
                        title,
                        organization_title,
                        metadata_modified
                    FROM packages
                    WHERE provider = ?
                    ORDER BY metadata_modified DESC
                    LIMIT 10
                    """,
                    [selected_provider],
                )
            ).rows

            catalog["organizations"] = (
                await db.execute(
                    """
                    SELECT
                        o.provider,
                        o.id,
                        o.title,
                        o.name,
                        COUNT(p.id) AS package_count
                    FROM organizations o
                    LEFT JOIN packages p
                      ON p.provider = o.provider
                     AND p.organization_id = o.id
                    WHERE o.provider = ?
                    GROUP BY o.provider, o.id
                    ORDER BY package_count DESC, o.title
                    LIMIT 12
                    """,
                    [selected_provider],
                )
            ).rows

            catalog["groups"] = (
                await db.execute(
                    """
                    SELECT
                        g.provider,
                        g.id,
                        g.title,
                        g.name,
                        COUNT(pg.package_id) AS package_count
                    FROM groups g
                    LEFT JOIN package_groups pg
                      ON pg.provider = g.provider
                     AND pg.group_id = g.id
                    WHERE g.provider = ?
                    GROUP BY g.provider, g.id
                    ORDER BY package_count DESC, g.title
                    LIMIT 12
                    """,
                    [selected_provider],
                )
            ).rows

            catalog["tags"] = (
                await db.execute(
                    """
                    SELECT
                        t.provider,
                        t.name,
                        COUNT(pt.package_id) AS package_count
                    FROM tags t
                    LEFT JOIN package_tags pt
                      ON pt.provider = t.provider
                     AND pt.tag_name = t.name
                    WHERE t.provider = ?
                    GROUP BY t.provider, t.name
                    ORDER BY package_count DESC, t.name
                    LIMIT 24
                    """,
                    [selected_provider],
                )
            ).rows

        except Exception as exc:
            catalog["error"] = str(exc)

    if _wants_json(request):
        return Response.json(
            {
                "providers": {
                    name: {
                        "title": p.title,
                        "base_url": p.base_url,
                        "type": p.type,
                    }
                    for name, p in providers.items()
                },
                "selected_provider": selected_provider,
                "catalog_available": catalog["available"],
            }
        )

    html = await datasette.render_template(
        "open_data_index.html",
        {
            "providers": providers,
            "selected_provider": selected_provider,
            "catalog": catalog,
            "q": request.args.get("q", ""),
        },
        request=request,
    )

    return Response.html(html)


async def search_view(datasette, request):
    q = request.args.get("q", "").strip()
    provider_name = request.args.get("provider")
    provider = get_provider(datasette, provider_name)

    tag = request.args.get("tag")
    organization_id = request.args.get("organization_id")
    group_id = request.args.get("group_id")

    rows_limit = int(request.args.get("rows", 20))
    use_live = request.args.get("source") == "live"

    results = []
    search_source = "live"
    search_label = None

    if tag:
        search_label = f'Tag: {tag}'
    elif organization_id:
        search_label = "Organization"
    elif group_id:
        search_label = "Group"
    elif q:
        search_label = f'Search: {q}'

    can_use_catalog = (
        "catalog" in datasette.databases
        and not use_live
        and (q or tag or organization_id or group_id)
    )

    if can_use_catalog:
        db = datasette.get_database("catalog")

        try:
            params = {
                "provider": provider.name,
                "limit": rows_limit,
            }

            joins = [
                """
                LEFT JOIN resources r
                  ON r.provider = p.provider
                 AND r.package_id = p.id
                """
            ]

            where = [
                "p.provider = :provider"
            ]

            if q:
                joins.insert(
                    0,
                    """
                    JOIN packages_fts_map m
                      ON m.provider = p.provider
                     AND m.package_id = p.id
                    JOIN packages_fts fts
                      ON fts.rowid = m.fts_rowid
                    """
                )
                where.append("packages_fts MATCH :query")
                params["query"] = _fts_query(q)

            if tag:
                joins.append(
                    """
                    JOIN package_tags pt
                      ON pt.provider = p.provider
                     AND pt.package_id = p.id
                    """
                )
                where.append("pt.tag_name = :tag")
                params["tag"] = tag

            if organization_id:
                where.append("p.organization_id = :organization_id")
                params["organization_id"] = organization_id

                org_row = (
                    await db.execute(
                        """
                        SELECT title, name
                        FROM organizations
                        WHERE provider = ? AND id = ?
                        """,
                        [provider.name, organization_id],
                    )
                ).first()
                if org_row:
                    search_label = f"Organization: {org_row['title'] or org_row['name'] or organization_id}"

            if group_id:
                joins.append(
                    """
                    JOIN package_groups pg
                      ON pg.provider = p.provider
                     AND pg.package_id = p.id
                    """
                )
                where.append("pg.group_id = :group_id")
                params["group_id"] = group_id

                group_row = (
                    await db.execute(
                        """
                        SELECT title, name
                        FROM groups
                        WHERE provider = ? AND id = ?
                        """,
                        [provider.name, group_id],
                    )
                ).first()
                if group_row:
                    search_label = f"Group: {group_row['title'] or group_row['name'] or group_id}"

            sql = f"""
                SELECT
                    p.provider,
                    p.id,
                    p.name,
                    p.title,
                    p.notes,
                    p.organization_title AS organization,
                    COUNT(DISTINCT r.id) AS resource_count
                FROM packages p
                {' '.join(joins)}
                WHERE {' AND '.join(where)}
                GROUP BY p.provider, p.id
                {"ORDER BY rank" if q else "ORDER BY p.title"}
                LIMIT :limit
            """

            rows = (await db.execute(sql, params)).rows

            results = [
                {
                    "provider": row["provider"],
                    "id": row["id"],
                    "name": row["name"],
                    "title": row["title"] or row["name"] or row["id"],
                    "notes": row["notes"],
                    "organization": row["organization"],
                    "resource_count": row["resource_count"],
                }
                for row in rows
            ]

            search_source = "catalog"

        except Exception:
            results = []
            search_source = "live-fallback"

    if q and search_source != "catalog":
        live_results = await provider.search(q, rows=rows_limit)

        results = [
            {
                "provider": provider.name,
                "id": dataset.id,
                "name": dataset.name,
                "title": dataset.title,
                "notes": dataset.notes,
                "organization": dataset.organization,
                "resource_count": len(dataset.resources),
            }
            for dataset in live_results
        ]

    if _wants_json(request):
        return Response.json(
            {
                "q": q,
                "tag": tag,
                "organization_id": organization_id,
                "group_id": group_id,
                "provider": provider.name,
                "source": search_source,
                "count": len(results),
                "results": results,
            }
        )

    html = await datasette.render_template(
        "open_data_search.html",
        {
            "provider": provider,
            "q": q,
            "tag": tag,
            "organization_id": organization_id,
            "group_id": group_id,
            "results": results,
            "count": len(results),
            "search_source": search_source,
            "search_label": search_label,
        },
        request=request,
    )

    return Response.html(html)


async def dataset_view(datasette, request):
    provider = get_provider(datasette, request.args.get("provider"))
    dataset_id = request.url_vars["dataset_id"]

    dataset = None
    dataset_source = "live"

    if "catalog" in datasette.databases:
        db = datasette.get_database("catalog")

        try:
            package_row = (
                await db.execute(
                    """
                    SELECT
                        provider,
                        id,
                        name,
                        title,
                        notes,
                        organization_title AS organization,
                        license_title,
                        url,
                        raw_json
                    FROM packages
                    WHERE provider = ? AND id = ?
                    """,
                    [provider.name, dataset_id],
                )
            ).first()

            if package_row:
                resource_rows = (
                    await db.execute(
                        """
                        SELECT
                            id,
                            name,
                            description,
                            format,
                            url,
                            datastore_active,
                            raw_json
                        FROM resources
                        WHERE provider = ? AND package_id = ?
                        ORDER BY name
                        """,
                        [provider.name, dataset_id],
                    )
                ).rows

                tag_rows = (
                    await db.execute(
                        """
                        SELECT tag_name
                        FROM package_tags
                        WHERE provider = ? AND package_id = ?
                        ORDER BY tag_name
                        """,
                        [provider.name, dataset_id],
                    )
                ).rows

                from .models import Dataset, Resource

                dataset = Dataset(
                    id=package_row["id"],
                    name=package_row["name"] or package_row["id"],
                    title=package_row["title"] or package_row["name"] or package_row["id"],
                    notes=package_row["notes"],
                    organization=package_row["organization"],
                    tags=[row["tag_name"] for row in tag_rows],
                    resources=[
                        Resource(
                            id=row["id"],
                            name=row["name"],
                            description=row["description"],
                            format=row["format"],
                            url=row["url"],
                            datastore_active=bool(row["datastore_active"]),
                        )
                        for row in resource_rows
                    ],
                    license_title=package_row["license_title"],
                    url=package_row["url"],
                )

                dataset_source = "catalog"

        except Exception:
            dataset = None
            dataset_source = "live-fallback"

    if dataset is None:
        dataset = await provider.dataset(dataset_id)

    if _wants_json(request):
        return Response.json(
            {
                "source": dataset_source,
                "dataset": _jsonable(dataset),
            }
        )

    html = await datasette.render_template(
        "open_data_dataset.html",
        {
            "provider": provider,
            "dataset": dataset,
            "dataset_source": dataset_source,
        },
        request=request,
    )

    return Response.html(html)

async def resource_preview_view(datasette, request):
    provider = get_provider(datasette, request.args.get("provider"))
    resource_id = request.url_vars["resource_id"]
    limit = int(request.args.get("limit", 10))

    result = await provider.datastore_preview(resource_id, limit=limit)

    if _wants_json(request):
        return Response.json(result)

    html = await datasette.render_template(
        "open_data_preview.html",
        {
            "provider": provider,
            "resource_id": resource_id,
            "preview": result,
        },
        request=request,
    )

    return Response.html(html)


async def groups_view(datasette, request):
    provider = get_provider(datasette, request.args.get("provider"))
    return Response.json(await provider.groups())


async def organizations_view(datasette, request):
    provider = get_provider(datasette, request.args.get("provider"))
    return Response.json(await provider.organizations())


async def tags_view(datasette, request):
    provider = get_provider(datasette, request.args.get("provider"))
    return Response.json(await provider.tags())

async def load_resource_view(datasette, request):
    provider = get_provider(datasette, request.args.get("provider"))
    resource_id = request.url_vars["resource_id"]

    resource = await provider.resource(resource_id)

    table = safe_table_name(
        request.args.get("table")
        or resource.name
        or resource.id
    )

    db_path = datasette.databases["data"].path

    if db_path is None:
        return Response.text(
            "Cannot load resource: the 'data' database is not backed by a file.",
            status=400,
        )

    limit = int(request.args.get("limit", 50_000))

    rows_loaded = await load_resource(
        provider=provider,
        resource=resource,
        db_path=db_path,
        table=table,
        limit=limit,
    )

    if _wants_json(request):
        return Response.json(
            {
                "ok": True,
                "resource_id": resource.id,
                "table": table,
                "rows_loaded": rows_loaded,
            }
        )

    return Response.redirect(f"/data/{table}")