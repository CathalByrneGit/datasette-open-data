from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .providers.ckan import CKANProvider


DEFAULT_CONFIG = {
    "providers": {
        "centralbank": {
            "type": "ckan",
            "title": "Central Bank of Ireland Open Data Portal",
            "base_url": "https://opendata.centralbank.ie",
            "api_base_url": "https://opendata.centralbank.ie/api/3",
            "datastore_api_base_url": "https://opendata.centralbank.ie/en_GB/api/3",
        }
    }
}


def load_providers_file(path: str | Path = "providers.yml") -> dict[str, Any] | None:
    path = Path(path)

    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as fp:
        return yaml.safe_load(fp) or None


def plugin_config(datasette) -> dict[str, Any]:
    if datasette is not None:
        try:
            config = (
                datasette.plugin_config("datasette-open-data")
                or datasette.plugin_config("open-data")
            )
        except Exception:
            config = None

        if config:
            return config

    file_config = load_providers_file()
    return file_config or DEFAULT_CONFIG


def providers_from_config(config: dict[str, Any]) -> dict[str, CKANProvider]:
    providers: dict[str, CKANProvider] = {}

    for name, item in (config.get("providers") or {}).items():
        provider_type = item.get("type", "ckan")

        if provider_type != "ckan":
            raise ValueError(f"Unsupported provider type: {provider_type}")

        providers[name] = CKANProvider(
            name=name,
            title=item.get("title"),
            base_url=item["base_url"],
            api_base_url=item.get("api_base_url"),
            datastore_api_base_url=item.get("datastore_api_base_url"),
        )

    return providers


def get_provider(datasette, name: str | None = None) -> CKANProvider:
    providers = providers_from_config(plugin_config(datasette))

    if not providers:
        raise ValueError("No open data providers configured")

    if not name:
        name = next(iter(providers))

    if name not in providers:
        raise KeyError(f"Unknown open data provider: {name}")

    return providers[name]