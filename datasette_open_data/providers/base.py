from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from ..models import Dataset, DatasetSummary, Resource


class OpenDataProvider(Protocol):
    name: str
    title: str
    type: str

    async def search(self, query: str, rows: int = 20, start: int = 0) -> list[DatasetSummary]: ...
    async def dataset(self, dataset_id: str) -> Dataset: ...
    async def resource(self, resource_id: str) -> Resource: ...
    async def groups(self) -> list[dict[str, Any]]: ...
    async def organizations(self) -> list[dict[str, Any]]: ...
    async def tags(self) -> list[str]: ...
    async def datastore_preview(self, resource_id: str, limit: int = 10) -> dict[str, Any]: ...

    def iter_catalog(self, limit: int | None = None) -> AsyncIterator[dict[str, Any]]:
        """Yield every dataset in the portal as a catalog record.

        Used by scripts/build_catalog.py to populate catalog.db. Each yielded
        dict uses the CKAN package shape, which is the catalog schema's native
        format; non-CKAN providers translate into it:

            {
                "id", "name", "title", "notes",
                "organization": {"id", "name", "title", "description"},
                "license_title", "url",
                "metadata_created", "metadata_modified",
                "resources": [
                    {"id", "name", "description", "format", "url",
                     "datastore_active", "created", "last_modified"}
                ],
                "tags": [{"name", "display_name"}],
                "groups": [{"id", "name", "title", "description"}],
            }

        Only "id" is required. Implementations should stop after `limit`
        records when it is not None.
        """
        ...
