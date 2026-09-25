"""Schema mapping between the unified LPG-style vocabulary and RDF terms.

The mesh speaks in labels / relationship types / property keys. Each RDF
backend supplies a `SchemaMapping` that tells the SPARQL compiler which IRIs
those names correspond to. LPG backends use the names directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


@dataclass
class SchemaMapping:
    namespaces: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    properties: dict[str, str] = field(default_factory=dict)
    relationships: dict[str, str] = field(default_factory=dict)
    # property used to align the same real-world entity across backends
    identity_property: str = "name"
    # relationship types whose RDF predicate points from dst to src
    inverse_relationships: set[str] = field(default_factory=set)

    def expand(self, curie_or_iri: str) -> str:
        if ":" in curie_or_iri and not curie_or_iri.startswith(("http://", "https://", "urn:")):
            prefix, local = curie_or_iri.split(":", 1)
            if prefix in self.namespaces:
                return self.namespaces[prefix] + local
        return curie_or_iri

    def label_iri(self, label: str) -> str:
        return self.expand(self.labels[label])

    def property_iri(self, prop: str) -> str:
        return self.expand(self.properties[prop])

    def relationship_iri(self, rel: str) -> str:
        return self.expand(self.relationships[rel])

    def label_for_iri(self, iri: str) -> str | None:
        return self._reverse(self.labels).get(iri)

    def property_for_iri(self, iri: str) -> str | None:
        return self._reverse(self.properties).get(iri)

    def relationship_for_iri(self, iri: str) -> str | None:
        return self._reverse(self.relationships).get(iri)

    def _reverse(self, table: dict[str, str]) -> dict[str, str]:
        return {self.expand(v): k for k, v in table.items()}

    def known_labels(self) -> set[str]:
        return set(self.labels)

    def known_relationships(self) -> set[str]:
        return set(self.relationships)

    def known_properties(self) -> set[str]:
        return set(self.properties)

    def sparql_prefixes(self) -> str:
        return "\n".join(f"PREFIX {p}: <{iri}>" for p, iri in self.namespaces.items())
