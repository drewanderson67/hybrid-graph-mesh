"""Backend-neutral query intermediate representation.

Every query in the mesh -- whether it originated as natural language, Cypher,
or SPARQL -- is normalised into a `Query`, which the compilers then render
into the dialect of a concrete backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

Scalar = str | int | float | bool | None


class Direction(str, Enum):
    OUT = "out"
    IN = "in"
    ANY = "any"


class Op(str, Enum):
    EQ = "="
    NE = "!="
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"


@dataclass
class NodePattern:
    var: str
    labels: list[str] = field(default_factory=list)
    props: dict[str, Scalar] = field(default_factory=dict)


@dataclass
class EdgePattern:
    src: str
    dst: str
    types: list[str] = field(default_factory=list)
    direction: Direction = Direction.OUT
    var: str | None = None


@dataclass
class Filter:
    var: str
    prop: str
    op: Op
    value: Scalar


@dataclass
class Projection:
    var: str
    prop: str | None = None
    alias: str | None = None

    @property
    def name(self) -> str:
        if self.alias:
            return self.alias
        return f"{self.var}.{self.prop}" if self.prop else self.var


@dataclass
class OrderBy:
    var: str
    prop: str
    descending: bool = False


@dataclass
class Query:
    nodes: list[NodePattern] = field(default_factory=list)
    edges: list[EdgePattern] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    returns: list[Projection] = field(default_factory=list)
    aggregate: Literal["count"] | None = None
    distinct: bool = True
    order_by: OrderBy | None = None
    limit: int | None = None

    def node(self, var: str) -> NodePattern:
        for n in self.nodes:
            if n.var == var:
                return n
        raise KeyError(var)

    def variables(self) -> list[str]:
        return [n.var for n in self.nodes]

    def labels(self) -> set[str]:
        return {lbl for n in self.nodes for lbl in n.labels}

    def rel_types(self) -> set[str]:
        return {t for e in self.edges for t in e.types}

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [{"var": n.var, "labels": n.labels, "props": n.props} for n in self.nodes],
            "edges": [
                {
                    "src": e.src,
                    "dst": e.dst,
                    "types": e.types,
                    "direction": e.direction.value,
                    "var": e.var,
                }
                for e in self.edges
            ],
            "filters": [{"var": f.var, "prop": f.prop, "op": f.op.value, "value": f.value} for f in self.filters],
            "returns": [{"var": p.var, "prop": p.prop, "alias": p.alias} for p in self.returns],
            "aggregate": self.aggregate,
            "distinct": self.distinct,
            "order_by": (
                {
                    "var": self.order_by.var,
                    "prop": self.order_by.prop,
                    "descending": self.order_by.descending,
                }
                if self.order_by
                else None
            ),
            "limit": self.limit,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Query:
        ob = d.get("order_by")
        return cls(
            nodes=[
                NodePattern(n["var"], list(n.get("labels", [])), dict(n.get("props", {}))) for n in d.get("nodes", [])
            ],
            edges=[
                EdgePattern(
                    e["src"],
                    e["dst"],
                    list(e.get("types", [])),
                    Direction(e.get("direction", "out")),
                    e.get("var"),
                )
                for e in d.get("edges", [])
            ],
            filters=[Filter(f["var"], f["prop"], Op(f["op"]), f["value"]) for f in d.get("filters", [])],
            returns=[Projection(p["var"], p.get("prop"), p.get("alias")) for p in d.get("returns", [])],
            aggregate=d.get("aggregate"),
            distinct=d.get("distinct", True),
            order_by=OrderBy(ob["var"], ob["prop"], ob.get("descending", False)) if ob else None,
            limit=d.get("limit"),
        )
