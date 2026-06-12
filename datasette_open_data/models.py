from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Resource:
    id: str
    name: str | None = None
    description: str | None = None
    format: str | None = None
    url: str | None = None
    datastore_active: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class DatasetSummary:
    id: str
    name: str
    title: str
    notes: str | None = None
    organization: str | None = None
    tags: list[str] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class Dataset(DatasetSummary):
    license_title: str | None = None
    url: str | None = None
