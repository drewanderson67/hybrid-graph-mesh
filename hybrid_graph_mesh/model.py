"""Unified result model shared by all backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Node:
    id: str
    labels: list[str] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "labels": self.labels,
            "properties": self.properties,
            "source": self.source,
        }


Row = dict[str, Any]


@dataclass
class ResultSet:
    columns: list[str]
    rows: list[Row]
    plan: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "rows": [{k: (v.to_dict() if isinstance(v, Node) else v) for k, v in r.items()} for r in self.rows],
            "plan": self.plan,
        }
