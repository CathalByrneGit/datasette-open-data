from __future__ import annotations

from unittest.mock import patch

import pytest

from datasette_open_data.registry import get_provider, providers_from_config

# ---------------------------------------------------------------------------
# providers_from_config
# ---------------------------------------------------------------------------


def test_providers_from_config_ckan():
    config = {
        "providers": {
            "myportal": {
                "type": "ckan",
                "title": "My Portal",
                "base_url": "https://data.example.com",
            }
        }
    }
    providers = providers_from_config(config)
    assert "myportal" in providers
    assert providers["myportal"].name == "myportal"
    assert providers["myportal"].title == "My Portal"
    assert providers["myportal"].base_url == "https://data.example.com"


def test_providers_from_config_socrata():
    config = {
        "providers": {
            "nyc": {
                "type": "socrata",
                "title": "NYC Open Data",
                "base_url": "https://data.cityofnewyork.us",
            }
        }
    }
    providers = providers_from_config(config)
    assert "nyc" in providers
    assert providers["nyc"].name == "nyc"
    assert providers["nyc"].title == "NYC Open Data"
    assert providers["nyc"].type == "socrata"


def test_providers_from_config_pxstat():
    config = {
        "providers": {
            "cso": {
                "type": "pxstat",
                "title": "Central Statistics Office Ireland",
                "base_url": "https://ws.cso.ie",
                "language": "en",
            }
        }
    }
    providers = providers_from_config(config)
    assert "cso" in providers
    assert providers["cso"].name == "cso"
    assert providers["cso"].type == "pxstat"
    assert providers["cso"].title == "Central Statistics Office Ireland"
    assert providers["cso"].language == "en"


def test_providers_from_config_pxstat_default_language():
    config = {
        "providers": {
            "cso": {"type": "pxstat", "base_url": "https://ws.cso.ie"}
        }
    }
    providers = providers_from_config(config)
    assert providers["cso"].language == "en"


def test_providers_from_config_mixed_types():
    config = {
        "providers": {
            "ckan_portal": {"type": "ckan", "base_url": "https://ckan.example.com"},
            "socrata_portal": {"type": "socrata", "base_url": "https://socrata.example.com"},
            "pxstat_portal": {"type": "pxstat", "base_url": "https://ws.example.ie"},
        }
    }
    providers = providers_from_config(config)
    assert providers["ckan_portal"].type == "ckan"
    assert providers["socrata_portal"].type == "socrata"
    assert providers["pxstat_portal"].type == "pxstat"


def test_providers_from_config_unsupported_type():
    config = {
        "providers": {
            "bad": {
                "type": "arcgis",
                "base_url": "https://data.example.com",
            }
        }
    }
    with pytest.raises(ValueError, match="Unsupported provider type"):
        providers_from_config(config)


def test_providers_from_config_empty():
    assert providers_from_config({}) == {}
    assert providers_from_config({"providers": {}}) == {}


def test_providers_from_config_multiple():
    config = {
        "providers": {
            "alpha": {"type": "ckan", "base_url": "https://alpha.example.com"},
            "beta": {"type": "ckan", "base_url": "https://beta.example.com"},
        }
    }
    providers = providers_from_config(config)
    assert set(providers.keys()) == {"alpha", "beta"}


def test_providers_from_config_optional_fields():
    config = {
        "providers": {
            "p": {
                "type": "ckan",
                "base_url": "https://example.com",
                "api_base_url": "https://example.com/api/v3",
                "datastore_api_base_url": "https://ds.example.com/api/v3",
            }
        }
    }
    providers = providers_from_config(config)
    assert providers["p"].api_base_url == "https://example.com/api/v3"
    assert providers["p"].datastore_api_base_url == "https://ds.example.com/api/v3"


# ---------------------------------------------------------------------------
# get_provider
# ---------------------------------------------------------------------------


_SIMPLE_CONFIG = {
    "providers": {
        "alpha": {"type": "ckan", "base_url": "https://alpha.example.com"},
        "beta": {"type": "ckan", "base_url": "https://beta.example.com"},
    }
}


def test_get_provider_by_name():
    with patch("datasette_open_data.registry.plugin_config", return_value=_SIMPLE_CONFIG):
        provider = get_provider(None, name="beta")
    assert provider.name == "beta"


def test_get_provider_defaults_to_first():
    with patch("datasette_open_data.registry.plugin_config", return_value=_SIMPLE_CONFIG):
        provider = get_provider(None)
    assert provider.name == "alpha"


def test_get_provider_unknown_name_raises():
    with patch("datasette_open_data.registry.plugin_config", return_value=_SIMPLE_CONFIG):
        with pytest.raises(KeyError, match="Unknown open data provider"):
            get_provider(None, name="nope")


def test_get_provider_no_providers_raises():
    empty = {"providers": {}}
    with patch("datasette_open_data.registry.plugin_config", return_value=empty):
        with pytest.raises(ValueError, match="No open data providers configured"):
            get_provider(None)
