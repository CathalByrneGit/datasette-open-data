from __future__ import annotations

import asyncio
import csv
import io
import itertools
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx
from sqlite_utils import Database

from .models import Resource

# Rows written per statement. Bounded so that a large load is a sequence of
# short writes rather than one long one: on the Datasette path the write thread
# is shared with the rest of the instance, and a single 50k-row statement would
# hold it for the whole load.
DEFAULT_BATCH_SIZE = 5_000


class LoadError(RuntimeError):
    """Raised when a resource load fails, including partial-load context."""


def safe_table_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
    return value or "open_data_resource"


def _chunked(rows: Iterable[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    """Yield lists of up to `size` rows, without materialising the whole input."""
    iterator = iter(rows)
    while True:
        chunk = list(itertools.islice(iterator, size))
        if not chunk:
            return
        yield chunk


# ---------------------------------------------------------------------------
# Write targets
# ---------------------------------------------------------------------------


@runtime_checkable
class RowWriter(Protocol):
    """Somewhere rows can be written to."""

    async def insert_rows(
        self,
        table: str,
        rows: Iterable[dict[str, Any]],
        replace: bool = False,
    ) -> int: ...


def _insert_rows(
    db_path: str,
    table: str,
    rows: Iterable[dict[str, Any]],
    replace: bool = True,
) -> int:
    """Write rows synchronously. Callers in async code must go through a writer."""
    rows = list(rows)

    if not rows:
        # No table is created for an empty result: an empty placeholder would
        # show up in list_loaded_open_data_tables and join suggestions as if
        # it held data.
        return 0

    db = Database(db_path)
    db[table].insert_all(rows, replace=replace, alter=True)
    return len(rows)


class PathRowWriter:
    """Writes straight to a SQLite file.

    Used when there is no Datasette instance to route through — the
    open-data-load CLI, and tests. sqlite_utils is synchronous, so the write
    runs on a worker thread to keep it off whatever event loop is running.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    async def insert_rows(
        self,
        table: str,
        rows: Iterable[dict[str, Any]],
        replace: bool = False,
    ) -> int:
        return await asyncio.to_thread(_insert_rows, self.db_path, table, rows, replace)


class DatasetteRowWriter:
    """Writes through Datasette's write connection.

    Datasette serialises all writes to a database onto a single thread. Going
    around it with a second connection risks SQLITE_BUSY against Datasette's own
    writes, and leaves schema changes outside what the instance knows about.
    execute_write_fn also gives us its transaction handling and event tracking.

    sqlite_utils issues no commits of its own, so wrapping the connection
    execute_write_fn hands us composes with that transaction rather than
    fighting it.
    """

    def __init__(self, db):
        self.db = db

    async def insert_rows(
        self,
        table: str,
        rows: Iterable[dict[str, Any]],
        replace: bool = False,
    ) -> int:
        rows = list(rows)

        if not rows:
            return 0

        def write(conn):
            Database(conn)[table].insert_all(rows, replace=replace, alter=True)
            return len(rows)

        return await self.db.execute_write_fn(write)


def resolve_writer(destination: Any) -> RowWriter:
    """Accept a writer, a Datasette Database, or a path to a SQLite file."""
    if isinstance(destination, (str, Path)):
        return PathRowWriter(destination)

    if hasattr(destination, "execute_write_fn"):
        return DatasetteRowWriter(destination)

    if hasattr(destination, "insert_rows"):
        return destination

    raise TypeError(
        f"Cannot write to {destination!r}: expected a path, a Datasette database, "
        f"or an object with insert_rows()"
    )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


async def load_datastore_resource(
    provider: Any,
    resource_id: str,
    destination: Any,
    table: str | None = None,
    limit: int = 50_000,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    table = safe_table_name(table or resource_id)
    batch_size = min(batch_size, limit)
    writer = resolve_writer(destination)

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

        await writer.insert_rows(table, records)

        count = len(records)
        total += count
        offset += count

        if count < page_size:
            break

    return total


async def load_csv_url(
    csv_url: str,
    destination: Any,
    table: str,
    encoding: str = "utf-8-sig",
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    writer = resolve_writer(destination)

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

    table_name = safe_table_name(table)
    total = 0

    try:
        text = response.content.decode(encoding, errors="replace")
        reader = csv.DictReader(io.StringIO(text))

        # Written in batches so a large CSV does not become one long write.
        for chunk in _chunked(reader, batch_size):
            total += await writer.insert_rows(table_name, chunk)
    except csv.Error as exc:
        raise LoadError(f"CSV parse error from {csv_url!r}: {exc}") from exc

    return total


async def load_resource(
    provider: Any,
    resource: Resource,
    destination: Any,
    table: str | None = None,
    limit: int = 50_000,
) -> int:
    table_name = safe_table_name(table or resource.name or resource.id)

    if resource.datastore_active:
        return await load_datastore_resource(
            provider=provider,
            resource_id=resource.id,
            destination=destination,
            table=table_name,
            limit=limit,
        )

    resource_format = (resource.format or "").lower()

    if resource_format == "csv":
        if not resource.url:
            raise LoadError(f"Resource {resource.id!r} has format=CSV but no URL to download from")
        return await load_csv_url(
            csv_url=resource.url,
            destination=destination,
            table=table_name,
        )

    raise LoadError(
        f"Cannot load resource {resource.id!r}: "
        f"unsupported format={resource.format!r}, "
        f"datastore_active={resource.datastore_active!r}"
    )
