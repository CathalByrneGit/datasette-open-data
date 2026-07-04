from __future__ import annotations

import pytest

from datasette_open_data.providers.socrata import SocrataError, SocrataProvider


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


def test_base_url_trailing_slash_stripped():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us/")
    assert p.base_url == "https://data.cityofnewyork.us"


def test_title_defaults_to_name():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")
    assert p.title == "nyc"


def test_explicit_title():
    p = SocrataProvider(name="nyc", title="NYC Open Data", base_url="https://data.cityofnewyork.us")
    assert p.title == "NYC Open Data"


# ---------------------------------------------------------------------------
# _resource_from_view
# ---------------------------------------------------------------------------


def test_resource_from_view_csv_export_url():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")
    r = p._resource_from_view("abc1-def2")
    assert r.id == "abc1-def2"
    assert r.format == "CSV"
    assert r.url == "https://data.cityofnewyork.us/resource/abc1-def2.csv?$limit=50000"
    assert r.datastore_active is False


def test_resource_from_view_title_as_description():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")
    r = p._resource_from_view("abc1-def2", title="Parks Data")
    assert r.description == "Parks Data"


# ---------------------------------------------------------------------------
# _summary_from_result
# ---------------------------------------------------------------------------


def test_summary_from_result_basic():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")
    result = {
        "resource": {
            "id": "abc1-def2",
            "name": "Parks and Recreation",
            "description": "Info about parks",
        },
        "classification": {
            "tags": ["parks", "recreation"],
        },
    }
    s = p._summary_from_result(result)
    assert s.id == "abc1-def2"
    assert s.title == "Parks and Recreation"
    assert s.notes == "Info about parks"
    assert "parks" in s.tags
    assert "recreation" in s.tags
    assert len(s.resources) == 1
    assert s.resources[0].format == "CSV"


def test_summary_from_result_missing_tags():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")
    result = {"resource": {"id": "abc1-def2", "name": "Dataset"}, "classification": {}}
    s = p._summary_from_result(result)
    assert s.tags == []


def test_summary_from_result_no_org():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")
    result = {"resource": {"id": "x1y2-z3w4", "name": "X"}, "classification": {}}
    s = p._summary_from_result(result)
    assert s.organization is None


# ---------------------------------------------------------------------------
# _dataset_from_view
# ---------------------------------------------------------------------------


def test_dataset_from_view_full():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")
    data = {
        "id": "abc1-def2",
        "name": "Building Permits",
        "description": "NYC building permits",
        "attribution": "Department of Buildings",
        "tags": [{"name": "buildings"}, {"name": "permits"}],
        "license": {"name": "Public Domain"},
        "webUri": "https://data.cityofnewyork.us/Housing/Building-Permits/abc1-def2",
    }
    d = p._dataset_from_view(data)
    assert d.id == "abc1-def2"
    assert d.title == "Building Permits"
    assert d.organization == "Department of Buildings"
    assert "buildings" in d.tags
    assert d.license_title == "Public Domain"
    assert "abc1-def2" in d.url


def test_dataset_from_view_string_tags():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")
    data = {
        "id": "abc1-def2",
        "name": "X",
        "tags": ["alpha", "beta"],
    }
    d = p._dataset_from_view(data)
    assert d.tags == ["alpha", "beta"]


def test_dataset_from_view_string_license():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")
    data = {"id": "abc1-def2", "name": "X", "license": "CC BY 4.0"}
    d = p._dataset_from_view(data)
    assert d.license_title == "CC BY 4.0"


def test_dataset_from_view_fallback_url():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")
    data = {"id": "abc1-def2", "name": "X"}
    d = p._dataset_from_view(data)
    assert d.url == "https://data.cityofnewyork.us/d/abc1-def2"


# ---------------------------------------------------------------------------
# search (mocked _get)
# ---------------------------------------------------------------------------


async def test_search_returns_summaries():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")

    async def fake_get(path, params=None):
        assert path == "/api/catalog/v1"
        assert params.get("q") == "parks"
        return {
            "results": [
                {
                    "resource": {
                        "id": "abc1-def2",
                        "name": "Parks Data",
                        "description": "NYC parks",
                    },
                    "classification": {"tags": ["parks"]},
                }
            ],
            "resultSetSize": 1,
        }

    p._get = fake_get
    results = await p.search("parks")
    assert len(results) == 1
    assert results[0].id == "abc1-def2"
    assert results[0].title == "Parks Data"
    assert "parks" in results[0].tags


async def test_search_empty():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")

    async def fake_get(path, params=None):
        return {"results": [], "resultSetSize": 0}

    p._get = fake_get
    assert await p.search("nothing found") == []


# ---------------------------------------------------------------------------
# dataset (mocked _get)
# ---------------------------------------------------------------------------


async def test_dataset_fetches_by_id():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")

    async def fake_get(path, params=None):
        assert path == "/api/views/abc1-def2.json"
        return {
            "id": "abc1-def2",
            "name": "Schools",
            "attribution": "Dept of Education",
            "tags": [],
        }

    p._get = fake_get
    d = await p.dataset("abc1-def2")
    assert d.id == "abc1-def2"
    assert d.organization == "Dept of Education"
    assert len(d.resources) == 1


# ---------------------------------------------------------------------------
# groups (mocked _get)
# ---------------------------------------------------------------------------


async def test_groups_returns_categories():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")

    async def fake_get(path, params=None):
        return [
            {"category": "Education", "count": 50},
            {"category": "Health", "count": 30},
        ]

    p._get = fake_get
    groups = await p.groups()
    assert len(groups) == 2
    names = [g["name"] for g in groups]
    assert "Education" in names
    assert "Health" in names


async def test_groups_skips_empty_category():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")

    async def fake_get(path, params=None):
        return [{"category": "", "count": 5}, {"category": "Health", "count": 10}]

    p._get = fake_get
    groups = await p.groups()
    assert len(groups) == 1


# ---------------------------------------------------------------------------
# tags (mocked _get)
# ---------------------------------------------------------------------------


async def test_tags_returns_list():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")

    async def fake_get(path, params=None):
        return [{"tag": "parks", "count": 10}, {"tag": "health", "count": 5}]

    p._get = fake_get
    tags = await p.tags()
    assert tags == ["parks", "health"]


async def test_tags_skips_empty():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")

    async def fake_get(path, params=None):
        return [{"tag": "", "count": 1}, {"tag": "valid", "count": 2}]

    p._get = fake_get
    assert await p.tags() == ["valid"]


# ---------------------------------------------------------------------------
# organizations
# ---------------------------------------------------------------------------


async def test_organizations_returns_empty():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")
    assert await p.organizations() == []


# ---------------------------------------------------------------------------
# datastore_preview (mocked _get)
# ---------------------------------------------------------------------------


async def test_datastore_preview_normalises_response():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")

    async def fake_get(path, params=None):
        return [{"name": "Central Park", "area": "843 acres"}]

    p._get = fake_get
    result = await p.datastore_preview("abc1-def2", limit=1)
    assert result["records"] == [{"name": "Central Park", "area": "843 acres"}]
    field_ids = [f["id"] for f in result["fields"]]
    assert "name" in field_ids
    assert "area" in field_ids


async def test_datastore_preview_raises_on_non_list():
    p = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")

    async def fake_get(path, params=None):
        return {"error": "not found"}

    p._get = fake_get
    with pytest.raises(SocrataError, match="Unexpected response"):
        await p.datastore_preview("bad-id")
