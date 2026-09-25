"""The virtual mesh: routes IR queries to RDF/LPG backends and stitches results.

Nothing is stored here. A query is either pushed down whole to every backend
that can answer it (results unioned), or decomposed into per-backend
sub-patterns that are joined in memory on shared variables, using each
backend's identity property (e.g. `name`, or an owl:sameAs-style key) to
align entities across stores.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .backends.base import Backend
from .ir import Filter, NodePattern, Projection, Query
from .model import Node, ResultSet


class PlanningError(Exception):
    pass


@dataclass
class SubPlan:
    backend: Backend
    query: Query
    native: str


@dataclass
class Plan:
    strategy: str  # "pushdown" | "federated"
    steps: list[SubPlan] = field(default_factory=list)
    join_vars: list[str] = field(default_factory=list)

    def to_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "backend": s.backend.name,
                "kind": s.backend.kind,
                "strategy": self.strategy,
                "language": "sparql" if s.backend.kind == "rdf" else "cypher",
                "query": s.native,
            }
            for s in self.steps
        ]


class VirtualMesh:
    def __init__(self, backends: list[Backend] | None = None):
        self.backends: list[Backend] = list(backends or [])

    def add(self, backend: Backend) -> VirtualMesh:
        self.backends.append(backend)
        return self

    # -- schema -----------------------------------------------------------

    def labels(self) -> set[str]:
        return set().union(*(b.labels() for b in self.backends)) if self.backends else set()

    def relationships(self) -> set[str]:
        return set().union(*(b.relationships() for b in self.backends)) if self.backends else set()

    def properties(self) -> set[str]:
        return set().union(*(b.properties() for b in self.backends)) if self.backends else set()

    def schema(self) -> dict[str, Any]:
        return {
            "backends": [
                {
                    "name": b.name,
                    "kind": b.kind,
                    "labels": sorted(b.labels()),
                    "relationships": sorted(b.relationships()),
                    "properties": sorted(b.properties()),
                    "identity_property": b.identity_property(),
                }
                for b in self.backends
            ],
            "labels": sorted(self.labels()),
            "relationships": sorted(self.relationships()),
            "properties": sorted(self.properties()),
        }

    # -- planning -----------------------------------------------------------

    def plan(self, q: Query) -> Plan:
        if not self.backends:
            raise PlanningError("mesh has no backends")
        covering = [b for b in self.backends if b.covers(q) and self._filters_ok(b, q)]
        if covering:
            return Plan("pushdown", [SubPlan(b, q, b.compile(q)) for b in covering])
        return self._federate(q)

    @staticmethod
    def _filters_ok(b: Backend, q: Query) -> bool:
        props = b.properties()
        needed = {f.prop for f in q.filters} | {k for n in q.nodes for k in n.props}
        needed |= {p.prop for p in q.returns if p.prop}
        if q.order_by:
            needed.add(q.order_by.prop)
        return needed <= props

    def _federate(self, q: Query) -> Plan:
        assignments: dict[str, list[int]] = {b.name: [] for b in self.backends}
        for i, e in enumerate(q.edges):
            needed_labels = set(q.node(e.src).labels) | set(q.node(e.dst).labels)
            owner = next(
                (b for b in self.backends if set(e.types) <= b.relationships() and self._some_labels(b, needed_labels)),
                None,
            )
            if owner is None:
                raise PlanningError(f"no backend can answer relationship(s) {e.types}")
            assignments[owner.name].append(i)

        steps: list[SubPlan] = []
        touched: set[str] = set()
        for b in self.backends:
            idxs = assignments[b.name]
            if not idxs:
                continue
            edges = [q.edges[i] for i in idxs]
            vars_ = []
            for e in edges:
                for v in (e.src, e.dst):
                    if v not in vars_:
                        vars_.append(v)
            touched.update(vars_)
            steps.append(self._subplan(b, q, vars_, edges))

        for n in q.nodes:
            if n.var in touched:
                continue
            owner = next((b for b in self.backends if set(n.labels) <= b.labels()), None)
            if owner is None:
                raise PlanningError(f"no backend knows label(s) {n.labels}")
            steps.append(self._subplan(owner, q, [n.var], []))

        # filters / projections whose property no edge-owning backend knows are
        # pushed to a backend that does, as a node-only lookup joined on identity
        applied = {(f.var, f.prop) for s in steps for f in s.query.filters}
        wanted: dict[str, set[str]] = {}
        for f in q.filters:
            if (f.var, f.prop) not in applied:
                wanted.setdefault(f.var, set()).add(f.prop)
        for p in q.returns:
            if p.prop and not any(p.var in s.query.variables() and p.prop in s.backend.properties() for s in steps):
                wanted.setdefault(p.var, set()).add(p.prop)
        if q.order_by and not any(
            q.order_by.var in s.query.variables() and q.order_by.prop in s.backend.properties() for s in steps
        ):
            wanted.setdefault(q.order_by.var, set()).add(q.order_by.prop)
        for var, props in wanted.items():
            owner = next((b for b in self.backends if props <= b.properties()), None)
            if owner is None:
                raise PlanningError(f"no backend can evaluate property(s) {sorted(props)} on {var}")
            steps.append(self._subplan(owner, q, [var], []))

        counts: dict[str, int] = {}
        for s in steps:
            for v in s.query.variables():
                counts[v] = counts.get(v, 0) + 1
        join_vars = [v for v, c in counts.items() if c > 1]
        return Plan("federated", steps, join_vars)

    @staticmethod
    def _some_labels(b: Backend, labels: set[str]) -> bool:
        return not labels or bool(labels & b.labels())

    @staticmethod
    def _subplan(b: Backend, q: Query, vars_: list[str], edges: list) -> SubPlan:
        known_labels, known_props = b.labels(), b.properties()
        nodes = [
            NodePattern(
                v,
                [lbl for lbl in q.node(v).labels if lbl in known_labels],
                {k: val for k, val in q.node(v).props.items() if k in known_props},
            )
            for v in vars_
        ]
        filters: list[Filter] = [f for f in q.filters if f.var in vars_ and f.prop in known_props]
        sub = Query(
            nodes=nodes,
            edges=edges,
            filters=filters,
            returns=[Projection(v) for v in vars_],
            distinct=True,
        )
        return SubPlan(b, sub, b.compile(sub))

    # -- execution ----------------------------------------------------------

    def execute(self, q: Query) -> ResultSet:
        plan = self.plan(q)
        if plan.strategy == "pushdown":
            rs = self._union([s.backend.execute(s.query) for s in plan.steps], q)
        else:
            rs = self._join(plan, q)
        rs.plan = plan.to_dicts()
        return rs

    def _union(self, results: list[ResultSet], q: Query) -> ResultSet:
        if q.aggregate == "count":
            # counts from different stores may overlap; report the sum of
            # distinct identities when we have them, else the plain sum
            total = sum(int(r.rows[0]["count"]) for r in results if r.rows)
            return ResultSet(["count"], [{"count": total}])
        columns = results[0].columns
        merged: dict[str, dict[str, Any]] = {}
        for r in results:
            for row in r.rows:
                key = self._row_key(row)
                if key in merged:
                    prev = merged[key]
                    for k, v in row.items():
                        if isinstance(v, Node) and isinstance(prev.get(k), Node):
                            prev[k] = self._merge_node(prev[k], v)
                else:
                    merged[key] = dict(row)
        return self._finish(columns, list(merged.values()), q)

    def _join(self, plan: Plan, q: Query) -> ResultSet:
        partials = [(s, s.backend.execute(s.query)) for s in plan.steps]
        bindings: list[dict[str, Node]] = [{}]
        for step, rs in partials:
            idp = step.backend.identity_property()
            new: list[dict[str, Node]] = []
            for b in bindings:
                shared = [v for v in step.query.variables() if v in b]
                for row in rs.rows:
                    if all(self._same_entity(b[v], row[v], idp) for v in shared):
                        merged = dict(b)
                        for v in step.query.variables():
                            merged[v] = self._merge_node(b.get(v), row[v])
                        new.append(merged)
            bindings = new

        if q.aggregate == "count":
            target = q.returns[0].var if q.returns else q.nodes[0].var
            ids = {self._identity(b[target], None) for b in bindings}
            return ResultSet(["count"], [{"count": len(ids)}])

        columns = [p.name for p in q.returns] or q.variables()
        rows = []
        for b in bindings:
            if q.returns:
                rows.append({p.name: (b[p.var].properties.get(p.prop) if p.prop else b[p.var]) for p in q.returns})
            else:
                rows.append({v: b[v] for v in q.variables()})
        return self._finish(columns, rows, q)

    def _finish(self, columns: list[str], rows: list[dict[str, Any]], q: Query) -> ResultSet:
        if q.distinct:
            seen: set[str] = set()
            uniq = []
            for row in rows:
                k = self._row_key(row)
                if k not in seen:
                    seen.add(k)
                    uniq.append(row)
            rows = uniq
        if q.order_by:
            ob = q.order_by

            def sort_key(row: dict[str, Any]) -> Any:
                v = row.get(f"{ob.var}.{ob.prop}")
                if v is None and isinstance(row.get(ob.var), Node):
                    v = row[ob.var].properties.get(ob.prop)
                return (v is None, v)

            rows.sort(key=sort_key, reverse=ob.descending)
        if q.limit is not None:
            rows = rows[: q.limit]
        return ResultSet(columns, rows)

    # -- entity alignment ---------------------------------------------------

    @staticmethod
    def _identity(node: Node, idp: str | None) -> str:
        if idp and idp in node.properties:
            return f"{idp}={node.properties[idp]}"
        if "name" in node.properties:
            return f"name={node.properties['name']}"
        return f"id={node.id}"

    @classmethod
    def _same_entity(cls, a: Node, b: Node, idp: str) -> bool:
        if a.id == b.id:
            return True
        return cls._identity(a, idp) == cls._identity(b, idp)

    @staticmethod
    def _merge_node(a: Node | None, b: Node) -> Node:
        if a is None:
            return b
        labels = list(a.labels) + [lbl for lbl in b.labels if lbl not in a.labels]
        props = {**b.properties, **a.properties}
        src = ",".join(sorted({*(a.source or "").split(","), *(b.source or "").split(",")} - {""}))
        return Node(id=a.id, labels=labels, properties=props, source=src)

    @classmethod
    def _row_key(cls, row: dict[str, Any]) -> str:
        return json.dumps(
            {k: (cls._identity(v, None) if isinstance(v, Node) else v) for k, v in row.items()},
            sort_keys=True,
            default=str,
        )
