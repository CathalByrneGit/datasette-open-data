from __future__ import annotations

import pytest

from datasette_open_data.providers.ckan import CKANProvider, CKANError


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


def test_urls_trailing_slash_stripped():
    p = CKANProvider(
        name="centralbank",
        title="Central Bank",
        base_url="https://opendata.centralbank.ie/",
        datastore_api_base_url="https://opendata.centralbank.ie/en_GB/api/3",
    )
    assert p.api_base_url == "https://opendata.centralbank.ie/api/3"
    assert p.datastore_api_base_url == "https://opendata.centralbank.ie/en_GB/api/3"


def test_defaults_api_base_url_from_base():
    p = CKANProvider(name="test", base_url="https://data.example.com")
    assert p.api_base_url == "https://data.example.com/api/3"
    assert p.datastore_api_base_url == "https://data.example.com/api/3"


def test_title_defaults_to_name():
    p = CKANProvider(name="myportal", base_url="https://example.com")
    assert p.title == "myportal"


def test_explicit_title():
    p = CKANProvider(name="myportal", title="My Portal", base_url="https://example.com")
    assert p.title == "My Portal"


# ---------------------------------------------------------------------------
# _resource_from_ckan
# ---------------------------------------------------------------------------


def test_resource_from_ckan_full():
    p = CKANProvider(name="test", base_url="https://example.com")
    data = {
        "id": "res-123",
        "name": "Annual Report",
        "description": "The annual report",
        "format": "CSV",
        "url": "https://example.com/data.csv",
        "datastore_active": True,
    }
    r = p._resource_from_ckan(data)
    assert r.id == "res-123"
    assert r.name == "Annual Report"
    assert r.format == "CSV"
    assert r.datastore_active is True
    assert r.url == "https://example.com/data.csv"


def test_resource_from_ckan_minimal():
    p = CKANProvider(name="test", base_url="https://example.com")
    r = p._resource_from_ckan({"id": "r1"})
    assert r.id == "r1"
    assert r.name is None
    assert r.datastore_active is False


# ---------------------------------------------------------------------------
# _summary_from_ckan
# ---------------------------------------------------------------------------


def test_summary_from_ckan_with_org():
    p = CKANProvider(name="test", base_url="https://example.com")
    data = {
        "id": "pkg-1",
        "name": "dataset-1",
        "title": "Dataset One",
        "notes": "Some notes",
        "organization": {"title": "Stats Office", "name": "stats-office"},
        "tags": [{"display_name": "finance"}, {"name": "economy"}],
        "resources": [],
    }
    s = p._summary_from_ckan(data)
    assert s.id == "pkg-1"
    assert s.title == "Dataset One"
    assert s.organization == "Stats Office"
    assert "finance" in s.tags
    assert "economy" in s.tags


def test_summary_from_ckan_no_org():
    p = CKANProvider(name="test", base_url="https://example.com")
    data = {"id": "pkg-2", "name": "dataset-2", "tags": [], "resources": []}
    s = p._summary_from_ckan(data)
    assert s.organization is None
    assert s.title == "dataset-2"


def test_summary_from_ckan_tags_skip_empty():
    p = CKANProvider(name="test", base_url="https://example.com")
    data = {
        "id": "pkg-3",
        "tags": [{"display_name": ""}, {"name": ""}, {"display_name": "valid"}],
        "resources": [],
    }
    s = p._summary_from_ckan(data)
    assert s.tags == ["valid"]


# ---------------------------------------------------------------------------
# _dataset_from_ckan
# ---------------------------------------------------------------------------


def test_dataset_from_ckan_extra_fields():
    p = CKANProvider(name="test", base_url="https://example.com")
    data = {
        "id": "pkg-4",
        "name": "dataset-4",
        "title": "Dataset Four",
        "tags": [],
        "resources": [],
        "license_title": "Open Government Licence",
        "url": "https://example.com/dataset/4",
    }
    d = p._dataset_from_ckan(data)
    assert d.license_title == "Open Government Licence"
    assert d.url == "https://example.com/dataset/4"


# ---------------------------------------------------------------------------
# search (mocked _get)
# ---------------------------------------------------------------------------


async def test_search_returns_summaries():
    p = CKANProvider(name="test", base_url="https://example.com")

    async def fake_get(action, params=None, datastore=False):
        assert action == "package_search"
        assert params["q"] == "inflation"
        return {
            "results": [
                {
                    "id": "pkg-1",
                    "name": "inflation-data",
                    "title": "Inflation Data",
                    "organization": {"title": "Central Bank"},
                    "tags": [],
                    "resources": [],
                }
            ]
        }

    p._get = fake_get
    results = await p.search("inflation")
    assert len(results) == 1
    assert results[0].id == "pkg-1"
    assert results[0].title == "Inflation Data"
    assert results[0].organization == "Central Bank"


async def test_search_empty_results():
    p = CKANProvider(name="test", base_url="https://example.com")

    async def fake_get(action, params=None, datastore=False):
        return {"results": []}

    p._get = fake_get
    results = await p.search("nothing")
    assert results == []


# ---------------------------------------------------------------------------
# dataset (mocked _get)
# ---------------------------------------------------------------------------


async def test_dataset_fetches_by_id():
    p = CKANProvider(name="test", base_url="https://example.com")

    async def fake_get(action, params=None, datastore=False):
        assert action == "package_show"
        assert params["id"] == "pkg-abc"
        return {
            "id": "pkg-abc",
            "name": "my-dataset",
            "title": "My Dataset",
            "tags": [],
            "resources": [
                {
                    "id": "res-1",
                    "name": "Data CSV",
                    "format": "CSV",
                    "url": "https://example.com/data.csv",
                    "datastore_active": False,
                }
            ],
        }

    p._get = fake_get
    d = await p.dataset("pkg-abc")
    assert d.id == "pkg-abc"
    assert len(d.resources) == 1
    assert d.resources[0].format == "CSV"


# ---------------------------------------------------------------------------
# CKANError
# ---------------------------------------------------------------------------


async def test_get_raises_ckan_error_on_failure(httpx_mock=None):
    p = CKANProvider(name="test", base_url="https://example.com")

    async def fake_get(action, params=None, datastore=False):
        raise CKANError("Not found")

    p._get = fake_get
    with pytest.raises(CKANError, match="Not found"):
        await p._get("package_show", {"id": "bad"})
