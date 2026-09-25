"""Surface forms for labels, relationship types and properties.

Built from the mesh schema plus optional user synonyms, so the NL layer speaks
the vocabulary of whatever graphs are plugged in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_IRREGULAR_PLURALS = {"person": "people", "child": "children", "man": "men", "woman": "women"}


def plural(word: str) -> str:
    w = word.lower()
    if w in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[w]
    if w.endswith("y") and w[-2:-1] not in "aeiou":
        return w[:-1] + "ies"
    if w.endswith(("s", "x", "z", "ch", "sh")):
        return w + "es"
    return w + "s"


def verb_forms(verb: str) -> set[str]:
    v = verb.lower()
    forms = {v}
    if v.endswith("s"):
        base = v[:-1]
        forms.add(base)
    else:
        base = v
        forms.add(v + "s")
    if base.endswith("e"):
        forms.add(base[:-1] + "ing")
        forms.add(base + "d")
    else:
        forms.add(base + "ing")
        forms.add(base + "ed")
    return forms


def split_identifier(name: str) -> list[str]:
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").replace("-", " ")
    return [p.lower() for p in parts.split() if p]


@dataclass
class Vocabulary:
    labels: dict[str, str] = field(default_factory=dict)  # phrase -> Label
    relationships: dict[str, str] = field(default_factory=dict)  # phrase -> REL_TYPE
    properties: dict[str, str] = field(default_factory=dict)  # phrase -> prop
    # phrases that name the *target* side of a relationship, e.g. "employees of" -> WORKS_AT
    inverse_relationships: dict[str, str] = field(default_factory=dict)
    symmetric: set[str] = field(default_factory=set)

    @classmethod
    def build(
        cls,
        labels: set[str],
        relationships: set[str],
        properties: set[str],
        synonyms: dict[str, list[str]] | None = None,
        inverse_synonyms: dict[str, list[str]] | None = None,
        symmetric: set[str] | None = None,
    ) -> Vocabulary:
        v = cls(symmetric=set(symmetric or ()))
        for rel, phrases in (inverse_synonyms or {}).items():
            for p in phrases:
                v.inverse_relationships[p.lower()] = rel
        for lbl in labels:
            words = split_identifier(lbl)
            phrase = " ".join(words)
            v.labels[phrase] = lbl
            v.labels[" ".join(words[:-1] + [plural(words[-1])])] = lbl
        for rel in relationships:
            words = split_identifier(rel)
            if not words:
                continue
            head, tail = words[0], words[1:]
            for form in verb_forms(head):
                v.relationships[" ".join([form] + tail)] = rel
        for prop in properties:
            v.properties[" ".join(split_identifier(prop))] = prop
        for canonical, phrases in (synonyms or {}).items():
            table = v.labels if canonical in labels else v.relationships if canonical in relationships else v.properties
            for p in phrases:
                table[p.lower()] = canonical
        return v

    @staticmethod
    def match(table: dict[str, str], tokens: list[str], i: int) -> tuple[str, int] | None:
        """Longest phrase in `table` starting at tokens[i]; returns (canonical, n_tokens)."""
        best: tuple[str, int] | None = None
        for phrase, canonical in table.items():
            words = phrase.split()
            n = len(words)
            if tokens[i : i + n] == words and (best is None or n > best[1]):
                best = (canonical, n)
        return best
