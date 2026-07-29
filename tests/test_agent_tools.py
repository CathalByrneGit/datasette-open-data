from __future__ import annotations

import json
import sqlite3

import pytest
from conftest import FakeDatabase, FakeDatasette

from datasette_open_data.agent_tools import (
    _can_preview,
    _dataset_html,
    _esc,
    _resolve_table,
    _search_results_html,
    _tool_describe_loaded_table,
    _tool_list_loaded_tables,
    _tool_list_open_data_providers,
    _tool_load_open_data_resource,
    _tool_sample_loaded_table,
    _tool_search_open_data_catalog,
    _tool_show_open_data_dataset,
    _tool_suggest_open_data_joins,
)
from datasette_open_data.models import Dataset, Resource

_CONFIG = {
    "providers": {
        "alpha": {"type": "ckan", "title": "Alpha", "base_url": "https://alpha.example.com"},
        "cso": {"type": "pxstat", "title": "CSO", "base_url": "https://ws.cso.ie"},
    }
}


# ---------------------------------------------------------------------------
# _can_preview
# ---------------------------------------------------------------------------


def test_can_preview_datastore_resource():
    assert _can_preview(Resource(id="r", datastore_active=True)) is True


def test_can_preview_csv_with_url():
    assert _can_preview(Resource(id="r", format="CSV", url="https://x/y.csv")) is True


def test_can_preview_csv_without_url():
    assert _can_preview(Resource(id="r", format="CSV", url=None)) is False


def test_can_preview_other_format():
    assert _can_preview(Resource(id="r", format="XLS", url="https://x/y.xls")) is False


# ---------------------------------------------------------------------------
# HTML escaping
# ---------------------------------------------------------------------------


def test_esc_escapes_markup():
    assert _esc("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"


def test_esc_handles_none():
    assert _esc(None) == ""


def test_search_results_html_escapes_portal_titles():
    """Titles come from third-party portals and must never render as markup."""
    results = [
        {
            "url": "/-/open-data/dataset/x",
            "title": "<img src=x onerror=alert(1)>",
            "provider": "alpha",
            "resource_count": 1,
        }
    ]
    out = _search_results_html(results)
    assert "<img src=x" not in out
    assert "&lt;img src=x" in out


def test_search_results_html_empty():
    assert _search_results_html([]) == "<p>No datasets found.</p>"


def test_dataset_html_escapes_title_and_notes():
    dataset = Dataset(
        id="x",
        name="x",
        title="<b>Title</b>",
        notes="<script>bad()</script>",
    )
    out = _dataset_html("alpha", dataset, [])
    assert "<b>Title</b>" not in out
    assert "<script>" not in out
    assert "&lt;b&gt;Title&lt;/b&gt;" in out


def test_dataset_html_escapes_resource_name():
    dataset = Dataset(id="x", name="x", title="T", notes=None)
    resources = [
        {
            "id": "r1",
            "name": '"><script>x</script>',
            "format": "CSV",
            "preview_url": "/preview",
            "load_url": "/load",
        }
    ]
    out = _dataset_html("alpha", dataset, resources)
    assert "<script>" not in out


# ---------------------------------------------------------------------------
# _resolve_table
# ---------------------------------------------------------------------------


async def test_resolve_table_exact_match(data_db):
    assert await _resolve_table(data_db, "population") == "population"


async def test_resolve_table_case_insensitive(data_db):
    assert await _resolve_table(data_db, "POPULATION") == "population"


async def test_resolve_table_unknown_raises(data_db):
    with pytest.raises(KeyError, match="No table named"):
        await _resolve_table(data_db, "nope")


async def test_resolve_table_rejects_sql_injection(data_db):
    """A crafted identifier must be rejected, not quoted into the query."""
    with pytest.raises(KeyError):
        await _resolve_table(data_db, 'population" UNION SELECT 1 --')


# ---------------------------------------------------------------------------
# list_open_data_providers
# ---------------------------------------------------------------------------


async def test_list_providers(monkeypatch):
    monkeypatch.setattr("datasette_open_data.registry.plugin_config", lambda ds: _CONFIG)
    ds = FakeDatasette()
    result = json.loads(await _tool_list_open_data_providers(ds, None))

    names = {p["name"]: p for p in result["providers"]}
    assert set(names) == {"alpha", "cso"}
    assert names["cso"]["type"] == "pxstat"
    assert names["alpha"]["title"] == "Alpha"


async def test_list_providers_reports_bad_config(monkeypatch):
    monkeypatch.setattr(
        "datasette_open_data.registry.plugin_config",
        lambda ds: {"providers": {"x": {"type": "arcgis", "base_url": "https://x"}}},
    )
    result = json.loads(await _tool_list_open_data_providers(FakeDatasette(), None))
    assert "Unsupported provider type" in result["error"]


# ---------------------------------------------------------------------------
# search_open_data_catalog
# ---------------------------------------------------------------------------


async def test_search_falls_back_to_live_when_no_catalog(monkeypatch):
    """With no catalog.db attached the tool must still search, not error."""
    from datasette_open_data.models import DatasetSummary

    async def fake_search(query, rows=20, start=0):
        return [
            DatasetSummary(
                id="pkg-1",
                name="pkg-1",
                title="Mortgage arrears",
                notes="Quarterly",
                organization="Central Bank",
                resources=[Resource(id="r1")],
            )
        ]

    class FakeProvider:
        name = "alpha"
        type = "ckan"
        search = staticmethod(fake_search)

    monkeypatch.setattr(
        "datasette_open_data.agent_tools.get_provider", lambda ds, p: FakeProvider()
    )

    result = json.loads(
        await _tool_search_open_data_catalog(FakeDatasette(), None, query="mortgage")
    )
    assert result["source"] == "live"
    assert result["count"] == 1
    assert result["results"][0]["dataset_id"] == "pkg-1"


async def test_search_reports_provider_failure(monkeypatch):
    class FakeProvider:
        name = "alpha"
        type = "ckan"

        async def search(self, query, rows=20, start=0):
            raise RuntimeError("portal down")

    monkeypatch.setattr(
        "datasette_open_data.agent_tools.get_provider", lambda ds, p: FakeProvider()
    )

    result = json.loads(await _tool_search_open_data_catalog(FakeDatasette(), None, query="x"))
    assert "portal down" in result["error"]


async def test_search_unusable_catalog_degrades_and_reports(monkeypatch, tmp_path):
    """A present-but-unbuilt catalog.db degrades to live search with a reason."""
    from datasette_open_data.models import DatasetSummary

    empty = str(tmp_path / "catalog.db")
    sqlite3.connect(empty).close()

    class FakeProvider:
        name = "alpha"
        type = "ckan"

        async def search(self, query, rows=20, start=0):
            return [DatasetSummary(id="p1", name="p1", title="T")]

    monkeypatch.setattr(
        "datasette_open_data.agent_tools.get_provider", lambda ds, p: FakeProvider()
    )

    ds = FakeDatasette(databases={"catalog": FakeDatabase(empty)})
    result = json.loads(await _tool_search_open_data_catalog(ds, None, query="x"))

    assert result["source"] == "live"
    assert "catalog_error" in result


async def test_search_clamps_limit(monkeypatch):
    seen = {}

    class FakeProvider:
        name = "alpha"
        type = "ckan"

        async def search(self, query, rows=20, start=0):
            seen["rows"] = rows
            return []

    monkeypatch.setattr(
        "datasette_open_data.agent_tools.get_provider", lambda ds, p: FakeProvider()
    )

    await _tool_search_open_data_catalog(FakeDatasette(), None, query="x", limit=500)
    assert seen["rows"] == 50


# ---------------------------------------------------------------------------
# show_open_data_dataset
# ---------------------------------------------------------------------------


async def test_show_dataset_marks_csv_resources_previewable(monkeypatch):
    """PxStat/Socrata CSV resources are previewable even without a DataStore."""

    class FakeProvider:
        name = "cso"
        type = "pxstat"

        async def dataset(self, dataset_id):
            return Dataset(
                id="VSA01",
                name="VSA01",
                title="Vital Statistics",
                notes="Annual",
                resources=[
                    Resource(
                        id="VSA01",
                        name="VSA01",
                        format="CSV",
                        url="https://ws.cso.ie/x.csv",
                        datastore_active=False,
                    )
                ],
            )

    monkeypatch.setattr(
        "datasette_open_data.agent_tools.get_provider", lambda ds, p: FakeProvider()
    )

    result = json.loads(
        await _tool_show_open_data_dataset(FakeDatasette(), None, dataset_id="VSA01")
    )
    assert result["resources"][0]["preview_url"] is not None
    assert result["resources"][0]["load_url"].endswith("provider=cso")


async def test_show_dataset_no_preview_for_unloadable_format(monkeypatch):
    class FakeProvider:
        name = "alpha"
        type = "ckan"

        async def dataset(self, dataset_id):
            return Dataset(
                id="d",
                name="d",
                title="T",
                resources=[Resource(id="r", format="PDF", url="https://x/y.pdf")],
            )

    monkeypatch.setattr(
        "datasette_open_data.agent_tools.get_provider", lambda ds, p: FakeProvider()
    )

    result = json.loads(await _tool_show_open_data_dataset(FakeDatasette(), None, dataset_id="d"))
    assert result["resources"][0]["preview_url"] is None


async def test_show_dataset_reports_error(monkeypatch):
    class FakeProvider:
        name = "alpha"
        type = "ckan"

        async def dataset(self, dataset_id):
            raise RuntimeError("404 not found")

    monkeypatch.setattr(
        "datasette_open_data.agent_tools.get_provider", lambda ds, p: FakeProvider()
    )

    result = json.loads(
        await _tool_show_open_data_dataset(FakeDatasette(), None, dataset_id="nope")
    )
    assert "404 not found" in result["error"]


# ---------------------------------------------------------------------------
# load_open_data_resource
# ---------------------------------------------------------------------------


async def test_load_resource_denied_without_permission(monkeypatch, data_db):
    """The tool writes directly, so it must enforce the permission itself or it
    becomes a way around load_resource_view's check."""
    called = False

    async def fake_load_resource(**kwargs):
        nonlocal called
        called = True
        return 1

    monkeypatch.setattr("datasette_open_data.agent_tools.load_resource", fake_load_resource)

    ds = FakeDatasette(databases={"data": data_db}, allow=False)
    result = json.loads(await _tool_load_open_data_resource(ds, None, resource_id="r"))

    assert "Permission denied" in result["error"]
    assert "insert-row" in result["error"]
    assert called is False


async def test_load_resource_checks_permission_with_actor(monkeypatch, data_db):
    class FakeProvider:
        name = "alpha"
        type = "ckan"

        async def resource(self, resource_id):
            return Resource(id=resource_id, name="t", datastore_active=True)

    monkeypatch.setattr(
        "datasette_open_data.agent_tools.get_provider", lambda ds, p: FakeProvider()
    )

    async def fake_load_resource(**kwargs):
        return 1

    monkeypatch.setattr("datasette_open_data.agent_tools.load_resource", fake_load_resource)

    ds = FakeDatasette(databases={"data": data_db})
    await _tool_load_open_data_resource(ds, {"id": "bob"}, resource_id="r")

    action, resource, actor = ds.permission_checks[0]
    assert action == "insert-row"
    assert resource.parent == "data"
    assert actor == {"id": "bob"}


async def test_show_dataset_marks_load_as_post(monkeypatch):
    class FakeProvider:
        name = "alpha"
        type = "ckan"

        async def dataset(self, dataset_id):
            return Dataset(id="d", name="d", title="T", resources=[Resource(id="r", format="CSV")])

    monkeypatch.setattr(
        "datasette_open_data.agent_tools.get_provider", lambda ds, p: FakeProvider()
    )

    result = json.loads(await _tool_show_open_data_dataset(FakeDatasette(), None, dataset_id="d"))
    assert result["resources"][0]["load_method"] == "POST"
    # rendered as a form, not a link the model might present as clickable
    assert '<form method="POST"' in result["_html"]
    assert ">Load</a>" not in result["_html"]


async def test_load_resource_requires_data_database(monkeypatch):
    monkeypatch.setattr("datasette_open_data.agent_tools.get_provider", lambda ds, p: object())
    result = json.loads(await _tool_load_open_data_resource(FakeDatasette(), None, resource_id="r"))
    assert "No database named 'data'" in result["error"]
    assert "hint" in result


async def test_load_resource_rejects_memory_database(monkeypatch):
    monkeypatch.setattr("datasette_open_data.agent_tools.get_provider", lambda ds, p: object())
    ds = FakeDatasette(databases={"data": FakeDatabase(None)})
    result = json.loads(await _tool_load_open_data_resource(ds, None, resource_id="r"))
    assert "not file-backed" in result["error"]


async def test_load_resource_surfaces_load_error(monkeypatch, datasette_with_data):
    """LoadError becomes a structured message, not a raised exception."""

    class FakeProvider:
        name = "alpha"
        type = "ckan"

        async def resource(self, resource_id):
            return Resource(id=resource_id, name="thing", format="CSV", url=None)

    monkeypatch.setattr(
        "datasette_open_data.agent_tools.get_provider", lambda ds, p: FakeProvider()
    )

    result = json.loads(
        await _tool_load_open_data_resource(datasette_with_data, None, resource_id="r1")
    )
    assert "no URL to download from" in result["error"]


async def test_load_resource_reports_metadata_failure(monkeypatch, datasette_with_data):
    class FakeProvider:
        name = "alpha"
        type = "ckan"

        async def resource(self, resource_id):
            raise RuntimeError("resource_show failed")

    monkeypatch.setattr(
        "datasette_open_data.agent_tools.get_provider", lambda ds, p: FakeProvider()
    )

    result = json.loads(
        await _tool_load_open_data_resource(datasette_with_data, None, resource_id="r")
    )
    assert "Could not fetch resource metadata" in result["error"]


async def test_load_resource_success(monkeypatch, datasette_with_data):
    class FakeProvider:
        name = "alpha"
        type = "ckan"

        async def resource(self, resource_id):
            return Resource(id=resource_id, name="My Table", datastore_active=True)

    monkeypatch.setattr(
        "datasette_open_data.agent_tools.get_provider", lambda ds, p: FakeProvider()
    )

    async def fake_load_resource(**kwargs):
        return 42

    monkeypatch.setattr("datasette_open_data.agent_tools.load_resource", fake_load_resource)

    result = json.loads(
        await _tool_load_open_data_resource(datasette_with_data, None, resource_id="r")
    )
    assert result["ok"] is True
    assert result["table"] == "my_table"
    assert result["rows_loaded"] == 42
    assert result["browse_url"] == "/data/my_table"


# ---------------------------------------------------------------------------
# list / describe / sample
# ---------------------------------------------------------------------------


async def test_list_loaded_tables(datasette_with_data):
    result = json.loads(await _tool_list_loaded_tables(datasette_with_data, None))
    assert set(result["tables"]) == {"population", "prices"}


async def test_list_loaded_tables_no_database():
    result = json.loads(await _tool_list_loaded_tables(FakeDatasette(), None))
    assert "No database named 'data'" in result["error"]


async def test_describe_loaded_table(datasette_with_data):
    result = json.loads(
        await _tool_describe_loaded_table(datasette_with_data, None, table="population")
    )
    assert [c["name"] for c in result["columns"]] == ["county", "year", "value"]


async def test_describe_unknown_table_lists_available(datasette_with_data):
    result = json.loads(
        await _tool_describe_loaded_table(datasette_with_data, None, table="missing")
    )
    assert "No table named 'missing'" in result["error"]
    assert "population" in result["error"]


async def test_describe_rejects_injected_table_name(datasette_with_data):
    result = json.loads(
        await _tool_describe_loaded_table(datasette_with_data, None, table='population") --')
    )
    assert "error" in result


async def test_sample_loaded_table(datasette_with_data):
    result = json.loads(
        await _tool_sample_loaded_table(datasette_with_data, None, table="prices", limit=2)
    )
    assert result["count"] == 2
    assert result["rows"][0]["county"] == "Dublin"


async def test_sample_clamps_limit(datasette_with_data):
    result = json.loads(
        await _tool_sample_loaded_table(datasette_with_data, None, table="prices", limit=999)
    )
    assert result["count"] == 3


async def test_sample_unknown_table(datasette_with_data):
    result = json.loads(await _tool_sample_loaded_table(datasette_with_data, None, table="nope"))
    assert "No table named" in result["error"]


# ---------------------------------------------------------------------------
# suggest_open_data_joins
# ---------------------------------------------------------------------------


async def test_suggest_joins_finds_shared_column(datasette_with_data):
    result = json.loads(await _tool_suggest_open_data_joins(datasette_with_data, None))

    pairs = {(s["column1"], s["column2"]) for s in result["suggestions"]}
    assert ("county", "county") in pairs

    county = next(
        s for s in result["suggestions"] if s["column1"] == "county" and s["column2"] == "county"
    )
    assert county["name_match"] is True
    # Dublin and Cork overlap; Galway and Kerry do not -> 2/4
    assert county["jaccard"] == pytest.approx(0.5)
    assert 'JOIN "prices"' in county["sql"]


async def test_suggest_joins_needs_two_tables(tmp_path):
    path = str(tmp_path / "one.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE only_one (a TEXT)")
    conn.commit()
    conn.close()

    ds = FakeDatasette(databases={"data": FakeDatabase(path)})
    result = json.loads(await _tool_suggest_open_data_joins(ds, None))
    assert "at least 2" in result["message"]


async def test_suggest_joins_no_database():
    result = json.loads(await _tool_suggest_open_data_joins(FakeDatasette(), None))
    assert "No database named 'data'" in result["error"]


async def test_suggest_joins_caps_table_count(tmp_path):
    """Comparison is quadratic, so breadth is capped and the cap is reported."""
    from datasette_open_data.agent_tools import MAX_JOIN_TABLES

    path = str(tmp_path / "many.db")
    conn = sqlite3.connect(path)
    for i in range(MAX_JOIN_TABLES + 3):
        conn.execute(f"CREATE TABLE t{i} (county TEXT)")
        conn.execute(f"INSERT INTO t{i} VALUES ('Dublin')")
    conn.commit()
    conn.close()

    ds = FakeDatasette(databases={"data": FakeDatabase(path)})
    result = json.loads(await _tool_suggest_open_data_joins(ds, None))

    assert len(result["tables"]) == MAX_JOIN_TABLES
    assert "note" in result
