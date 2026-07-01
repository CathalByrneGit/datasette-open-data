from __future__ import annotations

from types import SimpleNamespace

import pytest

from datasette_open_data.models import Dataset, Resource
from datasette_open_data.views import _fts_query, _jsonable, _wants_json


# ---------------------------------------------------------------------------
# _fts_query
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query, expected",
    [
        ("hello world", "hello* world*"),
        ("single", "single*"),
        ("", ""),
        ("  spaces  ", "spaces*"),
        ('he"llo', "he* llo*"),
        ("multiple   spaces", "multiple* spaces*"),
        ('"quoted phrase"', "quoted* phrase*"),
    ],
)
def test_fts_query(query, expected):
    assert _fts_query(query) == expected


# ---------------------------------------------------------------------------
# _wants_json
# ---------------------------------------------------------------------------


def _make_request(args=None, headers=None):
    req = SimpleNamespace()
    req.args = args or {}
    req.headers = headers or {}
    return req


def test_wants_json_format_param():
    req = _make_request(args={"_format": "json"})
    assert _wants_json(req) is True


def test_wants_json_accept_header():
    req = _make_request(headers={"accept": "application/json"})
    assert _wants_json(req) is True


def test_wants_json_accept_header_with_other_types():
    req = _make_request(headers={"accept": "text/html, application/json"})
    assert _wants_json(req) is True


def test_wants_json_false_no_signal():
    req = _make_request()
    assert _wants_json(req) is False


def test_wants_json_false_html_accept():
    req = _make_request(headers={"accept": "text/html"})
    assert _wants_json(req) is False


# ---------------------------------------------------------------------------
# _jsonable
# ---------------------------------------------------------------------------


def test_jsonable_passthrough_primitives():
    assert _jsonable(42) == 42
    assert _jsonable("hello") == "hello"
    assert _jsonable(None) is None


def test_jsonable_list():
    assert _jsonable([1, "two", None]) == [1, "two", None]


def test_jsonable_dict():
    assert _jsonable({"a": 1, "b": "two"}) == {"a": 1, "b": "two"}


def test_jsonable_dataclass_resource():
    r = Resource(id="res-1", name="My Data", format="CSV")
    result = _jsonable(r)
    assert isinstance(result, dict)
    assert result["id"] == "res-1"
    assert result["name"] == "My Data"
    assert result["format"] == "CSV"
    assert result["datastore_active"] is False
    assert result["extras"] == {}


def test_jsonable_dataclass_dataset():
    d = Dataset(
        id="pkg-1",
        name="my-dataset",
        title="My Dataset",
        resources=[Resource(id="r1")],
    )
    result = _jsonable(d)
    assert result["id"] == "pkg-1"
    assert isinstance(result["resources"], list)
    assert result["resources"][0]["id"] == "r1"


def test_jsonable_nested():
    inner = Resource(id="r2")
    result = _jsonable({"resource": inner, "items": [inner]})
    assert result["resource"]["id"] == "r2"
    assert result["items"][0]["id"] == "r2"
