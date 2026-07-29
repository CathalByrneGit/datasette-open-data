from __future__ import annotations

import asyncio
import csv
import io
import re
from collections.abc import Iterable
from typing import Any

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
    """Write rows synchronously. Callers in async code must use _insert_rows_async."""
    rows = list(rows)

    if not rows:
        # No table is created for an empty result: an empty placeholder would
        # show up in list_loaded_open_data_tables and join suggestions as if
        # it held data.
        return 0

    db = Database(db_path)
    db[table].insert_all(rows, replace=replace, alter=True)
    return len(rows)


async def _insert_rows_async(
    db_path: str,
    table: str,
    rows: Iterable[dict[str, Any]],
    replace: bool = True,
) -> int:
    """Run the sqlite_utils write on a worker thread.

    sqlite_utils is synchronous; calling it directly from a coroutine blocks
    Datasette's event loop for the whole write, which for a 50k-row load means
    the server stops serving until it finishes.
    """
    return await asyncio.to_thread(_insert_rows, db_path, table, rows, replace)


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

        await _insert_rows_async(db_path, table, records, replace=False)

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
        return await _insert_rows_async(db_path, safe_table_name(table), reader)
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

    raise LoadError(
        f"Cannot load resource {resource.id!r}: "
        f"unsupported format={resource.format!r}, "
        f"datastore_active={resource.datastore_active!r}"
    )
