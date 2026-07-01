from __future__ import annotations

from typing import Any

import httpx

from ..models import Dataset, DatasetSummary, Resource


class SocrataError(RuntimeError):
    pass


class SocrataProvider:
    """Async client for Socrata open data portals (SODA API).

    Works with any Socrata-powered portal such as NYC Open Data, Chicago Data Portal, etc.
    Each Socrata view (dataset) is exposed as a single CSV-downloadable resource.
    """

    type = "socrata"

    def __init__(
        self,
        name: str,
        base_url: str,
        title: str | None = None,
        timeout: float = 30.0,
    ):
        self.name = name
        self.title = title or name
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(url, params=params or {})
            response.raise_for_status()
            return response.json()

    def _resource_from_view(self, view_id: str, title: str | None = None) -> Resource:
        # Socrata data is downloadable as CSV via the SODA export URL.
        # A high $limit ensures we get more than the default 1000-row cap.
        return Resource(
            id=view_id,
            name=view_id,
            description=title,
            format="CSV",
            url=f"{self.base_url}/resource/{view_id}.csv?$limit=50000",
            datastore_active=False,
        )

    def _summary_from_result(self, result: dict[str, Any]) -> DatasetSummary:
        resource = result.get("resource", {})
        classification = result.get("classification", {})

        view_id = resource.get("id", "")
        title = resource.get("name") or view_id
        tags = [t for t in (classification.get("tags") or []) if t]

        return DatasetSummary(
            id=view_id,
            name=view_id,
            title=title,
            notes=resource.get("description"),
            organization=None,
            tags=tags,
            resources=[self._resource_from_view(view_id, title)],
            extras=result,
        )

    def _dataset_from_view(self, data: dict[str, Any]) -> Dataset:
        view_id = data.get("id", "")
        title = data.get("name") or view_id

        raw_tags = data.get("tags") or []
        tags = [
            (t.get("name") if isinstance(t, dict) else t)
            for t in raw_tags
            if (t.get("name") if isinstance(t, dict) else t)
        ]

        raw_license = data.get("license")
        license_title = (
            raw_license.get("name") if isinstance(raw_license, dict) else raw_license
        )

        return Dataset(
            id=view_id,
            name=view_id,
            title=title,
            notes=data.get("description"),
            organization=data.get("attribution"),
            tags=tags,
            resources=[self._resource_from_view(view_id, title)],
            license_title=license_title,
            url=data.get("webUri") or f"{self.base_url}/d/{view_id}",
            extras=data,
        )

    async def search(
        self, query: str, rows: int = 20, start: int = 0
    ) -> list[DatasetSummary]:
        data = await self._get(
            "/api/catalog/v1",
            {"q": query, "limit": rows, "offset": start, "only": "datasets"},
        )
        return [self._summary_from_result(r) for r in data.get("results", [])]

    async def dataset(self, dataset_id: str) -> Dataset:
        data = await self._get(f"/api/views/{dataset_id}.json")
        return self._dataset_from_view(data)

    async def resource(self, resource_id: str) -> Resource:
        data = await self._get(f"/api/views/{resource_id}.json")
        return self._resource_from_view(resource_id, data.get("name"))

    async def groups(self) -> list[dict[str, Any]]:
        data = await self._get("/api/catalog/v1/categories")
        return [
            {"name": item["category"], "title": item["category"], "count": item.get("count", 0)}
            for item in data
            if item.get("category")
        ]

    async def organizations(self) -> list[dict[str, Any]]:
        return []

    async def tags(self) -> list[str]:
        data = await self._get("/api/catalog/v1/tags")
        return [item["tag"] for item in data if item.get("tag")]

    async def datastore_preview(
        self, resource_id: str, limit: int = 10
    ) -> dict[str, Any]:
        records = await self._get(f"/resource/{resource_id}.json", {"$limit": limit})
        if not isinstance(records, list):
            raise SocrataError(
                f"Unexpected response from SODA API for resource {resource_id!r}"
            )
        fields = [
            {"id": k, "type": "text"} for k in (records[0].keys() if records else [])
        ]
        return {"records": records, "fields": fields, "total": len(records)}
