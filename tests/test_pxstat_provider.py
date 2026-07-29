from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from datasette_open_data.providers.pxstat import PxStatError, PxStatProvider

# ---------------------------------------------------------------------------
# Construction / URL derivation
# ---------------------------------------------------------------------------


def test_urls_derived_from_base():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    assert p.jsonrpc_url == "https://ws.cso.ie/public/api.jsonrpc"
    assert p.rest_base == "https://ws.cso.ie/public/api.restful"


def test_base_url_trailing_slash_stripped():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie/")
    assert p.jsonrpc_url == "https://ws.cso.ie/public/api.jsonrpc"


def test_title_defaults_to_name():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    assert p.title == "cso"


def test_explicit_title():
    p = PxStatProvider(name="cso", title="CSO Ireland", base_url="https://ws.cso.ie")
    assert p.title == "CSO Ireland"


def test_language_defaults_to_en():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    assert p.language == "en"


def test_csv_url():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    url = p._csv_url("VSA01")
    assert (
        url == "https://ws.cso.ie/public/api.restful/PxStat.Data.Cube_API.ReadDataset/VSA01/CSV/en/"
    )


# ---------------------------------------------------------------------------
# _resource_from_matrix
# ---------------------------------------------------------------------------


def test_resource_from_matrix():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    r = p._resource_from_matrix("VSA01", title="Vital Statistics")
    assert r.id == "VSA01"
    assert r.name == "VSA01"
    assert r.format == "CSV"
    assert "VSA01" in r.url
    assert r.datastore_active is False
    assert r.description == "Vital Statistics"


# ---------------------------------------------------------------------------
# _summary_from_item
# ---------------------------------------------------------------------------


def test_summary_from_item_full():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    item = {
        "extension": {
            "matrix": "VSA01",
            "subject": {"SbjCode": 12, "SbjValue": "Vital Statistics"},
        },
        "label": "Births, Deaths and Marriages",
    }
    s = p._summary_from_item(item)
    assert s.id == "VSA01"
    assert s.name == "VSA01"
    assert s.title == "Births, Deaths and Marriages"
    assert "Vital Statistics" in s.tags
    assert len(s.resources) == 1
    assert s.resources[0].format == "CSV"


def test_summary_from_item_no_subject():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    item = {"extension": {"matrix": "VSA01"}, "label": "Some Table"}
    s = p._summary_from_item(item)
    assert s.tags == []


def test_summary_from_item_fallback_title():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    item = {"extension": {"matrix": "VSA01"}}
    s = p._summary_from_item(item)
    assert s.title == "VSA01"


# ---------------------------------------------------------------------------
# _dataset_from_metadata
# ---------------------------------------------------------------------------


def test_dataset_from_metadata_full():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    meta = {
        "label": "Vital Statistics",
        "note": ["This table shows births.", "Updated annually."],
        "copyright": {"name": "Central Statistics Office"},
        "href": "https://ws.cso.ie/en/VSA01",
    }
    d = p._dataset_from_metadata("VSA01", meta)
    assert d.id == "VSA01"
    assert d.title == "Vital Statistics"
    assert "births" in d.notes
    assert "Updated annually" in d.notes
    assert d.organization == "Central Statistics Office"
    assert d.url == "https://ws.cso.ie/en/VSA01"


def test_dataset_from_metadata_no_notes():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    d = p._dataset_from_metadata("VSA01", {"label": "X"})
    assert d.notes is None


def test_dataset_from_metadata_fallback_url():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    d = p._dataset_from_metadata("VSA01", {})
    assert d.url == "https://ws.cso.ie/en/VSA01"
    assert d.title == "VSA01"


def test_dataset_from_metadata_string_copyright():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    d = p._dataset_from_metadata("X", {"copyright": "Some string"})
    assert d.organization is None


# ---------------------------------------------------------------------------
# search (mocked _rpc)
# ---------------------------------------------------------------------------


async def test_search_filters_by_label():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")

    async def fake_rpc(method, params=None):
        assert method == "PxStat.Data.Cube_API.ReadCollection"
        return {
            "link": {
                "item": [
                    {"extension": {"matrix": "VSA01"}, "label": "Vital Statistics Annual"},
                    {"extension": {"matrix": "CPA01"}, "label": "Consumer Price Index"},
                    {"extension": {"matrix": "VSA02"}, "label": "Vital Statistics Quarterly"},
                ]
            }
        }

    p._rpc = fake_rpc
    results = await p.search("vital")
    assert len(results) == 2
    assert all("VSA" in r.id for r in results)


async def test_search_filters_by_matrix_code():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")

    async def fake_rpc(method, params=None):
        return {
            "link": {
                "item": [
                    {"extension": {"matrix": "CPA01"}, "label": "Consumer Prices"},
                    {"extension": {"matrix": "VSA01"}, "label": "Vital Stats"},
                ]
            }
        }

    p._rpc = fake_rpc
    results = await p.search("CPA")
    assert len(results) == 1
    assert results[0].id == "CPA01"


async def test_search_respects_rows_and_start():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")

    async def fake_rpc(method, params=None):
        return {
            "link": {
                "item": [
                    {"extension": {"matrix": f"T{i:03d}"}, "label": "table"} for i in range(10)
                ]
            }
        }

    p._rpc = fake_rpc
    results = await p.search("table", rows=3, start=2)
    assert len(results) == 3
    assert results[0].id == "T002"


async def test_search_empty_result():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")

    async def fake_rpc(method, params=None):
        return {"link": {"item": []}}

    p._rpc = fake_rpc
    assert await p.search("nothing") == []


# ---------------------------------------------------------------------------
# dataset (mocked _rpc)
# ---------------------------------------------------------------------------


async def test_dataset_calls_read_metadata():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")

    async def fake_rpc(method, params=None):
        assert method == "PxStat.Data.Cube_API.ReadMetadata"
        assert params.get("matrix") == "VSA01"
        return {
            "label": "Vital Statistics",
            "note": ["Annual data"],
            "copyright": {"name": "CSO"},
        }

    p._rpc = fake_rpc
    d = await p.dataset("VSA01")
    assert d.id == "VSA01"
    assert d.title == "Vital Statistics"
    assert d.organization == "CSO"
    assert len(d.resources) == 1


# ---------------------------------------------------------------------------
# resource (mocked _rpc)
# ---------------------------------------------------------------------------


async def test_resource_returns_csv_resource():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")

    async def fake_rpc(method, params=None):
        return {"label": "Vital Statistics"}

    p._rpc = fake_rpc
    r = await p.resource("VSA01")
    assert r.id == "VSA01"
    assert r.format == "CSV"
    assert "VSA01" in r.url


async def test_resource_handles_rpc_error_gracefully():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")

    async def fake_rpc(method, params=None):
        raise PxStatError("Not found")

    p._rpc = fake_rpc
    r = await p.resource("UNKNOWN")
    assert r.id == "UNKNOWN"
    assert r.description is None


# ---------------------------------------------------------------------------
# groups (mocked _rpc)
# ---------------------------------------------------------------------------


async def test_groups_returns_themes():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")

    async def fake_rpc(method, params=None):
        assert method == "PxStat.System.Navigation.Navigation_API.Read"
        return [
            {
                "ThmCode": 1,
                "ThmValue": "Population",
                "subject": [
                    {"SbjCode": 11, "SbjValue": "Census"},
                    {"SbjCode": 12, "SbjValue": "Vital Statistics"},
                ],
            },
            {
                "ThmCode": 2,
                "ThmValue": "Economy",
                "subject": [{"SbjCode": 21, "SbjValue": "Prices"}],
            },
        ]

    p._rpc = fake_rpc
    groups = await p.groups()
    assert len(groups) == 2
    assert groups[0]["name"] == "Population"
    assert len(groups[0]["subjects"]) == 2
    assert groups[0]["subjects"][0]["name"] == "Census"


# ---------------------------------------------------------------------------
# tags (mocked _rpc)
# ---------------------------------------------------------------------------


async def test_tags_returns_all_subjects():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")

    async def fake_rpc(method, params=None):
        return [
            {
                "ThmCode": 1,
                "ThmValue": "Population",
                "subject": [
                    {"SbjCode": 11, "SbjValue": "Census"},
                    {"SbjCode": 12, "SbjValue": "Vital Statistics"},
                ],
            },
        ]

    p._rpc = fake_rpc
    tags = await p.tags()
    assert tags == ["Census", "Vital Statistics"]


async def test_tags_skips_empty_subject_value():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")

    async def fake_rpc(method, params=None):
        return [
            {
                "ThmCode": 1,
                "ThmValue": "T",
                "subject": [{"SbjCode": 1, "SbjValue": ""}, {"SbjCode": 2, "SbjValue": "Valid"}],
            }
        ]

    p._rpc = fake_rpc
    assert await p.tags() == ["Valid"]


# ---------------------------------------------------------------------------
# organizations
# ---------------------------------------------------------------------------


async def test_organizations_returns_empty():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    assert await p.organizations() == []


# ---------------------------------------------------------------------------
# datastore_preview (mocked HTTP)
# ---------------------------------------------------------------------------


def _mock_csv_client(csv_bytes: bytes):
    mock_response = MagicMock()
    mock_response.content = csv_bytes
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    mock_cls = MagicMock()
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_cls


async def test_datastore_preview_parses_csv():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    csv_bytes = b"Year,Value,Unit\n2020,100,Number\n2021,105,Number\n2022,110,Number\n"

    with patch(
        "datasette_open_data.providers.pxstat.httpx.AsyncClient", _mock_csv_client(csv_bytes)
    ):
        result = await p.datastore_preview("VSA01", limit=2)

    assert len(result["records"]) == 2
    assert result["records"][0] == {"Year": "2020", "Value": "100", "Unit": "Number"}
    field_ids = [f["id"] for f in result["fields"]]
    assert field_ids == ["Year", "Value", "Unit"]


async def test_datastore_preview_respects_limit():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    rows = "\n".join(f"{i},{i * 10}" for i in range(1, 21))
    csv_bytes = f"id,val\n{rows}\n".encode()

    with patch(
        "datasette_open_data.providers.pxstat.httpx.AsyncClient", _mock_csv_client(csv_bytes)
    ):
        result = await p.datastore_preview("VSA01", limit=5)

    assert len(result["records"]) == 5


async def test_datastore_preview_empty_csv():
    p = PxStatProvider(name="cso", base_url="https://ws.cso.ie")
    csv_bytes = b"Year,Value\n"

    with patch(
        "datasette_open_data.providers.pxstat.httpx.AsyncClient", _mock_csv_client(csv_bytes)
    ):
        result = await p.datastore_preview("VSA01")

    assert result["records"] == []
    assert result["fields"] == []
