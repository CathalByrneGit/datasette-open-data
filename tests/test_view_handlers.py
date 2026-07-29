"""Tests for the route handlers in views.py.

test_views.py covers the pure helpers; this file covers the view functions
themselves, with an emphasis on the error paths that used to surface as 500s.
"""

from __future__ import annotations

import json

import pytest
from conftest import FakeDatabase, FakeDatasette, FakeRequest

from datasette_open_data.loader import LoadError
from datasette_open_data.models import Dataset, DatasetSummary, Resource
from datasette_open_data.views import (
    dataset_view,
    groups_view,
    index_view,
    load_resource_view,
    organizations_view,
    resource_preview_view,
    search_view,
    tags_view,
)

JSON_REQUEST_ARGS = {"_format": "json"}


def _body(response):
    body = response.body
    return body.decode() if isinstance(body, bytes) else body


def _json(response):
    return json.loads(_body(response))


class StubProvider:
    name = "alpha"
    title = "Alpha"
    type = "ckan"
    base_url = "https://alpha.example.com"

    def __init__(self, **overrides):
        self._overrides = overrides

    async def _maybe_raise(self, key):
        exc = self._overrides.get(f"{key}_error")
        if exc:
            raise exc

    async def search(self, query, rows=20, start=0):
        await self._maybe_raise("search")
        return self._overrides.get(
            "search_results", [DatasetSummary(id="p1", name="p1", title="Package One")]
        )

    async def dataset(self, dataset_id):
        await self._maybe_raise("dataset")
        return self._overrides.get(
            "dataset_result", Dataset(id=dataset_id, name=dataset_id, title="A Dataset")
        )

    async def resource(self, resource_id):
        await self._maybe_raise("resource")
        return self._overrides.get(
            "resource_result", Resource(id=resource_id, name="thing", datastore_active=True)
        )

    async def datastore_preview(self, resource_id, limit=10):
        await self._maybe_raise("preview")
        return {"records": [], "fields": [], "total": 0}

    async def groups(self):
        await self._maybe_raise("groups")
        return [{"id": "g1", "name": "Group One"}]

    async def organizations(self):
        await self._maybe_raise("organizations")
        return [{"id": "o1", "name": "Org One"}]

    async def tags(self):
        await self._maybe_raise("tags")
        return ["tag-one"]


@pytest.fixture
def stub_provider(monkeypatch):
    """Install a provider and let each test swap in failure modes."""
    holder = {"provider": StubProvider()}

    def fake_get_provider(datasette, name=None):
        error = holder.get("get_provider_error")
        if error:
            raise error
        return holder["provider"]

    monkeypatch.setattr("datasette_open_data.views.get_provider", fake_get_provider)
    return holder


# ---------------------------------------------------------------------------
# Unknown provider -> 404, not a traceback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "view,url_vars",
    [
        (search_view, {}),
        (dataset_view, {"dataset_id": "d1"}),
        (resource_preview_view, {"resource_id": "r1"}),
        (load_resource_view, {"resource_id": "r1"}),
        (groups_view, {}),
        (organizations_view, {}),
        (tags_view, {}),
    ],
)
async def test_unknown_provider_returns_404(stub_provider, view, url_vars):
    stub_provider["get_provider_error"] = KeyError("Unknown open data provider: 'nope'")

    request = FakeRequest(
        args={**JSON_REQUEST_ARGS, "provider": "nope", "q": "x"}, url_vars=url_vars
    )
    response = await view(FakeDatasette(), request)

    assert response.status == 404
    assert _json(response)["ok"] is False


# ---------------------------------------------------------------------------
# Upstream provider failures -> 502, not a traceback
# ---------------------------------------------------------------------------


async def test_search_provider_failure_returns_502(stub_provider):
    stub_provider["provider"] = StubProvider(search_error=RuntimeError("portal down"))

    request = FakeRequest(args={**JSON_REQUEST_ARGS, "q": "mortgage"})
    response = await search_view(FakeDatasette(), request)

    assert response.status == 502
    assert "portal down" in _json(response)["error"]


async def test_dataset_provider_failure_returns_502(stub_provider):
    stub_provider["provider"] = StubProvider(dataset_error=RuntimeError("404 upstream"))

    request = FakeRequest(args=JSON_REQUEST_ARGS, url_vars={"dataset_id": "missing"})
    response = await dataset_view(FakeDatasette(), request)

    assert response.status == 502
    assert "404 upstream" in _json(response)["error"]


async def test_preview_provider_failure_returns_502(stub_provider):
    stub_provider["provider"] = StubProvider(preview_error=RuntimeError("no datastore"))

    request = FakeRequest(args=JSON_REQUEST_ARGS, url_vars={"resource_id": "r1"})
    response = await resource_preview_view(FakeDatasette(), request)

    assert response.status == 502
    assert "no datastore" in _json(response)["error"]


@pytest.mark.parametrize(
    "view,error_key",
    [
        (groups_view, "groups_error"),
        (organizations_view, "organizations_error"),
        (tags_view, "tags_error"),
    ],
)
async def test_listing_view_failure_returns_502(stub_provider, view, error_key):
    stub_provider["provider"] = StubProvider(**{error_key: RuntimeError("boom")})

    response = await view(FakeDatasette(), FakeRequest(args=JSON_REQUEST_ARGS))

    assert response.status == 502
    assert "boom" in _json(response)["error"]


# ---------------------------------------------------------------------------
# Listing views succeed
# ---------------------------------------------------------------------------


async def test_groups_view_success(stub_provider):
    response = await groups_view(FakeDatasette(), FakeRequest())
    assert response.status == 200
    assert _json(response) == [{"id": "g1", "name": "Group One"}]


async def test_tags_view_success(stub_provider):
    response = await tags_view(FakeDatasette(), FakeRequest())
    assert _json(response) == ["tag-one"]


async def test_organizations_view_success(stub_provider):
    response = await organizations_view(FakeDatasette(), FakeRequest())
    assert _json(response) == [{"id": "o1", "name": "Org One"}]


# ---------------------------------------------------------------------------
# load_resource_view
# ---------------------------------------------------------------------------


async def test_load_without_data_database_returns_400(stub_provider):
    """This used to raise KeyError and surface as a 500."""
    request = FakeRequest(args=JSON_REQUEST_ARGS, url_vars={"resource_id": "r1"})
    response = await load_resource_view(FakeDatasette(), request)

    assert response.status == 400
    assert "no database named 'data'" in _json(response)["error"].lower()


async def test_load_with_memory_database_returns_400(stub_provider):
    ds = FakeDatasette(databases={"data": FakeDatabase(None)})
    request = FakeRequest(args=JSON_REQUEST_ARGS, url_vars={"resource_id": "r1"})
    response = await load_resource_view(ds, request)

    assert response.status == 400
    assert "not backed by a file" in _json(response)["error"]


async def test_load_surfaces_load_error_as_502(stub_provider, monkeypatch, data_db):
    async def failing_load(**kwargs):
        raise LoadError("HTTP 404 downloading CSV from 'https://x/y.csv'")

    monkeypatch.setattr("datasette_open_data.views.load_resource", failing_load)

    ds = FakeDatasette(databases={"data": data_db})
    request = FakeRequest(args=JSON_REQUEST_ARGS, url_vars={"resource_id": "r1"})
    response = await load_resource_view(ds, request)

    assert response.status == 502
    assert "HTTP 404 downloading CSV" in _json(response)["error"]


async def test_load_resource_metadata_failure_returns_502(stub_provider, data_db):
    stub_provider["provider"] = StubProvider(
        resource_error=RuntimeError("resource_show 500")
    )

    ds = FakeDatasette(databases={"data": data_db})
    request = FakeRequest(args=JSON_REQUEST_ARGS, url_vars={"resource_id": "r1"})
    response = await load_resource_view(ds, request)

    assert response.status == 502
    assert "resource_show 500" in _json(response)["error"]


async def test_load_bad_limit_returns_400(stub_provider, data_db):
    ds = FakeDatasette(databases={"data": data_db})
    request = FakeRequest(
        args={**JSON_REQUEST_ARGS, "limit": "lots"}, url_vars={"resource_id": "r1"}
    )
    response = await load_resource_view(ds, request)

    assert response.status == 400
    assert "limit must be an integer" in _json(response)["error"]


async def test_load_success_returns_json(stub_provider, monkeypatch, data_db):
    async def fake_load(**kwargs):
        return 17

    monkeypatch.setattr("datasette_open_data.views.load_resource", fake_load)

    ds = FakeDatasette(databases={"data": data_db})
    request = FakeRequest(args=JSON_REQUEST_ARGS, url_vars={"resource_id": "r1"})
    response = await load_resource_view(ds, request)

    payload = _json(response)
    assert payload["ok"] is True
    assert payload["rows_loaded"] == 17
    assert payload["table"] == "thing"


async def test_load_success_redirects_for_html(stub_provider, monkeypatch, data_db):
    async def fake_load(**kwargs):
        return 5

    monkeypatch.setattr("datasette_open_data.views.load_resource", fake_load)

    ds = FakeDatasette(databases={"data": data_db})
    request = FakeRequest(url_vars={"resource_id": "r1"})
    response = await load_resource_view(ds, request)

    assert response.status == 302
    assert response.headers["Location"] == "/data/thing"


# ---------------------------------------------------------------------------
# Happy paths that render templates
# ---------------------------------------------------------------------------


async def test_search_view_live_results(stub_provider):
    ds = FakeDatasette()
    request = FakeRequest(args={**JSON_REQUEST_ARGS, "q": "mortgage"})
    response = await search_view(ds, request)

    payload = _json(response)
    assert payload["source"] == "live"
    assert payload["count"] == 1
    assert payload["results"][0]["id"] == "p1"


async def test_dataset_view_live(stub_provider):
    request = FakeRequest(args=JSON_REQUEST_ARGS, url_vars={"dataset_id": "d1"})
    response = await dataset_view(FakeDatasette(), request)

    payload = _json(response)
    assert payload["source"] == "live"
    assert payload["dataset"]["title"] == "A Dataset"


async def test_dataset_view_renders_html(stub_provider):
    ds = FakeDatasette()
    request = FakeRequest(url_vars={"dataset_id": "d1"})
    response = await dataset_view(ds, request)

    assert response.status == 200
    assert ds.rendered[0][0] == "open_data_dataset.html"


async def test_preview_view_success(stub_provider):
    request = FakeRequest(args=JSON_REQUEST_ARGS, url_vars={"resource_id": "r1"})
    response = await resource_preview_view(FakeDatasette(), request)

    assert _json(response) == {"records": [], "fields": [], "total": 0}


async def test_preview_bad_limit_returns_400(stub_provider):
    request = FakeRequest(
        args={**JSON_REQUEST_ARGS, "limit": "many"}, url_vars={"resource_id": "r1"}
    )
    response = await resource_preview_view(FakeDatasette(), request)

    assert response.status == 400


async def test_index_view_reports_bad_config(monkeypatch):
    monkeypatch.setattr(
        "datasette_open_data.views.providers_from_config",
        lambda config: (_ for _ in ()).throw(ValueError("Unsupported provider type: 'x'")),
    )
    monkeypatch.setattr("datasette_open_data.views.plugin_config", lambda ds: {})

    response = await index_view(FakeDatasette(), FakeRequest(args=JSON_REQUEST_ARGS))

    assert response.status == 500
    assert "Unsupported provider type" in _json(response)["error"]


async def test_index_view_json(monkeypatch):
    monkeypatch.setattr(
        "datasette_open_data.views.providers_from_config",
        lambda config: {"alpha": StubProvider()},
    )
    monkeypatch.setattr("datasette_open_data.views.plugin_config", lambda ds: {})

    response = await index_view(FakeDatasette(), FakeRequest(args=JSON_REQUEST_ARGS))

    payload = _json(response)
    assert payload["selected_provider"] == "alpha"
    assert payload["providers"]["alpha"]["type"] == "ckan"
    assert payload["catalog_available"] is False
