"""LPG backends: an in-memory property-graph engine and a Neo4j (Bolt) client."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ..compilers.cypher import compile_cypher
from ..ir import Direction, Op, Query
from ..model import Node, ResultSet
from .base import Backend


@dataclass
class Edge:
    src: str
    dst: str
    type: str
    properties: dict[str, Any] = field(default_factory=dict)


def _compare(left: Any, op: Op, right: Any) -> bool:
    try:
        if op == Op.EQ:
            return left == right
        if op == Op.NE:
            return left != right
        if op == Op.LT:
            return left < right
        if op == Op.LE:
            return left <= right
        if op == Op.GT:
            return left > right
        if op == Op.GE:
            return left >= right
        if op == Op.CONTAINS:
            return str(right).lower() in str(left).lower()
        if op == Op.STARTS_WITH:
            return str(left).lower().startswith(str(right).lower())
    except TypeError:
        return False
    return False


class InMemoryLpgBackend(Backend):
    """Tiny LPG engine that evaluates the IR directly (no Cypher parser)."""

    kind = "lpg"

    def __init__(self, name: str, identity_property: str = "name"):
        self.name = name
        self._identity = identity_property
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self._out: dict[str, list[Edge]] = {}
        self._in: dict[str, list[Edge]] = {}

    @classmethod
    def from_json(cls, name: str, path: str, identity_property: str = "name") -> InMemoryLpgBackend:
        with open(path) as fh:
            data = json.load(fh)
        b = cls(name, identity_property)
        for n in data.get("nodes", []):
            b.add_node(n["id"], n.get("labels", []), n.get("properties", {}))
        for e in data.get("edges", []):
            b.add_edge(e["src"], e["dst"], e["type"], e.get("properties", {}))
        return b

    def add_node(self, id: str, labels: list[str], properties: dict[str, Any] | None = None) -> Node:
        node = Node(id=id, labels=list(labels), properties=dict(properties or {}), source=self.name)
        self.nodes[id] = node
        return node

    def add_edge(self, src: str, dst: str, type: str, properties: dict[str, Any] | None = None) -> Edge:
        e = Edge(src, dst, type, dict(properties or {}))
        self.edges.append(e)
        self._out.setdefault(src, []).append(e)
        self._in.setdefault(dst, []).append(e)
        return e

    def labels(self) -> set[str]:
        return {lbl for n in self.nodes.values() for lbl in n.labels}

    def relationships(self) -> set[str]:
        return {e.type for e in self.edges}

    def properties(self) -> set[str]:
        return {k for n in self.nodes.values() for k in n.properties}

    def identity_property(self) -> str:
        return self._identity

    def compile(self, q: Query) -> str:
        return compile_cypher(q)

    def execute_native(self, text: str) -> ResultSet:
        raise NotImplementedError("InMemoryLpgBackend has no Cypher parser; use Neo4jBackend for raw Cypher")

    # -- evaluation ---------------------------------------------------------

    def _node_ok(self, q: Query, var: str, node: Node) -> bool:
        pat = q.node(var)
        if not set(pat.labels) <= set(node.labels):
            return False
        for k, v in pat.props.items():
            if node.properties.get(k) != v:
                return False
        return all(
            _compare(node.properties.get(f.prop), f.op, f.value)
            for f in q.filters
            if f.var == var and f.prop in node.properties
        ) and all(f.prop in node.properties for f in q.filters if f.var == var)

    def _candidates(self, q: Query, var: str) -> Iterator[Node]:
        return (n for n in self.nodes.values() if self._node_ok(q, var, n))

    def _match(self, q: Query) -> Iterator[dict[str, Node]]:
        order = q.variables()

        def extend(binding: dict[str, Node], remaining: list[str]) -> Iterator[dict[str, Node]]:
            if not remaining:
                if self._edges_ok(q, binding):
                    yield binding
                return
            # pick a variable connected to something already bound when possible
            var = next((v for v in remaining if self._connected(q, v, binding)), remaining[0])
            rest = [v for v in remaining if v != var]
            for cand in self._neighbours(q, var, binding):
                yield from extend({**binding, var: cand}, rest)

        yield from extend({}, order)

    def _connected(self, q: Query, var: str, binding: dict[str, Node]) -> bool:
        return any((e.src == var and e.dst in binding) or (e.dst == var and e.src in binding) for e in q.edges)

    def _neighbours(self, q: Query, var: str, binding: dict[str, Node]) -> Iterator[Node]:
        for e in q.edges:
            other = e.dst if e.src == var else e.src if e.dst == var else None
            if other is None or other not in binding:
                continue
            bound = binding[other]
            forward = e.src == other  # bound node is the source of the pattern edge
            if e.direction == Direction.ANY:
                pool = self._out.get(bound.id, []) + self._in.get(bound.id, [])
            elif (e.direction == Direction.OUT) == forward:
                pool = self._out.get(bound.id, [])
            else:
                pool = self._in.get(bound.id, [])
            seen: set[str] = set()
            for edge in pool:
                if e.types and edge.type not in e.types:
                    continue
                nid = edge.dst if edge.src == bound.id else edge.src
                if nid in seen:
                    continue
                seen.add(nid)
                node = self.nodes[nid]
                if self._node_ok(q, var, node):
                    yield node
            return
        yield from self._candidates(q, var)

    def _edges_ok(self, q: Query, binding: dict[str, Node]) -> bool:
        for e in q.edges:
            s, d = binding[e.src].id, binding[e.dst].id
            if not any(
                self._edge_matches(edge, s, d, e.types, e.direction)
                for edge in self._out.get(s, []) + self._in.get(s, [])
            ):
                return False
        return True

    @staticmethod
    def _edge_matches(edge: Edge, s: str, d: str, types: list[str], direction: Direction) -> bool:
        if types and edge.type not in types:
            return False
        if direction == Direction.OUT:
            return edge.src == s and edge.dst == d
        if direction == Direction.IN:
            return edge.src == d and edge.dst == s
        return {edge.src, edge.dst} == {s, d}

    def execute(self, q: Query) -> ResultSet:
        bindings = list(self._match(q))
        if q.aggregate == "count":
            target = q.returns[0].var if q.returns else q.nodes[0].var
            return ResultSet(["count"], [{"count": len({b[target].id for b in bindings})}])

        projections = q.returns
        columns = [p.name for p in projections] or q.variables()
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for b in bindings:
            if projections:
                row = {p.name: (b[p.var].properties.get(p.prop) if p.prop else b[p.var]) for p in projections}
            else:
                row = {v: b[v] for v in q.variables()}
            if q.distinct:
                key = json.dumps(
                    {k: (v.id if isinstance(v, Node) else v) for k, v in row.items()}, sort_keys=True, default=str
                )
                if key in seen:
                    continue
                seen.add(key)
            rows.append(row)
        if q.order_by:
            ob = q.order_by

            def sort_key(row: dict[str, Any]) -> Any:
                v = row.get(f"{ob.var}.{ob.prop}")
                if v is None:
                    node = row.get(ob.var)
                    v = node.properties.get(ob.prop) if isinstance(node, Node) else None
                return (v is None, v)

            rows.sort(key=sort_key, reverse=ob.descending)
        if q.limit is not None:
            rows = rows[: q.limit]
        return ResultSet(columns, rows)


class Neo4jBackend(Backend):
    """Executes compiled Cypher against a Neo4j (or any Bolt-compatible) server."""

    kind = "lpg"

    def __init__(
        self,
        name: str,
        uri: str,
        auth: tuple[str, str] | None = None,
        database: str | None = None,
        identity_property: str = "name",
    ):
        try:
            from neo4j import GraphDatabase  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ImportError("pip install neo4j to use Neo4jBackend") from exc
        self.name = name
        self._identity = identity_property
        self._driver = GraphDatabase.driver(uri, auth=auth)
        self._database = database
        self._schema: dict[str, set[str]] | None = None

    def _load_schema(self) -> dict[str, set[str]]:
        if self._schema is None:
            with self._driver.session(database=self._database) as s:
                labels = {r[0] for r in s.run("CALL db.labels()")}
                rels = {r[0] for r in s.run("CALL db.relationshipTypes()")}
                props = {r[0] for r in s.run("CALL db.propertyKeys()")}
            self._schema = {"labels": labels, "rels": rels, "props": props}
        return self._schema

    def labels(self) -> set[str]:
        return self._load_schema()["labels"]

    def relationships(self) -> set[str]:
        return self._load_schema()["rels"]

    def properties(self) -> set[str]:
        return self._load_schema()["props"]

    def identity_property(self) -> str:
        return self._identity

    def compile(self, q: Query) -> str:
        return compile_cypher(q)

    def execute(self, q: Query) -> ResultSet:
        return self.execute_native(self.compile(q))

    def execute_native(self, text: str) -> ResultSet:
        from neo4j.graph import Node as NeoNode  # type: ignore

        def conv(v: Any) -> Any:
            if isinstance(v, NeoNode):
                return Node(id=str(v.element_id), labels=list(v.labels), properties=dict(v), source=self.name)
            return v

        with self._driver.session(database=self._database) as s:
            result = s.run(text)
            columns = list(result.keys())
            rows = [{k: conv(v) for k, v in r.items()} for r in result]
        return ResultSet(columns, rows)
