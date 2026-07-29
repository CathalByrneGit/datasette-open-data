from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..models import Dataset, DatasetSummary, Resource


class PxStatError(RuntimeError):
    pass


class PxStatProvider:
    """Async client for PxStat open data portals (e.g. CSO Ireland).

    Uses the PxStat JSON-RPC API for catalog/metadata/navigation and the
    RESTful API for CSV data downloads. The two base URLs are derived from
    base_url by convention:
        jsonrpc_url = base_url + /public/api.jsonrpc
        rest_base   = base_url + /public/api.restful

    search() performs a live fetch of the full catalog (~12,600 tables for
    CSO) and filters in-memory. Run scripts/build_catalog.py to populate
    catalog.db so the views layer uses the fast FTS path instead.
    """

    type = "pxstat"

    def __init__(
        self,
        name: str,
        base_url: str,
        title: str | None = None,
        language: str = "en",
        timeout: float = 60.0,
    ):
        self.name = name
        self.title = title or name
        self.base_url = base_url.rstrip("/")
        self.language = language
        self.timeout = timeout
        self.jsonrpc_url = f"{self.base_url}/public/api.jsonrpc"
        self.rest_base = f"{self.base_url}/public/api.restful"

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.post(
                self.jsonrpc_url,
                json={
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params or {},
                },
            )
            response.raise_for_status()
            data = response.json()

        if "error" in data:
            raise PxStatError(str(data["error"]))
        return data.get("result")

    def _csv_url(self, matrix: str) -> str:
        return f"{self.rest_base}/PxStat.Data.Cube_API.ReadDataset/{matrix}/CSV/{self.language}/"

    # ------------------------------------------------------------------
    # Model converters
    # ------------------------------------------------------------------

    def _resource_from_matrix(self, matrix: str, title: str | None = None) -> Resource:
        return Resource(
            id=matrix,
            name=matrix,
            description=title,
            format="CSV",
            url=self._csv_url(matrix),
            datastore_active=False,
        )

    def _summary_from_item(self, item: dict[str, Any]) -> DatasetSummary:
        ext = item.get("extension") or {}
        matrix = ext.get("matrix") or item.get("id", "")
        title = item.get("label") or matrix

        sbj = ext.get("subject") or {}
        tags = [sbj["SbjValue"]] if sbj.get("SbjValue") else []

        return DatasetSummary(
            id=matrix,
            name=matrix,
            title=title,
            notes=None,
            organization=None,
            tags=tags,
            resources=[self._resource_from_matrix(matrix, title)],
            extras=item,
        )

    def _dataset_from_metadata(self, matrix: str, meta: dict[str, Any]) -> Dataset:
        title = meta.get("label") or matrix

        notes_list = meta.get("note") or []
        notes = " ".join(notes_list) if notes_list else None

        copyright_info = meta.get("copyright") or {}
        organization = copyright_info.get("name") if isinstance(copyright_info, dict) else None

        return Dataset(
            id=matrix,
            name=matrix,
            title=title,
            notes=notes,
            organization=organization,
            tags=[],
            resources=[self._resource_from_matrix(matrix, title)],
            license_title=None,
            url=meta.get("href") or f"{self.base_url}/en/{matrix}",
            extras=meta,
        )

    # ------------------------------------------------------------------
    # Provider interface
    # ------------------------------------------------------------------

    async def search(self, query: str, rows: int = 20, start: int = 0) -> list[DatasetSummary]:
        result = await self._rpc(
            "PxStat.Data.Cube_API.ReadCollection",
            {"language": self.language},
        )
        items = (result or {}).get("link", {}).get("item") or []

        q_lower = query.lower()
        filtered = [
            item
            for item in items
            if q_lower in (item.get("label") or "").lower()
            or q_lower in ((item.get("extension") or {}).get("matrix") or "").lower()
        ]

        return [self._summary_from_item(item) for item in filtered[start : start + rows]]

    async def dataset(self, dataset_id: str) -> Dataset:
        meta = await self._rpc(
            "PxStat.Data.Cube_API.ReadMetadata",
            {"matrix": dataset_id, "language": self.language},
        )
        return self._dataset_from_metadata(dataset_id, meta or {})

    async def resource(self, resource_id: str) -> Resource:
        # resource_id IS the matrix code for PxStat.
        try:
            meta = await self._rpc(
                "PxStat.Data.Cube_API.ReadMetadata",
                {"matrix": resource_id, "language": self.language},
            )
            title = (meta or {}).get("label")
        except PxStatError:
            title = None
        return self._resource_from_matrix(resource_id, title)

    async def groups(self) -> list[dict[str, Any]]:
        tree = await self._rpc(
            "PxStat.System.Navigation.Navigation_API.Read",
            {"LngIsoCode": self.language},
        )
        return [
            {
                "id": str(t["ThmCode"]),
                "name": t["ThmValue"],
                "title": t["ThmValue"],
                "subjects": [
                    {"id": str(s["SbjCode"]), "name": s["SbjValue"]}
                    for s in (t.get("subject") or [])
                ],
            }
            for t in (tree or [])
        ]

    async def organizations(self) -> list[dict[str, Any]]:
        return []

    async def tags(self) -> list[str]:
        tree = await self._rpc(
            "PxStat.System.Navigation.Navigation_API.Read",
            {"LngIsoCode": self.language},
        )
        return [
            s["SbjValue"]
            for t in (tree or [])
            for s in (t.get("subject") or [])
            if s.get("SbjValue")
        ]

    async def datastore_preview(self, resource_id: str, limit: int = 10) -> dict[str, Any]:
        csv_url = self._csv_url(resource_id)
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(csv_url)
            response.raise_for_status()

        text = response.content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        records = [dict(row) for i, row in enumerate(reader) if i < limit]

        fields = [{"id": k, "type": "text"} for k in (records[0].keys() if records else [])]
        return {"records": records, "fields": fields, "total": len(records)}

    async def iter_catalog(
        self,
        limit: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield one catalog record per PxStat matrix.

        ReadCollection returns every table in a single response, carrying the
        matrix code, label and subject. Theme (group) membership comes from the
        navigation tree, fetched once and joined on subject code.

        Per-table notes are deliberately not fetched: that would mean one
        ReadMetadata call per table (~12,600 for CSO). Notes are filled in
        on demand by dataset().
        """
        theme_by_subject: dict[str, dict[str, Any]] = {}
        try:
            tree = await self._rpc(
                "PxStat.System.Navigation.Navigation_API.Read",
                {"LngIsoCode": self.language},
            )
            for theme in tree or []:
                group = {
                    "id": str(theme.get("ThmCode")),
                    "name": theme.get("ThmValue"),
                    "title": theme.get("ThmValue"),
                    "description": None,
                }
                for subject in theme.get("subject") or []:
                    theme_by_subject[str(subject.get("SbjCode"))] = group
        except (PxStatError, httpx.HTTPError):
            # Navigation is a nice-to-have; the catalog is still usable without it.
            pass

        result = await self._rpc(
            "PxStat.Data.Cube_API.ReadCollection",
            {"language": self.language},
        )
        items = (result or {}).get("link", {}).get("item") or []

        for index, item in enumerate(items):
            if limit is not None and index >= limit:
                return

            ext = item.get("extension") or {}
            matrix = ext.get("matrix")
            if not matrix:
                continue

            title = item.get("label") or matrix
            subject = ext.get("subject") or {}
            subject_value = subject.get("SbjValue")
            group = theme_by_subject.get(str(subject.get("SbjCode")))

            yield {
                "id": matrix,
                "name": matrix,
                "title": title,
                "notes": None,
                "organization": {},
                "license_title": None,
                "url": f"{self.base_url}/en/{matrix}",
                "metadata_created": None,
                "metadata_modified": item.get("updated"),
                "resources": [
                    {
                        "id": matrix,
                        "name": matrix,
                        "description": title,
                        "format": "CSV",
                        "url": self._csv_url(matrix),
                        "datastore_active": False,
                        "created": None,
                        "last_modified": item.get("updated"),
                    }
                ],
                "tags": (
                    [{"name": subject_value, "display_name": subject_value}]
                    if subject_value
                    else []
                ),
                "groups": [group] if group else [],
            }
