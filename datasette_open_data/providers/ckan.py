from __future__ import annotations

from typing import Any

import httpx

from ..models import Dataset, DatasetSummary, Resource


class CKANError(RuntimeError):
    pass


class CKANProvider:
    """Small async CKAN client.

    Central Bank note: catalog APIs work at /api/3, while the DataStore info snippet
    currently documents /en_GB/api/3/action/datastore_search. We support separate
    api_base_url and datastore_api_base_url for that reason.
    """

    type = "ckan"

    def __init__(
        self,
        name: str,
        base_url: str,
        title: str | None = None,
        api_base_url: str | None = None,
        datastore_api_base_url: str | None = None,
        timeout: float = 30.0,
    ):
        self.name = name
        self.title = title or name
        self.base_url = base_url.rstrip("/")
        self.api_base_url = (api_base_url or f"{self.base_url}/api/3").rstrip("/")
        self.datastore_api_base_url = (
            datastore_api_base_url or self.api_base_url
        ).rstrip("/")
        self.timeout = timeout

    async def _get(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        datastore: bool = False,
    ) -> dict[str, Any]:
        base = self.datastore_api_base_url if datastore else self.api_base_url
        url = f"{base}/action/{action}"

        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
        ) as client:
            response = await client.get(url, params=params or {})
            response.raise_for_status()
            data = response.json()

        if not data.get("success", False):
            raise CKANError(str(data.get("error") or data))

        return data["result"]

    def _resource_from_ckan(self, data: dict[str, Any]) -> Resource:
        return Resource(
            id=data["id"],
            name=data.get("name"),
            description=data.get("description"),
            format=data.get("format"),
            url=data.get("url"),
            datastore_active=bool(data.get("datastore_active")),
            extras=data,
        )

    def _summary_from_ckan(self, data: dict[str, Any]) -> DatasetSummary:
        organization = data.get("organization") or {}

        return DatasetSummary(
            id=data["id"],
            name=data.get("name") or data["id"],
            title=data.get("title") or data.get("name") or data["id"],
            notes=data.get("notes"),
            organization=organization.get("title") or organization.get("name"),
            tags=[
                tag.get("display_name") or tag.get("name")
                for tag in data.get("tags", [])
                if tag.get("display_name") or tag.get("name")
            ],
            resources=[
                self._resource_from_ckan(resource)
                for resource in data.get("resources", [])
            ],
            extras=data,
        )

    def _dataset_from_ckan(self, data: dict[str, Any]) -> Dataset:
        summary = self._summary_from_ckan(data)

        return Dataset(
            id=summary.id,
            name=summary.name,
            title=summary.title,
            notes=summary.notes,
            organization=summary.organization,
            tags=summary.tags,
            resources=summary.resources,
            extras=summary.extras,
            license_title=data.get("license_title"),
            url=data.get("url"),
        )

    async def search(
        self,
        query: str,
        rows: int = 20,
        start: int = 0,
    ) -> list[DatasetSummary]:
        result = await self._get(
            "package_search",
            {
                "q": query,
                "rows": rows,
                "start": start,
            },
        )

        return [
            self._summary_from_ckan(item)
            for item in result.get("results", [])
        ]

    async def dataset(self, dataset_id: str) -> Dataset:
        result = await self._get("package_show", {"id": dataset_id})
        return self._dataset_from_ckan(result)

    async def groups(self) -> list[dict[str, Any]]:
        return await self._get("group_list", {"all_fields": True})

    async def organizations(self) -> list[dict[str, Any]]:
        return await self._get("organization_list", {"all_fields": True})

    async def tags(self) -> list[str]:
        return await self._get("tag_list")

    async def datastore_preview(
        self,
        resource_id: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        return await self._get(
            "datastore_search",
            {
                "resource_id": resource_id,
                "limit": limit,
            },
            datastore=True,
        )
    
    async def resource(self, resource_id: str) -> Resource:
        result = await self._get("resource_show", {"id": resource_id})
        return self._resource_from_ckan(result)