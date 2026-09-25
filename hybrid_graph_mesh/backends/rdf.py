"""RDF backend: local rdflib graph or remote SPARQL endpoint."""

from __future__ import annotations

from typing import Any

from rdflib import Graph, Literal
from rdflib.plugins.stores.sparqlstore import SPARQLStore

from ..compilers.sparql import SparqlCompiler
from ..ir import Query
from ..mapping import RDF_TYPE, SchemaMapping
from ..model import Node, ResultSet
from .base import Backend


def _py(term: Any) -> Any:
    if isinstance(term, Literal):
        v = term.toPython()
        return v if isinstance(v, (str, int, float, bool)) else str(v)
    return str(term) if term is not None else None


class RdfBackend(Backend):
    kind = "rdf"

    def __init__(self, name: str, mapping: SchemaMapping, graph: Graph | None = None, endpoint: str | None = None):
        if graph is None and endpoint is None:
            raise ValueError("provide a graph or an endpoint")
        self.name = name
        self.mapping = mapping
        self.graph = graph if graph is not None else Graph(store=SPARQLStore(endpoint))
        self.compiler = SparqlCompiler(mapping)

    @classmethod
    def from_file(cls, name: str, mapping: SchemaMapping, path: str, fmt: str | None = None) -> RdfBackend:
        g = Graph()
        g.parse(path, format=fmt)
        return cls(name, mapping, graph=g)

    def labels(self) -> set[str]:
        return self.mapping.known_labels()

    def relationships(self) -> set[str]:
        return self.mapping.known_relationships()

    def properties(self) -> set[str]:
        return self.mapping.known_properties()

    def identity_property(self) -> str:
        return self.mapping.identity_property

    def compile(self, q: Query) -> str:
        return self.compiler.compile(q)

    def execute(self, q: Query) -> ResultSet:
        sparql = self.compile(q)
        res = self.graph.query(sparql)
        vars_ = [str(v) for v in res.vars]
        if q.aggregate == "count":
            rows = [{"count": _py(r[0])} for r in res]
            return ResultSet(["count"], rows)

        columns = [p.name for p in q.returns] or q.variables()
        node_cols = {p.name for p in q.returns if not p.prop} if q.returns else set(columns)
        raw_rows = [dict(zip(vars_, (_py(t) for t in r), strict=False)) for r in res]
        # SPARQL variable names cannot contain '.', so map by position.
        rows = [dict(zip(columns, r.values(), strict=False)) for r in raw_rows]

        iris = sorted({row[c] for row in rows for c in node_cols if row.get(c)})
        nodes = self._hydrate(iris)
        for row in rows:
            for c in node_cols:
                if row.get(c) in nodes:
                    row[c] = nodes[row[c]]
        return ResultSet(columns, rows)

    def execute_native(self, text: str) -> ResultSet:
        res = self.graph.query(text)
        vars_ = [str(v) for v in res.vars]
        rows = [dict(zip(vars_, (_py(t) for t in r), strict=False)) for r in res]
        return ResultSet(vars_, rows)

    def _hydrate(self, iris: list[str]) -> dict[str, Node]:
        if not iris:
            return {}
        nodes = {i: Node(id=i, source=self.name) for i in iris}
        for chunk_start in range(0, len(iris), 200):
            chunk = iris[chunk_start : chunk_start + 200]
            for s, p, o in self.graph.query(self.compiler.compile_hydrate(chunk)):
                node = nodes[str(s)]
                if str(p) == RDF_TYPE:
                    lbl = self.mapping.label_for_iri(str(o))
                    if lbl:
                        node.labels.append(lbl)
                else:
                    key = self.mapping.property_for_iri(str(p)) or str(p)
                    node.properties[key] = _py(o)
        return nodes

    def describe(self, iri: str) -> Node | None:
        return self._hydrate([iri]).get(iri)
