from __future__ import annotations

import csv
import io
import re
from typing import Any, Iterable

import httpx
from sqlite_utils import Database

from .models import Resource


class LoadError(RuntimeError):
    """Raised when a resource load fails, including partial-load context."""


def safe_table_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
    return value or "open_data_resource"


def _insert_rows(
    db_path: str,
    table: str,
    rows: Iterable[dict[str, Any]],
    replace: bool = True,
) -> int:
    db = Database(db_path)
    rows = list(rows)

    if not rows:
        db[table].create({"_empty": str}, pk=None, if_not_exists=True)
        return 0

    db[table].insert_all(rows, replace=replace, alter=True)
    return len(rows)


async def load_datastore_resource(
    provider: Any,
    resource_id: str,
    db_path: str,
    table: str | None = None,
    limit: int = 50_000,
    batch_size: int = 5_000,
) -> int:
    table = safe_table_name(table or resource_id)
    batch_size = min(batch_size, limit)

    db = Database(db_path)
    total = 0
    offset = 0

    while total < limit:
        remaining = limit - total
        page_size = min(batch_size, remaining)

        try:
            result = await provider._get(
                "datastore_search",
                {
                    "resource_id": resource_id,
                    "limit": page_size,
                    "offset": offset,
                },
                datastore=True,
            )
        except Exception as exc:
            raise LoadError(
                f"Failed fetching resource {resource_id!r} at offset {offset} "
                f"({total} rows already written): {exc}"
            ) from exc

        records = result.get("records") or []

        if not records:
            break

        db[table].insert_all(records, alter=True)

        count = len(records)
        total += count
        offset += count

        if count < page_size:
            break

    return total


async def load_csv_url(
    csv_url: str,
    db_path: str,
    table: str,
    encoding: str = "utf-8-sig",
) -> int:
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.get(csv_url)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise LoadError(
            f"HTTP {exc.response.status_code} downloading CSV from {csv_url!r}"
        ) from exc
    except httpx.TimeoutException as exc:
        raise LoadError(f"Timed out downloading CSV from {csv_url!r}") from exc

    try:
        text = response.content.decode(encoding, errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        return _insert_rows(db_path, safe_table_name(table), reader)
    except csv.Error as exc:
        raise LoadError(f"CSV parse error from {csv_url!r}: {exc}") from exc


async def load_resource(
    provider: Any,
    resource: Resource,
    db_path: str,
    table: str | None = None,
    limit: int = 50_000,
) -> int:
    table_name = safe_table_name(table or resource.name or resource.id)

    if resource.datastore_active:
        return await load_datastore_resource(
            provider=provider,
            resource_id=resource.id,
            db_path=db_path,
            table=table_name,
            limit=limit,
        )

    resource_format = (resource.format or "").lower()

    if resource_format == "csv":
        if not resource.url:
            raise LoadError(
                f"Resource {resource.id!r} has format=CSV but no URL to download from"
            )
        return await load_csv_url(
            csv_url=resource.url,
            db_path=db_path,
            table=table_name,
        )

    raise ValueError(
        f"Cannot load resource {resource.id!r}: "
        f"unsupported format={resource.format!r}, "
        f"datastore_active={resource.datastore_active!r}"
    )
