"""Tests for the loader's write targets.

Loads used to open their own sqlite_utils connection to the database file.
Datasette serialises writes onto one thread per database, so a second
connection risks SQLITE_BUSY against Datasette's own writes and leaves schema
changes outside what the instance tracks. Writes now go through
Database.execute_write_fn when a Datasette database is available, and fall back
to the file only for the CLI, which has no instance to route through.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlite_utils import Database

from datasette_open_data.loader import (
    DatasetteRowWriter,
    PathRowWriter,
    _chunked,
    load_csv_url,
    load_datastore_resource,
    resolve_writer,
)

# ---------------------------------------------------------------------------
# _chunked
# ---------------------------------------------------------------------------


def test_chunked_splits_evenly():
    assert list(_chunked([1, 2, 3, 4], 2)) == [[1, 2], [3, 4]]


def test_chunked_handles_remainder():
    assert list(_chunked([1, 2, 3], 2)) == [[1, 2], [3]]


def test_chunked_empty():
    assert list(_chunked([], 5)) == []


def test_chunked_is_lazy():
    """Chunking must not materialise the whole input -- CSVs stream."""

    def infinite():
        n = 0
        while True:
            yield n
            n += 1

    chunks = _chunked(infinite(), 3)
    assert next(chunks) == [0, 1, 2]
    assert next(chunks) == [3, 4, 5]


# ---------------------------------------------------------------------------
# resolve_writer
# ---------------------------------------------------------------------------


def test_resolve_writer_from_str_path(tmp_path):
    writer = resolve_writer(str(tmp_path / "x.db"))
    assert isinstance(writer, PathRowWriter)


def test_resolve_writer_from_pathlib_path(tmp_path):
    writer = resolve_writer(Path(tmp_path / "x.db"))
    assert isinstance(writer, PathRowWriter)


def test_resolve_writer_from_datasette_database():
    class FakeDatasetteDatabase:
        async def execute_write_fn(self, fn, **kwargs):
            return None

    writer = resolve_writer(FakeDatasetteDatabase())
    assert isinstance(writer, DatasetteRowWriter)


def test_resolve_writer_passes_through_existing_writer(tmp_path):
    writer = PathRowWriter(tmp_path / "x.db")
    assert resolve_writer(writer) is writer


def test_resolve_writer_rejects_unusable_target():
    with pytest.raises(TypeError, match="Cannot write to"):
        resolve_writer(object())


# ---------------------------------------------------------------------------
# PathRowWriter
# ---------------------------------------------------------------------------


async def test_path_writer_inserts(tmp_path):
    path = tmp_path / "t.db"
    writer = PathRowWriter(path)

    assert await writer.insert_rows("people", [{"name": "Alice"}, {"name": "Bob"}]) == 2
    assert [r["name"] for r in Database(str(path))["people"].rows] == ["Alice", "Bob"]


async def test_path_writer_empty_creates_no_table(tmp_path):
    path = tmp_path / "t.db"
    assert await PathRowWriter(path).insert_rows("nothing", []) == 0
    assert "nothing" not in Database(str(path)).table_names()


async def test_path_writer_discovers_new_columns(tmp_path):
    path = tmp_path / "t.db"
    writer = PathRowWriter(path)

    await writer.insert_rows("t", [{"a": 1}])
    await writer.insert_rows("t", [{"a": 2, "b": "new"}])

    assert set(Database(str(path))["t"].columns_dict) == {"a", "b"}


# ---------------------------------------------------------------------------
# DatasetteRowWriter
# ---------------------------------------------------------------------------


class RecordingDatabase:
    """Captures what gets handed to execute_write_fn, and runs it for real."""

    def __init__(self, path):
        self.path = str(path)
        self.calls = 0

    async def execute_write_fn(self, fn, **kwargs):
        self.calls += 1
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                return fn(conn)
        finally:
            conn.close()


async def test_datasette_writer_routes_through_execute_write_fn(tmp_path):
    db = RecordingDatabase(tmp_path / "t.db")
    writer = DatasetteRowWriter(db)

    assert await writer.insert_rows("people", [{"name": "Alice"}]) == 1
    assert db.calls == 1


async def test_datasette_writer_skips_empty_without_writing(tmp_path):
    """An empty batch must not occupy the shared write thread at all."""
    db = RecordingDatabase(tmp_path / "t.db")

    assert await DatasetteRowWriter(db).insert_rows("t", []) == 0
    assert db.calls == 0


# ---------------------------------------------------------------------------
# Against a real Datasette database
# ---------------------------------------------------------------------------


@pytest.fixture
def datasette_database(tmp_path):
    """A real Datasette Database backed by a file, with a real write thread."""
    from datasette.app import Datasette

    path = tmp_path / "data.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE _seed (a integer)")
    conn.commit()
    conn.close()

    ds = Datasette([str(path)])
    return ds, ds.get_database("data")


async def test_real_datasette_write_is_visible_to_readers(datasette_database):
    _, db = datasette_database
    writer = DatasetteRowWriter(db)

    await writer.insert_rows("loaded", [{"county": "Dublin", "value": 1}])

    rows = (await db.execute("SELECT county, value FROM loaded")).rows
    assert [dict(r) for r in rows] == [{"county": "Dublin", "value": 1}]


async def test_real_datasette_write_alters_schema_across_batches(datasette_database):
    """alter=True still works through execute_write_fn, so later batches can
    introduce columns the first batch didn't have."""
    _, db = datasette_database
    writer = DatasetteRowWriter(db)

    await writer.insert_rows("loaded", [{"a": 1}])
    await writer.insert_rows("loaded", [{"a": 2, "b": "later"}])

    row = (await db.execute("SELECT * FROM loaded ORDER BY a")).rows[1]
    assert dict(row) == {"a": 2, "b": "later"}


async def test_real_datasette_load_datastore_resource(datasette_database):
    """Full datastore load against a real write connection."""
    _, db = datasette_database

    class FakeProvider:
        async def _get(self, action, params=None, datastore=False):
            offset = params["offset"]
            if offset >= 3:
                return {"records": []}
            return {"records": [{"id": offset, "name": f"row-{offset}"}]}

    total = await load_datastore_resource(
        provider=FakeProvider(),
        resource_id="res-1",
        destination=db,
        table="from_datastore",
        limit=10,
        batch_size=1,
    )

    assert total == 3
    rows = (await db.execute("SELECT name FROM from_datastore ORDER BY id")).rows
    assert [r["name"] for r in rows] == ["row-0", "row-1", "row-2"]


# ---------------------------------------------------------------------------
# CSV batching
# ---------------------------------------------------------------------------


def _mock_csv_client(csv_bytes: bytes):
    from unittest.mock import AsyncMock, MagicMock

    response = MagicMock()
    response.content = csv_bytes
    response.raise_for_status = MagicMock()

    client = AsyncMock()
    client.get = AsyncMock(return_value=response)

    cls = MagicMock()
    cls.return_value.__aenter__ = AsyncMock(return_value=client)
    cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return cls


class CountingWriter:
    def __init__(self):
        self.batches = []

    async def insert_rows(self, table, rows, replace=False):
        rows = list(rows)
        self.batches.append(len(rows))
        return len(rows)


async def test_load_csv_url_writes_in_batches(monkeypatch):
    """A large CSV becomes several short writes, not one long one holding the
    shared write thread for the whole load."""
    rows = "\n".join(f"{i},v{i}" for i in range(10))
    csv_bytes = f"id,val\n{rows}\n".encode()

    monkeypatch.setattr("datasette_open_data.loader.httpx.AsyncClient", _mock_csv_client(csv_bytes))

    writer = CountingWriter()
    total = await load_csv_url(
        csv_url="https://x/y.csv", destination=writer, table="t", batch_size=4
    )

    assert total == 10
    assert writer.batches == [4, 4, 2]


async def test_load_csv_url_single_batch_when_small(monkeypatch):
    csv_bytes = b"id,val\n1,a\n2,b\n"
    monkeypatch.setattr("datasette_open_data.loader.httpx.AsyncClient", _mock_csv_client(csv_bytes))

    writer = CountingWriter()
    await load_csv_url(csv_url="https://x/y.csv", destination=writer, table="t")

    assert writer.batches == [2]


async def test_load_csv_url_empty_writes_nothing(monkeypatch):
    monkeypatch.setattr(
        "datasette_open_data.loader.httpx.AsyncClient", _mock_csv_client(b"id,val\n")
    )

    writer = CountingWriter()
    total = await load_csv_url(csv_url="https://x/y.csv", destination=writer, table="t")

    assert total == 0
    assert writer.batches == []
