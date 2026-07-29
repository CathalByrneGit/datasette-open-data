"""Tests for provider.iter_catalog(), the interface build_catalog.py consumes.

Every provider must yield records in the CKAN package shape so that
scripts/build_catalog.py's upsert_package can store them unchanged.
"""

from __future__ import annotations

import pytest

from datasette_open_data.providers.ckan import CKANProvider
from datasette_open_data.providers.pxstat import PxStatError, PxStatProvider
from datasette_open_data.providers.socrata import SocrataProvider


async def _collect(provider, **kwargs):
    return [record async for record in provider.iter_catalog(**kwargs)]


# ---------------------------------------------------------------------------
# Every provider implements it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider",
    [
        CKANProvider(name="c", base_url="https://ckan.example.com"),
        SocrataProvider(name="s", base_url="https://socrata.example.com"),
        PxStatProvider(name="p", base_url="https://ws.example.ie"),
    ],
    ids=["ckan", "socrata", "pxstat"],
)
def test_all_providers_implement_iter_catalog(provider):
    assert hasattr(provider, "iter_catalog")


# ---------------------------------------------------------------------------
# CKAN
# ---------------------------------------------------------------------------


async def test_ckan_iter_catalog_pages_and_expands():
    provider = CKANProvider(name="c", base_url="https://ckan.example.com")
    calls = []

    async def fake_get(action, params=None, datastore=False):
        calls.append((action, dict(params or {})))
        if action == "package_search":
            start = params["start"]
            if start == 0:
                return {"count": 3, "results": [{"id": "a"}, {"id": "b"}]}
            return {"count": 3, "results": [{"id": "c"}]}
        return {"id": params["id"], "title": f"Package {params['id']}"}

    provider._get = fake_get
    records = await _collect(provider, rows_per_page=2)

    assert [r["id"] for r in records] == ["a", "b", "c"]
    assert [r["title"] for r in records] == ["Package a", "Package b", "Package c"]
    # package_show is called per package, since package_search omits resources
    assert sum(1 for action, _ in calls if action == "package_show") == 3


async def test_ckan_iter_catalog_respects_limit():
    provider = CKANProvider(name="c", base_url="https://ckan.example.com")

    async def fake_get(action, params=None, datastore=False):
        if action == "package_search":
            return {"count": 100, "results": [{"id": f"p{i}"} for i in range(10)]}
        return {"id": params["id"]}

    provider._get = fake_get
    records = await _collect(provider, limit=3)

    assert len(records) == 3


async def test_ckan_iter_catalog_skips_unreadable_package():
    """One broken package must not abort the whole crawl."""
    from datasette_open_data.providers.ckan import CKANError

    provider = CKANProvider(name="c", base_url="https://ckan.example.com")

    async def fake_get(action, params=None, datastore=False):
        if action == "package_search":
            return {"count": 3, "results": [{"id": "a"}, {"id": "bad"}, {"id": "c"}]}
        if params["id"] == "bad":
            raise CKANError("Not found")
        return {"id": params["id"]}

    provider._get = fake_get
    records = await _collect(provider)

    assert [r["id"] for r in records] == ["a", "c"]


async def test_ckan_iter_catalog_stops_on_empty_page():
    provider = CKANProvider(name="c", base_url="https://ckan.example.com")

    async def fake_get(action, params=None, datastore=False):
        return {"count": 0, "results": []}

    provider._get = fake_get
    assert await _collect(provider) == []


# ---------------------------------------------------------------------------
# PxStat
# ---------------------------------------------------------------------------


_NAVIGATION = [
    {
        "ThmCode": 1,
        "ThmValue": "Population",
        "subject": [{"SbjCode": 12, "SbjValue": "Vital Statistics"}],
    }
]

_COLLECTION = {
    "link": {
        "item": [
            {
                "label": "Births Annual",
                "updated": "2024-01-01",
                "extension": {
                    "matrix": "VSA01",
                    "subject": {"SbjCode": 12, "SbjValue": "Vital Statistics"},
                },
            },
            {
                "label": "Consumer Prices",
                "extension": {"matrix": "CPA01"},
            },
        ]
    }
}


def _pxstat_provider(navigation=_NAVIGATION, collection=_COLLECTION):
    provider = PxStatProvider(name="cso", base_url="https://ws.cso.ie")

    async def fake_rpc(method, params=None):
        if method == "PxStat.System.Navigation.Navigation_API.Read":
            if isinstance(navigation, Exception):
                raise navigation
            return navigation
        return collection

    provider._rpc = fake_rpc
    return provider


async def test_pxstat_iter_catalog_shape():
    records = await _collect(_pxstat_provider())

    assert [r["id"] for r in records] == ["VSA01", "CPA01"]

    births = records[0]
    assert births["title"] == "Births Annual"
    assert births["metadata_modified"] == "2024-01-01"
    assert births["tags"] == [
        {"name": "Vital Statistics", "display_name": "Vital Statistics"}
    ]
    assert births["groups"][0]["title"] == "Population"


async def test_pxstat_iter_catalog_builds_csv_resource():
    records = await _collect(_pxstat_provider())
    resource = records[0]["resources"][0]

    assert resource["format"] == "CSV"
    assert resource["datastore_active"] is False
    assert "VSA01" in resource["url"]
    assert resource["url"].endswith("/CSV/en/")


async def test_pxstat_iter_catalog_item_without_subject_has_no_tags():
    records = await _collect(_pxstat_provider())
    prices = records[1]

    assert prices["tags"] == []
    assert prices["groups"] == []


async def test_pxstat_iter_catalog_survives_navigation_failure():
    """Navigation is optional; the catalog is still usable without themes."""
    records = await _collect(_pxstat_provider(navigation=PxStatError("nav down")))

    assert [r["id"] for r in records] == ["VSA01", "CPA01"]
    assert all(r["groups"] == [] for r in records)


async def test_pxstat_iter_catalog_respects_limit():
    records = await _collect(_pxstat_provider(), limit=1)
    assert [r["id"] for r in records] == ["VSA01"]


async def test_pxstat_iter_catalog_skips_items_without_matrix():
    collection = {"link": {"item": [{"label": "No matrix", "extension": {}}]}}
    records = await _collect(_pxstat_provider(collection=collection))
    assert records == []


async def test_pxstat_iter_catalog_does_not_call_read_metadata():
    """Per-table metadata would mean ~12,600 extra requests for CSO."""
    provider = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    methods = []

    async def fake_rpc(method, params=None):
        methods.append(method)
        if method == "PxStat.System.Navigation.Navigation_API.Read":
            return _NAVIGATION
        return _COLLECTION

    provider._rpc = fake_rpc
    await _collect(provider)

    assert "PxStat.Data.Cube_API.ReadMetadata" not in methods


# ---------------------------------------------------------------------------
# Socrata
# ---------------------------------------------------------------------------


def _socrata_provider(pages):
    provider = SocrataProvider(name="nyc", base_url="https://data.cityofnewyork.us")
    calls = {"n": 0}

    async def fake_get(path, params=None):
        page = pages[min(calls["n"], len(pages) - 1)]
        calls["n"] += 1
        return page

    provider._get = fake_get
    return provider


async def test_socrata_iter_catalog_shape():
    provider = _socrata_provider(
        [
            {
                "resultSetSize": 1,
                "results": [
                    {
                        "resource": {
                            "id": "abcd-1234",
                            "name": "Tree Census",
                            "description": "Street trees",
                            "attribution": "Parks Dept",
                            "createdAt": "2020-01-01",
                            "updatedAt": "2024-05-05",
                        },
                        "classification": {
                            "tags": ["trees", "environment"],
                            "domain_category": "Environment",
                        },
                        "metadata": {"license": "CC0"},
                        "permalink": "https://data.cityofnewyork.us/d/abcd-1234",
                    }
                ],
            }
        ]
    )

    records = await _collect(provider)
    assert len(records) == 1

    record = records[0]
    assert record["id"] == "abcd-1234"
    assert record["title"] == "Tree Census"
    assert record["notes"] == "Street trees"
    assert record["organization"]["title"] == "Parks Dept"
    assert record["license_title"] == "CC0"
    assert record["metadata_modified"] == "2024-05-05"
    assert [t["name"] for t in record["tags"]] == ["trees", "environment"]
    assert record["groups"][0]["title"] == "Environment"

    resource = record["resources"][0]
    assert resource["format"] == "CSV"
    assert resource["url"].endswith("/resource/abcd-1234.csv?$limit=50000")


async def test_socrata_iter_catalog_no_attribution_gives_empty_org():
    provider = _socrata_provider(
        [
            {
                "resultSetSize": 1,
                "results": [{"resource": {"id": "x", "name": "N"}, "classification": {}}],
            }
        ]
    )
    records = await _collect(provider)
    assert records[0]["organization"] == {}


async def test_socrata_iter_catalog_stops_on_empty_results():
    provider = _socrata_provider([{"resultSetSize": 0, "results": []}])
    assert await _collect(provider) == []


async def test_socrata_iter_catalog_respects_limit():
    provider = _socrata_provider(
        [
            {
                "resultSetSize": 100,
                "results": [
                    {"resource": {"id": f"id-{i}", "name": f"N{i}"}, "classification": {}}
                    for i in range(10)
                ],
            }
        ]
    )
    records = await _collect(provider, limit=4)
    assert len(records) == 4


async def test_socrata_iter_catalog_skips_result_without_id():
    provider = _socrata_provider(
        [
            {
                "resultSetSize": 1,
                "results": [{"resource": {"name": "no id"}, "classification": {}}],
            }
        ]
    )
    assert await _collect(provider) == []
