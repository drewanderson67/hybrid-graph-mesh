"""Query IR -> SPARQL, guided by a SchemaMapping."""

from __future__ import annotations

import json

from ..ir import Direction, Op, Query, Scalar
from ..mapping import RDF_TYPE, SchemaMapping

_OP_MAP = {Op.EQ: "=", Op.NE: "!=", Op.LT: "<", Op.LE: "<=", Op.GT: ">", Op.GE: ">="}


def literal(value: Scalar) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(value)


def prop_var(var: str, prop: str) -> str:
    return f"?{var}__{prop}"


class SparqlCompiler:
    def __init__(self, mapping: SchemaMapping):
        self.m = mapping

    def compile(self, q: Query) -> str:
        where: list[str] = []
        bound_props: set[tuple[str, str]] = set()

        for n in q.nodes:
            for lbl in n.labels:
                where.append(f"?{n.var} <{RDF_TYPE}> <{self.m.label_iri(lbl)}> .")
            for k, v in n.props.items():
                where.append(f"?{n.var} <{self.m.property_iri(k)}> {literal(v)} .")

        for e in q.edges:
            preds = [self.m.relationship_iri(t) for t in e.types]
            src, dst = e.src, e.dst
            if any(t in self.m.inverse_relationships for t in e.types):
                src, dst = dst, src
            if e.direction == Direction.IN:
                src, dst = dst, src
            if not preds:
                where.append(f"?{src} ?{e.var or f'{src}_{dst}_p'} ?{dst} .")
            elif len(preds) == 1 and e.direction != Direction.ANY:
                where.append(f"?{src} <{preds[0]}> ?{dst} .")
            else:
                path = "|".join(f"<{p}>" for p in preds)
                if e.direction == Direction.ANY:
                    path = f"({path})|^({path})"
                where.append(f"?{src} {path} ?{dst} .")

        def bind(var: str, prop: str, optional: bool) -> None:
            if (var, prop) in bound_props:
                return
            bound_props.add((var, prop))
            triple = f"?{var} <{self.m.property_iri(prop)}> {prop_var(var, prop)} ."
            where.append(f"OPTIONAL {{ {triple} }}" if optional else triple)

        filters: list[str] = []
        for f in q.filters:
            bind(f.var, f.prop, optional=False)
            pv = prop_var(f.var, f.prop)
            if f.op == Op.CONTAINS:
                filters.append(f"CONTAINS(LCASE(STR({pv})), LCASE({literal(f.value)}))")
            elif f.op == Op.STARTS_WITH:
                filters.append(f"STRSTARTS(LCASE(STR({pv})), LCASE({literal(f.value)}))")
            else:
                filters.append(f"{pv} {_OP_MAP[f.op]} {literal(f.value)}")
        for f in filters:
            where.append(f"FILTER({f})")

        for p in q.returns:
            if p.prop:
                bind(p.var, p.prop, optional=True)
        if q.order_by:
            bind(q.order_by.var, q.order_by.prop, optional=True)

        if q.aggregate == "count":
            target = q.returns[0].var if q.returns else q.nodes[0].var
            select = f"SELECT (COUNT(DISTINCT ?{target}) AS ?count)"
        else:
            items = []
            for p in q.returns:
                if p.prop:
                    items.append(f"({prop_var(p.var, p.prop)} AS ?{self._safe(p.name)})")
                else:
                    items.append(f"?{p.var}")
            if not items:
                items = [f"?{v}" for v in q.variables()]
            select = ("SELECT DISTINCT " if q.distinct else "SELECT ") + " ".join(items)

        body = "\n  ".join(where)
        out = f"{select}\nWHERE {{\n  {body}\n}}"
        if q.order_by and q.aggregate is None:
            pv = prop_var(q.order_by.var, q.order_by.prop)
            out += f"\nORDER BY {'DESC' if q.order_by.descending else 'ASC'}({pv})"
        if q.limit is not None:
            out += f"\nLIMIT {q.limit}"
        return out

    def compile_hydrate(self, iris: list[str]) -> str:
        values = " ".join(f"<{i}>" for i in iris)
        return (
            "SELECT ?s ?p ?o WHERE {\n"
            f"  VALUES ?s {{ {values} }}\n"
            "  ?s ?p ?o .\n"
            "  FILTER(isLiteral(?o) || ?p = <" + RDF_TYPE + ">)\n"
            "}"
        )

    @staticmethod
    def _safe(name: str) -> str:
        return name.replace(".", "_")


def compile_sparql(q: Query, mapping: SchemaMapping) -> str:
    return SparqlCompiler(mapping).compile(q)
