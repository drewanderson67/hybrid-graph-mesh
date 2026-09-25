"""Hybrid Graph Mesh: a virtual layer bridging RDF (SPARQL) and LPG (Cypher) graphs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .backends import Backend, InMemoryLpgBackend, Neo4jBackend, RdfBackend
from .compilers import compile_cypher, compile_sparql
from .ir import Direction, EdgePattern, Filter, NodePattern, Op, Projection, Query
from .mapping import SchemaMapping
from .mesh import PlanningError, VirtualMesh
from .model import Node, ResultSet
from .nl import HybridTranslator, LLMTranslator, RuleBasedTranslator, Translation, TranslationError, Vocabulary

__all__ = [
    "Backend",
    "Direction",
    "EdgePattern",
    "Filter",
    "HybridGraph",
    "InMemoryLpgBackend",
    "Neo4jBackend",
    "Node",
    "NodePattern",
    "Op",
    "PlanningError",
    "Projection",
    "Query",
    "RdfBackend",
    "ResultSet",
    "SchemaMapping",
    "Translation",
    "TranslationError",
    "VirtualMesh",
    "Vocabulary",
    "compile_cypher",
    "compile_sparql",
]


@dataclass
class Answer:
    question: str
    translation: Translation
    results: ResultSet

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "translation": self.translation.to_dict(),
            "plan": self.results.plan,
            "columns": self.results.columns,
            "rows": self.results.to_dict()["rows"],
        }


class HybridGraph:
    """Facade: natural language in, federated RDF/LPG results out."""

    def __init__(
        self,
        backends: list[Backend],
        synonyms: dict[str, list[str]] | None = None,
        inverse_synonyms: dict[str, list[str]] | None = None,
        symmetric: set[str] | None = None,
        llm: Callable[[str], str] | None = None,
        default_limit: int | None = 100,
    ):
        self.mesh = VirtualMesh(backends)
        self._synonyms = synonyms or {}
        self._inverse = inverse_synonyms or {}
        self._symmetric = set(symmetric or ())
        self._llm = llm
        self._default_limit = default_limit
        self._translator: HybridTranslator | None = None

    @property
    def translator(self) -> HybridTranslator:
        if self._translator is None:
            vocab = Vocabulary.build(
                self.mesh.labels(),
                self.mesh.relationships(),
                self.mesh.properties(),
                synonyms=self._synonyms,
                inverse_synonyms=self._inverse,
                symmetric=self._symmetric,
            )
            rules = RuleBasedTranslator(vocab, default_limit=self._default_limit)
            fallback = LLMTranslator(self._llm, self.mesh.schema()) if self._llm else None
            self._translator = HybridTranslator(rules, fallback)
        return self._translator

    def translate(self, question: str) -> Translation:
        return self.translator.translate(question)

    def compile(self, question: str) -> dict[str, str]:
        """Show how a question renders in every backend's language."""
        q = self.translate(question).query
        return {step["backend"]: step["query"] for step in self.mesh.plan(q).to_dicts()}

    def ask(self, question: str) -> Answer:
        t = self.translate(question)
        return Answer(question, t, self.mesh.execute(t.query))

    def query(self, q: Query) -> ResultSet:
        return self.mesh.execute(q)

    def sparql(self, text: str, backend: str | None = None) -> ResultSet:
        return self._native(text, "rdf", backend)

    def cypher(self, text: str, backend: str | None = None) -> ResultSet:
        return self._native(text, "lpg", backend)

    def _native(self, text: str, kind: str, name: str | None) -> ResultSet:
        for b in self.mesh.backends:
            if b.kind == kind and (name is None or b.name == name):
                return b.execute_native(text)
        raise LookupError(f"no {kind} backend{' named ' + name if name else ''}")

    def schema(self) -> dict[str, Any]:
        return self.mesh.schema()
