"""Build a HybridGraph from a JSON config file.

{
  "backends": [
    {"name": "kg", "type": "rdf", "endpoint": "https://dbpedia.org/sparql",
     "mapping": {"namespaces": {...}, "labels": {...}, "properties": {...}, "relationships": {...}}},
    {"name": "kg-file", "type": "rdf", "file": "data.ttl", "format": "turtle", "mapping": {...}},
    {"name": "graph", "type": "neo4j", "uri": "bolt://localhost:7687", "user": "neo4j", "password": "..."},
    {"name": "local", "type": "lpg-json", "file": "graph.json"}
  ],
  "synonyms": {"Organization": ["company", "companies"]},
  "inverse_synonyms": {"WORKS_AT": ["employees of"]},
  "symmetric": ["KNOWS"]
}
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import HybridGraph
from .backends import Backend, InMemoryLpgBackend, Neo4jBackend, RdfBackend
from .mapping import SchemaMapping


def _mapping(d: dict[str, Any]) -> SchemaMapping:
    return SchemaMapping(
        namespaces=d.get("namespaces", {}),
        labels=d.get("labels", {}),
        properties=d.get("properties", {}),
        relationships=d.get("relationships", {}),
        identity_property=d.get("identity_property", "name"),
        inverse_relationships=set(d.get("inverse_relationships", [])),
    )


def _expand_env(value: str) -> str:
    return os.path.expandvars(value)


def build_backend(spec: dict[str, Any], base: Path) -> Backend:
    kind = spec["type"]
    name = spec["name"]
    if kind == "rdf":
        mapping = _mapping(spec["mapping"])
        if "endpoint" in spec:
            return RdfBackend(name, mapping, endpoint=_expand_env(spec["endpoint"]))
        return RdfBackend.from_file(name, mapping, str(base / spec["file"]), spec.get("format"))
    if kind == "lpg-json":
        return InMemoryLpgBackend.from_json(name, str(base / spec["file"]), spec.get("identity_property", "name"))
    if kind == "neo4j":
        auth = None
        if "user" in spec:
            auth = (_expand_env(spec["user"]), _expand_env(spec.get("password", "")))
        return Neo4jBackend(
            name, _expand_env(spec["uri"]), auth, spec.get("database"), spec.get("identity_property", "name")
        )
    raise ValueError(f"unknown backend type {kind!r}")


def load_config(path: str) -> HybridGraph:
    p = Path(path)
    with open(p) as fh:
        cfg = json.load(fh)
    backends = [build_backend(spec, p.parent) for spec in cfg["backends"]]
    return HybridGraph(
        backends,
        synonyms=cfg.get("synonyms"),
        inverse_synonyms=cfg.get("inverse_synonyms"),
        symmetric=set(cfg.get("symmetric", [])),
        default_limit=cfg.get("default_limit", 100),
    )
