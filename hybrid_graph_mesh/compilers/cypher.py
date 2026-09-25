"""Query IR -> Cypher."""

from __future__ import annotations

import json

from ..ir import Direction, Op, Query, Scalar

_OP_MAP = {
    Op.EQ: "=",
    Op.NE: "<>",
    Op.LT: "<",
    Op.LE: "<=",
    Op.GT: ">",
    Op.GE: ">=",
    Op.CONTAINS: "CONTAINS",
    Op.STARTS_WITH: "STARTS WITH",
}


def literal(value: Scalar) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(value)


def _node(q: Query, var: str) -> str:
    n = q.node(var)
    labels = "".join(f":`{lbl}`" for lbl in n.labels)
    props = ""
    if n.props:
        props = " {" + ", ".join(f"`{k}`: {literal(v)}" for k, v in n.props.items()) + "}"
    return f"({var}{labels}{props})"


def compile_cypher(q: Query) -> str:
    patterns: list[str] = []
    covered: set[str] = set()
    for e in q.edges:
        types = "|".join(f"`{t}`" for t in e.types)
        rel = f"[{e.var or ''}{':' + types if types else ''}]"
        left, right = (
            ("-", "->") if e.direction == Direction.OUT else ("<-", "-") if e.direction == Direction.IN else ("-", "-")
        )
        patterns.append(f"{_node(q, e.src)}{left}{rel}{right}{_node(q, e.dst)}")
        covered.update({e.src, e.dst})
    for n in q.nodes:
        if n.var not in covered:
            patterns.append(_node(q, n.var))

    parts = ["MATCH " + ", ".join(patterns)]
    if q.filters:
        conds = [f"{f.var}.`{f.prop}` {_OP_MAP[f.op]} {literal(f.value)}" for f in q.filters]
        parts.append("WHERE " + " AND ".join(conds))

    if q.aggregate == "count":
        target = q.returns[0].var if q.returns else q.nodes[0].var
        parts.append(f"RETURN count(DISTINCT {target}) AS count")
    else:
        items = []
        for p in q.returns:
            expr = f"{p.var}.`{p.prop}`" if p.prop else p.var
            items.append(f"{expr} AS `{p.name}`")
        if not items:
            items = [f"{v} AS `{v}`" for v in q.variables()]
        parts.append(("RETURN DISTINCT " if q.distinct else "RETURN ") + ", ".join(items))
        if q.order_by:
            parts.append(f"ORDER BY {q.order_by.var}.`{q.order_by.prop}`" + (" DESC" if q.order_by.descending else ""))
    if q.limit is not None:
        parts.append(f"LIMIT {q.limit}")
    return "\n".join(parts)
