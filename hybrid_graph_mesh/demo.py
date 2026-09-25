"""Builds the demo mesh: an RDF org chart federated with an LPG social graph.

The two stores overlap only on Person `name`, which is exactly what the mesh
uses to align entities when a question spans both.
"""

from __future__ import annotations

from pathlib import Path

from . import HybridGraph
from .backends import InMemoryLpgBackend, RdfBackend
from .mapping import SchemaMapping

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"

ORG_MAPPING = SchemaMapping(
    namespaces={"schema": "http://schema.org/", "ex": "http://example.org/"},
    labels={"Person": "schema:Person", "Organization": "schema:Organization", "City": "schema:City"},
    properties={"name": "schema:name", "jobTitle": "schema:jobTitle", "foundingDate": "schema:foundingDate"},
    relationships={"WORKS_AT": "schema:worksFor", "LOCATED_IN": "schema:location"},
    identity_property="name",
)

SYNONYMS = {
    "Organization": ["company", "companies", "org", "orgs", "employer", "employers"],
    "Person": ["employee", "employees", "user", "users", "folks"],
    "City": ["cities", "town", "towns", "place", "places"],
    "WORKS_AT": [
        "work at",
        "works at",
        "working at",
        "worked at",
        "work for",
        "works for",
        "employed by",
        "employed at",
        "work",
    ],
    "LOCATED_IN": ["located in", "based in", "headquartered in", "in"],
    "KNOWS": [
        "know",
        "knows",
        "friends of",
        "friend of",
        "friends with",
        "is friends with",
        "are friends with",
        "connected to",
    ],
    "HAS_SKILL": [
        "skilled in",
        "know how to use",
        "can use",
        "has skill",
        "have skill",
        "with skill",
        "knows how to use",
        "proficient in",
    ],
    "jobTitle": ["job title", "title", "job", "role"],
    "foundingDate": ["founded", "founding date", "founding year"],
}

INVERSE_SYNONYMS = {
    "WORKS_AT": ["employees of", "staff of", "people at", "workers at"],
    "LOCATED_IN": ["companies in", "organizations in"],
}


def build_demo(llm=None) -> HybridGraph:
    rdf = RdfBackend.from_file("orgs-rdf", ORG_MAPPING, str(DEMO_DIR / "organizations.ttl"), fmt="turtle")
    lpg = InMemoryLpgBackend.from_json("social-lpg", str(DEMO_DIR / "social.json"))
    return HybridGraph(
        [rdf, lpg],
        synonyms=SYNONYMS,
        inverse_synonyms=INVERSE_SYNONYMS,
        symmetric={"KNOWS"},
        llm=llm,
    )
