from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlite_utils import Database

from datasette_open_data.loader import (
    LoadError,
    _insert_rows,
    load_csv_url,
    load_datastore_resource,
    load_resource,
    safe_table_name,
)
from datasette_open_data.models import Resource
from datasette_open_data.providers.ckan import CKANProvider


# ---------------------------------------------------------------------------
# safe_table_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("hello world", "hello_world"),
        ("UPPER CASE", "upper_case"),
        ("123abc", "123abc"),
        ("has-dashes", "has_dashes"),
        ("_leading_trailing_", "leading_trailing"),
        ("!@#$%", "open_data_resource"),
        ("", "open_data_resource"),
        ("multiple   spaces", "multiple_spaces"),
    ],
)
def test_safe_table_name(value, expected):
    assert safe_table_name(value) == expected


# ---------------------------------------------------------------------------
# _insert_rows
# ---------------------------------------------------------------------------


def test_insert_rows_returns_count(tmp_path):
    db_path = str(tmp_path / "test.db")
    rows = [{"name": "Alice", "score": 10}, {"name": "Bob", "score": 20}]
    count = _insert_rows(db_path, "scores", rows)
    assert count == 2

    db = Database(db_path)
    assert list(db["scores"].rows) == rows


def test_insert_rows_empty_creates_table(tmp_path):
    db_path = str(tmp_path / "test.db")
    count = _insert_rows(db_path, "empty_table", [])
    assert count == 0

    db = Database(db_path)
    assert "empty_table" in db.table_names()


def test_insert_rows_accumulates_without_pk(tmp_path):
    db_path = str(tmp_path / "test.db")
    _insert_rows(db_path, "t", [{"v": "a"}])
    _insert_rows(db_path, "t", [{"v": "b"}])
    rows = list(Database(db_path)["t"].rows)
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# load_datastore_resource
# ---------------------------------------------------------------------------


async def test_load_datastore_resource_basic(tmp_path):
    db_path = str(tmp_path / "test.db")
    provider = CKANProvider(name="test", base_url="http://example.com")

    async def fake_get(action, params=None, datastore=False):
        offset = (params or {}).get("offset", 0)
        if offset == 0:
            return {"records": [{"name": "Alice"}, {"name": "Bob"}]}
        return {"records": []}

    provider._get = fake_get

    count = await load_datastore_resource(
        provider, "res-123", db_path, table="people", batch_size=1000
    )
    assert count == 2

    db = Database(db_path)
    assert "people" in db.table_names()
    assert len(list(db["people"].rows)) == 2


async def test_load_datastore_resource_respects_limit(tmp_path):
    db_path = str(tmp_path / "test.db")
    provider = CKANProvider(name="test", base_url="http://example.com")

    async def fake_get(action, params=None, datastore=False):
        return {"records": [{"n": i} for i in range((params or {}).get("limit", 5))]}

    provider._get = fake_get

    count = await load_datastore_resource(
        provider, "res-123", db_path, table="t", limit=3, batch_size=1000
    )
    assert count == 3


async def test_load_datastore_resource_empty(tmp_path):
    db_path = str(tmp_path / "test.db")
    provider = CKANProvider(name="test", base_url="http://example.com")

    async def fake_get(action, params=None, datastore=False):
        return {"records": []}

    provider._get = fake_get

    count = await load_datastore_resource(provider, "res-123", db_path, table="empty")
    assert count == 0


async def test_load_datastore_resource_wraps_error(tmp_path):
    db_path = str(tmp_path / "test.db")
    provider = CKANProvider(name="test", base_url="http://example.com")

    call_count = 0

    async def fake_get(action, params=None, datastore=False):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"records": [{"x": 1}]}
        raise RuntimeError("network failure")

    provider._get = fake_get

    with pytest.raises(LoadError, match="1 rows already written"):
        await load_datastore_resource(
            provider, "res-123", db_path, table="t", batch_size=1
        )


# ---------------------------------------------------------------------------
# load_csv_url
# ---------------------------------------------------------------------------


def _mock_http_client(content: bytes):
    mock_response = MagicMock()
    mock_response.content = content
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    mock_cls = MagicMock()
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_cls


async def test_load_csv_url_basic(tmp_path):
    db_path = str(tmp_path / "test.db")
    csv_content = b"name,value\nalice,1\nbob,2\n"

    with patch("datasette_open_data.loader.httpx.AsyncClient", _mock_http_client(csv_content)):
        count = await load_csv_url("http://example.com/data.csv", db_path, "my_table")

    assert count == 2
    db = Database(db_path)
    assert "my_table" in db.table_names()
    rows = list(db["my_table"].rows)
    assert rows[0]["name"] == "alice"
    assert rows[1]["value"] == "2"


async def test_load_csv_url_sanitises_table_name(tmp_path):
    db_path = str(tmp_path / "test.db")
    csv_content = b"x\n1\n"

    with patch("datasette_open_data.loader.httpx.AsyncClient", _mock_http_client(csv_content)):
        await load_csv_url("http://example.com/data.csv", db_path, "My Table!")

    db = Database(db_path)
    assert "my_table" in db.table_names()


async def test_load_csv_url_raises_load_error_on_http_error(tmp_path):
    db_path = str(tmp_path / "test.db")

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=mock_response
        )
    )
    mock_cls = MagicMock()
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("datasette_open_data.loader.httpx.AsyncClient", mock_cls):
        with pytest.raises(LoadError, match="HTTP 404"):
            await load_csv_url("http://example.com/missing.csv", db_path, "t")


async def test_load_csv_url_raises_load_error_on_timeout(tmp_path):
    db_path = str(tmp_path / "test.db")

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=httpx.TimeoutException("timed out", request=MagicMock())
    )
    mock_cls = MagicMock()
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("datasette_open_data.loader.httpx.AsyncClient", mock_cls):
        with pytest.raises(LoadError, match="Timed out"):
            await load_csv_url("http://example.com/slow.csv", db_path, "t")


# ---------------------------------------------------------------------------
# load_resource
# ---------------------------------------------------------------------------


async def test_load_resource_datastore_path(tmp_path):
    db_path = str(tmp_path / "test.db")
    provider = CKANProvider(name="test", base_url="http://example.com")
    resource = Resource(id="res-1", name="mydata", datastore_active=True)

    async def fake_get(action, params=None, datastore=False):
        return {"records": [{"x": 1}]}

    provider._get = fake_get

    count = await load_resource(provider, resource, db_path)
    assert count == 1


async def test_load_resource_csv_path(tmp_path):
    db_path = str(tmp_path / "test.db")
    provider = CKANProvider(name="test", base_url="http://example.com")
    resource = Resource(
        id="res-2", name="mycsv", format="CSV", url="http://example.com/data.csv"
    )
    csv_content = b"a,b\n1,2\n"

    with patch("datasette_open_data.loader.httpx.AsyncClient", _mock_http_client(csv_content)):
        count = await load_resource(provider, resource, db_path)

    assert count == 1


async def test_load_resource_csv_no_url_raises(tmp_path):
    db_path = str(tmp_path / "test.db")
    provider = CKANProvider(name="test", base_url="http://example.com")
    resource = Resource(id="res-3", name="nourl", format="CSV", url=None)

    with pytest.raises(LoadError, match="no URL to download from"):
        await load_resource(provider, resource, db_path)


async def test_load_resource_unsupported_raises(tmp_path):
    db_path = str(tmp_path / "test.db")
    provider = CKANProvider(name="test", base_url="http://example.com")
    resource = Resource(id="res-4", name="weird", format="XLS")

    with pytest.raises(ValueError, match="unsupported format"):
        await load_resource(provider, resource, db_path)
