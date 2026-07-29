"""End-to-end tests for scripts/build_catalog.py.

The regression these guard: build_catalog used to call the CKAN-only
provider._get("package_search", ...) directly, so `--provider cso` raised
AttributeError and `--all` broke as soon as a non-CKAN provider appeared in
providers.yml.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).parent.parent / "scripts"


def _load_build_catalog():
    spec = importlib.util.spec_from_file_location(
        "build_catalog", _SCRIPTS / "build_catalog.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_catalog"] = module
    spec.loader.exec_module(module)
    return module


build_catalog_module = _load_build_catalog()


class FakeProvider:
    """Provider that yields catalog records without any network access."""

    def __init__(self, name, provider_type, records):
        self.name = name
        self.type = provider_type
        self.title = name
        self.base_url = f"https://{name}.example.com"
        self._records = records
        self.seen_kwargs = None

    async def iter_catalog(self, limit=None, **kwargs):
        self.seen_kwargs = {"limit": limit, **kwargs}
        for index, record in enumerate(self._records):
            if limit is not None and index >= limit:
                return
            yield record


def _pxstat_record(
    matrix, title, subject="Vital Statistics", theme="Population", theme_id="1"
):
    return {
        "id": matrix,
        "name": matrix,
        "title": title,
        "notes": None,
        "organization": {},
        "license_title": None,
        "url": f"https://ws.cso.ie/en/{matrix}",
        "metadata_created": None,
        "metadata_modified": "2024-01-01",
        "resources": [
            {
                "id": matrix,
                "name": matrix,
                "description": title,
                "format": "CSV",
                "url": f"https://ws.cso.ie/public/api.restful/x/{matrix}/CSV/en/",
                "datastore_active": False,
                "created": None,
                "last_modified": None,
            }
        ],
        "tags": [{"name": subject, "display_name": subject}],
        "groups": [{"id": theme_id, "name": theme, "title": theme, "description": None}],
    }


@pytest.fixture
def patched_config(monkeypatch):
    """Install a fake provider set into build_catalog's module namespace."""
    state = {}

    def install(config, providers):
        state["providers"] = providers
        monkeypatch.setattr(build_catalog_module, "plugin_config", lambda ds: config)
        monkeypatch.setattr(
            build_catalog_module, "providers_from_config", lambda cfg: providers
        )
        return providers

    return install


# ---------------------------------------------------------------------------
# PxStat — the provider that used to crash
# ---------------------------------------------------------------------------


async def test_build_catalog_works_for_pxstat(tmp_path, patched_config):
    provider = FakeProvider(
        "cso",
        "pxstat",
        [
            _pxstat_record("VSA01", "Births Annual"),
            _pxstat_record(
                "CPA01",
                "Consumer Prices",
                subject="Prices",
                theme="Economy",
                theme_id="2",
            ),
        ],
    )
    patched_config(
        {"providers": {"cso": {"type": "pxstat", "base_url": "https://ws.cso.ie"}}},
        {"cso": provider},
    )

    database = tmp_path / "catalog.db"
    await build_catalog_module.build_catalog("cso", database)

    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row

    packages = conn.execute(
        "SELECT id, title FROM packages WHERE provider = 'cso' ORDER BY id"
    ).fetchall()
    assert [p["id"] for p in packages] == ["CPA01", "VSA01"]

    resources = conn.execute(
        "SELECT id, format FROM resources WHERE provider = 'cso'"
    ).fetchall()
    assert {r["format"] for r in resources} == {"CSV"}

    tags = conn.execute(
        "SELECT name FROM tags WHERE provider = 'cso' ORDER BY name"
    ).fetchall()
    assert [t["name"] for t in tags] == ["Prices", "Vital Statistics"]

    groups = conn.execute(
        "SELECT title FROM groups WHERE provider = 'cso' ORDER BY title"
    ).fetchall()
    assert [g["title"] for g in groups] == ["Economy", "Population"]

    conn.close()


async def test_pxstat_catalog_is_fts_searchable(tmp_path, patched_config):
    """The point of building the catalog: CSO search stops being a full crawl."""
    provider = FakeProvider(
        "cso",
        "pxstat",
        [
            _pxstat_record("VSA01", "Births Annual"),
            _pxstat_record("CPA01", "Consumer Price Index", subject="Prices"),
        ],
    )
    patched_config({"providers": {"cso": {"type": "pxstat"}}}, {"cso": provider})

    database = tmp_path / "catalog.db"
    await build_catalog_module.build_catalog("cso", database)

    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT p.id, p.title
        FROM packages_fts fts
        JOIN packages_fts_map m ON m.fts_rowid = fts.rowid
        JOIN packages p ON p.provider = m.provider AND p.id = m.package_id
        WHERE packages_fts MATCH 'consumer*'
        """
    ).fetchall()
    conn.close()

    assert [r["id"] for r in rows] == ["CPA01"]


async def test_pxstat_provider_gets_no_rows_per_page_kwarg(tmp_path, patched_config):
    """ReadCollection returns everything at once, so paging isn't applicable."""
    provider = FakeProvider("cso", "pxstat", [_pxstat_record("VSA01", "Births")])
    patched_config({"providers": {"cso": {"type": "pxstat"}}}, {"cso": provider})

    await build_catalog_module.build_catalog("cso", tmp_path / "catalog.db")

    assert provider.seen_kwargs == {"limit": None}


# ---------------------------------------------------------------------------
# CKAN and Socrata
# ---------------------------------------------------------------------------


async def test_build_catalog_works_for_ckan(tmp_path, patched_config):
    provider = FakeProvider(
        "alpha",
        "ckan",
        [
            {
                "id": "pkg-1",
                "name": "mortgage-arrears",
                "title": "Mortgage Arrears",
                "notes": "Quarterly statistics",
                "organization": {
                    "id": "org-1",
                    "name": "cbi",
                    "title": "Central Bank",
                    "description": None,
                },
                "license_title": "CC-BY",
                "url": "https://alpha.example.com/dataset/mortgage-arrears",
                "resources": [
                    {
                        "id": "res-1",
                        "name": "Data",
                        "format": "CSV",
                        "url": "https://x/y.csv",
                        "datastore_active": True,
                    }
                ],
                "tags": [{"name": "mortgages", "display_name": "Mortgages"}],
                "groups": [{"id": "g1", "name": "finance", "title": "Finance"}],
            }
        ],
    )
    patched_config({"providers": {"alpha": {"type": "ckan"}}}, {"alpha": provider})

    database = tmp_path / "catalog.db"
    await build_catalog_module.build_catalog("alpha", database, rows_per_page=50)

    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    package = conn.execute("SELECT * FROM packages WHERE id = 'pkg-1'").fetchone()
    organization = conn.execute("SELECT * FROM organizations").fetchone()
    conn.close()

    assert package["organization_title"] == "Central Bank"
    assert package["license_title"] == "CC-BY"
    assert organization["title"] == "Central Bank"
    # paged providers receive rows_per_page
    assert provider.seen_kwargs == {"limit": None, "rows_per_page": 50}


async def test_socrata_provider_receives_rows_per_page(tmp_path, patched_config):
    provider = FakeProvider(
        "nyc",
        "socrata",
        [{"id": "abcd-1234", "title": "Trees", "resources": [], "tags": [], "groups": []}],
    )
    patched_config({"providers": {"nyc": {"type": "socrata"}}}, {"nyc": provider})

    await build_catalog_module.build_catalog(
        "nyc", tmp_path / "catalog.db", rows_per_page=25
    )

    assert provider.seen_kwargs == {"limit": None, "rows_per_page": 25}


# ---------------------------------------------------------------------------
# build_all across mixed provider types
# ---------------------------------------------------------------------------


async def test_build_all_handles_mixed_provider_types(tmp_path, patched_config):
    """--all used to die on the first non-CKAN provider in providers.yml."""
    ckan = FakeProvider(
        "alpha",
        "ckan",
        [{"id": "pkg-1", "title": "CKAN Package", "resources": [], "tags": [], "groups": []}],
    )
    pxstat = FakeProvider("cso", "pxstat", [_pxstat_record("VSA01", "Births Annual")])

    patched_config(
        {"providers": {"alpha": {"type": "ckan"}, "cso": {"type": "pxstat"}}},
        {"alpha": ckan, "cso": pxstat},
    )

    database = tmp_path / "catalog.db"
    await build_catalog_module.build_all(database)

    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT provider, id FROM packages ORDER BY provider"
    ).fetchall()
    conn.close()

    assert [(r["provider"], r["id"]) for r in rows] == [
        ("alpha", "pkg-1"),
        ("cso", "VSA01"),
    ]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


async def test_unknown_provider_raises(tmp_path, patched_config):
    patched_config({"providers": {"alpha": {"type": "ckan"}}}, {"alpha": None})

    with pytest.raises(ValueError, match="Unknown provider"):
        await build_catalog_module.build_catalog("nope", tmp_path / "catalog.db")


async def test_provider_without_iter_catalog_raises(tmp_path, patched_config):
    class Legacy:
        name = "legacy"
        type = "legacy"

    patched_config({"providers": {"legacy": {"type": "legacy"}}}, {"legacy": Legacy()})

    with pytest.raises(ValueError, match="does not support catalog building"):
        await build_catalog_module.build_catalog("legacy", tmp_path / "catalog.db")


async def test_rebuild_replaces_previous_run(tmp_path, patched_config):
    """Rebuilding a provider must not leave orphaned rows or duplicate FTS entries."""
    first = FakeProvider("cso", "pxstat", [_pxstat_record("VSA01", "Births Annual")])
    install = patched_config({"providers": {"cso": {"type": "pxstat"}}}, {"cso": first})
    assert install

    database = tmp_path / "catalog.db"
    await build_catalog_module.build_catalog("cso", database)
    await build_catalog_module.build_catalog("cso", database)

    conn = sqlite3.connect(database)
    packages = conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
    fts_rows = conn.execute("SELECT COUNT(*) FROM packages_fts").fetchone()[0]
    runs = conn.execute("SELECT COUNT(*) FROM catalog_runs").fetchone()[0]
    conn.close()

    assert packages == 1
    assert fts_rows == 1
    assert runs == 2
