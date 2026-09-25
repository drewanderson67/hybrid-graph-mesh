"""Rule-based natural language -> Query IR.

The grammar is deliberately small and schema-driven: a *subject* clause picks
the answer variable, and any number of *qualifier* clauses attach edges and
filters to it. Because everything flows through the IR, the same sentence can
be rendered as Cypher or SPARQL by the mesh.

    "friends of Alice who work at Acme and are older than 30"
      subject:    friends of Alice        -> (Alice)-[:KNOWS]-(x)
      qualifier:  work at Acme            -> (x)-[:WORKS_AT]->(:Organization {name: Acme})
      qualifier:  older than 30           -> x.age > 30
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..ir import Direction, EdgePattern, Filter, NodePattern, Op, OrderBy, Projection, Query
from .vocabulary import Vocabulary

FILLERS = {
    "find",
    "list",
    "show",
    "get",
    "give",
    "me",
    "return",
    "all",
    "the",
    "every",
    "each",
    "please",
    "display",
    "fetch",
    "retrieve",
    "a",
    "an",
    "can",
    "you",
    "tell",
    "i",
    "want",
    "need",
    "search",
    "for",
    "some",
    "any",
    "us",
    "there",
    "exist",
}
CONNECTIVES = {
    "who",
    "that",
    "which",
    "and",
    "with",
    "whose",
    "where",
    "having",
    "have",
    "has",
    "are",
    "is",
    ",",
    "also",
    "in",
    "from",
    "there",
    "exist",
    "exists",
    "do",
    "does",
    "we",
}
COUNT_STARTS = [["how", "many"], ["count"], ["number", "of"], ["count", "of"], ["how", "much"]]
OBJ_DETERMINERS = {"a", "an", "the", "some"}

_COMPARATORS: list[tuple[list[str], Op]] = [
    (["greater", "than", "or", "equal", "to"], Op.GE),
    (["less", "than", "or", "equal", "to"], Op.LE),
    (["greater", "than"], Op.GT),
    (["more", "than"], Op.GT),
    (["larger", "than"], Op.GT),
    (["bigger", "than"], Op.GT),
    (["higher", "than"], Op.GT),
    (["over"], Op.GT),
    (["above"], Op.GT),
    (["after"], Op.GT),
    (["since"], Op.GE),
    (["before"], Op.LT),
    (["until"], Op.LE),
    (["exceeding"], Op.GT),
    (["at", "least"], Op.GE),
    (["minimum"], Op.GE),
    (["less", "than"], Op.LT),
    (["fewer", "than"], Op.LT),
    (["smaller", "than"], Op.LT),
    (["lower", "than"], Op.LT),
    (["under"], Op.LT),
    (["below"], Op.LT),
    (["at", "most"], Op.LE),
    (["maximum"], Op.LE),
    (["not", "equal", "to"], Op.NE),
    (["is", "not"], Op.NE),
    (["isn't"], Op.NE),
    (["!="], Op.NE),
    (["<>"], Op.NE),
    (["equal", "to"], Op.EQ),
    (["equals"], Op.EQ),
    (["is"], Op.EQ),
    (["of"], Op.EQ),
    (["="], Op.EQ),
    (["=="], Op.EQ),
    ([">="], Op.GE),
    (["<="], Op.LE),
    ([">"], Op.GT),
    (["<"], Op.LT),
    (["containing"], Op.CONTAINS),
    (["contains"], Op.CONTAINS),
    (["like"], Op.CONTAINS),
    (["including"], Op.CONTAINS),
    (["starting", "with"], Op.STARTS_WITH),
    (["starts", "with"], Op.STARTS_WITH),
    (["beginning", "with"], Op.STARTS_WITH),
]

# "older than 30" -> age > 30 ; extensible via Translator(property_comparators=...)
DEFAULT_PROPERTY_COMPARATORS: dict[str, tuple[str, Op]] = {
    "older than": ("age", Op.GT),
    "younger than": ("age", Op.LT),
    "aged": ("age", Op.EQ),
}


class TranslationError(ValueError):
    pass


@dataclass
class Translation:
    query: Query
    explanation: list[str] = field(default_factory=list)
    source: str = "rules"

    def to_dict(self) -> dict[str, Any]:
        return {"query": self.query.to_dict(), "explanation": self.explanation, "source": self.source}


@dataclass
class _Token:
    text: str
    lower: str
    quoted: bool = False

    @property
    def capitalized(self) -> bool:
        return self.text[:1].isupper() and not self.quoted


def tokenize(text: str) -> list[_Token]:
    out: list[_Token] = []
    for m in re.finditer(r'"([^"]*)"|\'([^\']*)\'|(<=|>=|!=|<>|==|[<>=])|([A-Za-z0-9_@.\-\']+)|(,)', text):
        if m.group(1) is not None or m.group(2) is not None:
            val = m.group(1) if m.group(1) is not None else m.group(2)
            out.append(_Token(val, val.lower(), quoted=True))
        else:
            raw = m.group(0)
            out.append(_Token(raw, raw.lower()))
    return out


class _Cursor:
    def __init__(self, tokens: list[_Token]):
        self.toks = tokens
        self.i = 0

    @property
    def lowers(self) -> list[str]:
        return [t.lower for t in self.toks]

    def done(self) -> bool:
        return self.i >= len(self.toks)

    def peek(self, n: int = 0) -> _Token | None:
        j = self.i + n
        return self.toks[j] if j < len(self.toks) else None

    def starts_with(self, words: list[str]) -> bool:
        return self.lowers[self.i : self.i + len(words)] == words

    def take(self, n: int = 1) -> list[_Token]:
        toks = self.toks[self.i : self.i + n]
        self.i += n
        return toks

    def remaining_text(self) -> str:
        return " ".join(t.text for t in self.toks[self.i :])


class RuleBasedTranslator:
    def __init__(
        self,
        vocab: Vocabulary,
        property_comparators: dict[str, tuple[str, Op]] | None = None,
        default_limit: int | None = None,
    ):
        self.vocab = vocab
        self.prop_cmp = dict(DEFAULT_PROPERTY_COMPARATORS)
        self.prop_cmp.update(property_comparators or {})
        self.default_limit = default_limit

    # -- public --------------------------------------------------------------

    def translate(self, text: str) -> Translation:
        cur = _Cursor(tokenize(text))
        q = Query()
        notes: list[str] = []
        self._var_counter = 0

        self._skip_fillers(cur)
        count = self._take_count(cur)
        self._skip_fillers(cur)
        projected_prop = self._take_projection(cur)
        self._skip_fillers(cur)

        # trailing modifiers are parsed first so the clause loop never sees them
        order_by, limit = self._take_trailers(cur)

        answer = self._parse_subject(cur, q, notes)
        while not cur.done():
            if not self._parse_qualifier(cur, q, answer, notes):
                raise TranslationError(
                    f"could not understand '{cur.remaining_text()}'. Known labels: "
                    f"{sorted(set(self.vocab.labels.values()))}; relationships: "
                    f"{sorted(set(self.vocab.relationships.values()))}; properties: "
                    f"{sorted(set(self.vocab.properties.values()))}"
                )

        if count:
            q.aggregate = "count"
            q.returns = [Projection(answer)]
            notes.append(f"count distinct {answer}")
        elif projected_prop:
            q.returns = [Projection(answer, projected_prop)]
            notes.append(f"return {answer}.{projected_prop}")
        else:
            q.returns = [Projection(answer)]
            notes.append(f"return {answer}")
        if order_by:
            q.order_by = OrderBy(answer, order_by[0], order_by[1])
        q.limit = limit if limit is not None else self.default_limit
        return Translation(q, notes)

    # -- helpers ---------------------------------------------------------------

    def _new_var(self, hint: str = "n") -> str:
        self._var_counter += 1
        return f"{hint[:1].lower()}{self._var_counter}"

    def _skip_fillers(self, cur: _Cursor) -> None:
        while not cur.done() and cur.peek().lower in FILLERS and not cur.peek().quoted:
            cur.take()

    def _take_count(self, cur: _Cursor) -> bool:
        for words in COUNT_STARTS:
            if cur.starts_with(words):
                cur.take(len(words))
                return True
        return False

    def _take_projection(self, cur: _Cursor) -> str | None:
        """'names of people ...' -> project the `name` property."""
        tok = cur.peek()
        if tok is None or cur.peek(1) is None or cur.peek(1).lower != "of":
            return None
        word = tok.lower
        candidates = [
            word,
            word[:-1] if word.endswith("s") else word,
            word[:-3] + "y" if word.endswith("ies") else word,
        ]
        for c in candidates:
            if c in self.vocab.properties:
                cur.take(2)
                return self.vocab.properties[c]
        return None

    def _take_trailers(self, cur: _Cursor) -> tuple[tuple[str, bool] | None, int | None]:
        lowers = cur.lowers
        order_by: tuple[str, bool] | None = None
        limit: int | None = None
        # "... sorted by age descending" / "ordered by name"
        for start in range(len(lowers)):
            if lowers[start : start + 2] in (["sorted", "by"], ["ordered", "by"], ["order", "by"], ["sort", "by"]):
                m = Vocabulary.match(self.vocab.properties, lowers, start + 2)
                if m:
                    desc = lowers[start + 2 + m[1] : start + 3 + m[1]] in (["descending"], ["desc"])
                    order_by = (m[0], desc)
                    end = (
                        start
                        + 2
                        + m[1]
                        + (
                            1
                            if lowers[start + 2 + m[1] : start + 3 + m[1]]
                            in (["descending"], ["desc"], ["ascending"], ["asc"])
                            else 0
                        )
                    )
                    del cur.toks[start:end]
                    break
        lowers = cur.lowers
        for start in range(len(lowers)):
            if lowers[start] in ("top", "first", "limit") and start + 1 < len(lowers) and lowers[start + 1].isdigit():
                limit = int(lowers[start + 1])
                del cur.toks[start : start + 2]
                break
        return order_by, limit

    def _parse_subject(self, cur: _Cursor, q: Query, notes: list[str]) -> str:
        if cur.done():
            raise TranslationError("empty question")
        lowers = cur.lowers
        i = cur.i

        # "where does Alice work" / "who does Alice know"
        if (
            lowers[i] in ("who", "what", "where", "whom")
            and i + 1 < len(lowers)
            and lowers[i + 1] in ("does", "did", "do")
        ):
            cur.take(2)
            name = self._take_name(cur)
            if not name:
                raise TranslationError("expected a name after 'does'")
            rel = self._match_rel(cur)
            if not rel:
                raise TranslationError(f"expected a relationship after '{name}'")
            src = self._add_node(q, None, {"name": name})
            answer = self._add_node(q, None, {})
            self._add_edge(q, src, answer, rel)
            notes.append(f"({name})-[:{rel}]->({answer}) ; answer = {answer}")
            return answer

        # "who works at Acme" / "which companies ..." / "what people ..."
        if lowers[i] in ("who", "what", "which", "whom"):
            cur.take()
            self._skip_fillers(cur)
            label = self._match_label(cur)
            answer = self._add_node(q, label, {})
            notes.append(f"answer = ({answer}{':' + label if label else ''})")
            return answer

        # "friends of Alice" / "employees of Acme"  (inverse phrases first: they are more specific)
        inv = Vocabulary.match(self.vocab.inverse_relationships, lowers, i)
        if inv:
            cur.take(inv[1])
            target_label, target_name = self._parse_object(cur)
            target = self._add_node(q, target_label, {"name": target_name} if target_name else {})
            answer = self._add_node(q, None, {})
            self._add_edge(q, answer, target, inv[0])
            notes.append(f"({answer})-[:{inv[0]}]->({target}) ; answer = {answer}")
            return answer
        rel = Vocabulary.match(self.vocab.relationships, lowers, i)
        if rel and lowers[i : i + rel[1]][-1] == "of" or (rel and cur.peek(rel[1]) and cur.peek(rel[1]).lower == "of"):
            cur.take(rel[1])
            if cur.peek() and cur.peek().lower == "of":
                cur.take()
            src_label, src_name = self._parse_object(cur)
            src = self._add_node(q, src_label, {"name": src_name} if src_name else {})
            answer = self._add_node(q, None, {})
            self._add_edge(q, src, answer, rel[0])
            notes.append(f"({src})-[:{rel[0]}]->({answer}) ; answer = {answer}")
            return answer

        # "<Label> [named X]" or a bare name
        label = self._match_label(cur)
        if label:
            props: dict[str, Any] = {}
            if cur.peek() and cur.peek().lower in ("named", "called"):
                cur.take()
                name = self._take_name(cur)
                if name:
                    props["name"] = name
            else:
                name = self._take_name(cur, require_capital=True)
                if name:
                    props["name"] = name
            answer = self._add_node(q, label, props)
            notes.append(f"answer = ({answer}:{label}{' ' + str(props) if props else ''})")
            return answer

        name = self._take_name(cur)
        if name:
            answer = self._add_node(q, None, {"name": name})
            notes.append(f"answer = ({answer} {{name: {name!r}}})")
            return answer
        raise TranslationError(f"could not find a subject in '{cur.remaining_text()}'")

    def _parse_qualifier(self, cur: _Cursor, q: Query, answer: str, notes: list[str]) -> bool:
        progressed = False
        while not cur.done() and cur.peek().lower in CONNECTIVES and not cur.peek().quoted:
            # "in"/"from" are only connectives when followed by a relationship phrase
            if cur.peek().lower in ("in", "from") and not self._match_rel(_Cursor(cur.toks[cur.i + 1 :])):
                break
            cur.take()
            progressed = True
        if cur.done():
            return True
        lowers = cur.lowers
        i = cur.i

        if lowers[i] in ("named", "called"):
            cur.take()
            name = self._take_name(cur)
            if not name:
                return False
            q.node(answer).props["name"] = name
            notes.append(f"{answer}.name = {name!r}")
            return True

        # property comparator idioms: "older than 30"
        for phrase, (prop, op) in self.prop_cmp.items():
            words = phrase.split()
            if lowers[i : i + len(words)] == words:
                cur.take(len(words))
                value = self._take_value(cur)
                if value is None:
                    return False
                q.filters.append(Filter(answer, prop, op, value))
                notes.append(f"{answer}.{prop} {op.value} {value!r}")
                return True

        inv = Vocabulary.match(self.vocab.inverse_relationships, lowers, i)
        if inv:
            cur.take(inv[1])
            lbl, name = self._parse_object(cur)
            other = self._add_node(q, lbl, {"name": name} if name else {})
            self._add_edge(q, other, answer, inv[0])
            notes.append(f"({other})-[:{inv[0]}]->({answer})")
            return True

        rel = self._match_rel(cur)
        if rel:
            if cur.peek() and cur.peek().lower in ("of", "to", "for", "by"):
                cur.take()
            lbl, name = self._parse_object(cur)
            if lbl is None and name is None:
                return False
            other = self._add_node(q, lbl, {"name": name} if name else {})
            self._add_edge(q, answer, other, rel)
            notes.append(f"({answer})-[:{rel}]->({other})")
            return True

        prop = Vocabulary.match(self.vocab.properties, lowers, i)
        if prop:
            cur.take(prop[1])
            op = self._take_comparator(cur) or Op.EQ
            value = self._take_value(cur)
            if value is None:
                return False
            q.filters.append(Filter(answer, prop[0], op, value))
            notes.append(f"{answer}.{prop[0]} {op.value} {value!r}")
            return True

        return progressed and cur.done()

    def _parse_object(self, cur: _Cursor) -> tuple[str | None, str | None]:
        while cur.peek() and cur.peek().lower in OBJ_DETERMINERS and not cur.peek().quoted:
            cur.take()
        label = self._match_label(cur)
        if cur.peek() and cur.peek().lower in ("named", "called"):
            cur.take()
        name = self._take_name(cur, require_capital=label is not None)
        return label, name

    def _match_label(self, cur: _Cursor) -> str | None:
        m = Vocabulary.match(self.vocab.labels, cur.lowers, cur.i)
        if m:
            cur.take(m[1])
            return m[0]
        return None

    def _match_rel(self, cur: _Cursor) -> str | None:
        m = Vocabulary.match(self.vocab.relationships, cur.lowers, cur.i)
        if m:
            cur.take(m[1])
            return m[0]
        return None

    def _is_vocab(self, cur: _Cursor) -> bool:
        lowers = cur.lowers
        return (
            any(
                Vocabulary.match(t, lowers, cur.i)
                for t in (
                    self.vocab.labels,
                    self.vocab.relationships,
                    self.vocab.properties,
                    self.vocab.inverse_relationships,
                )
            )
            or lowers[cur.i] in CONNECTIVES
            or lowers[cur.i] in FILLERS
            or any(lowers[cur.i : cur.i + len(p.split())] == p.split() for p in self.prop_cmp)
        )

    def _take_name(self, cur: _Cursor, require_capital: bool = False) -> str | None:
        parts: list[str] = []
        while not cur.done():
            tok = cur.peek()
            if tok.quoted:
                parts.append(tok.text)
                cur.take()
                continue
            if self._is_vocab(cur) or tok.lower == "of":
                break
            if require_capital and not tok.capitalized and not tok.text[:1].isdigit():
                break
            if parts and not tok.capitalized and require_capital:
                break
            parts.append(tok.text)
            cur.take()
            if not tok.capitalized and not require_capital and parts:
                # lower-case single-word names ("alice") stop after one token
                break
        return " ".join(parts) if parts else None

    def _take_comparator(self, cur: _Cursor) -> Op | None:
        for words, op in _COMPARATORS:
            if cur.starts_with(words):
                cur.take(len(words))
                return op
        return None

    def _take_value(self, cur: _Cursor) -> Any:
        tok = cur.peek()
        if tok is None:
            return None
        if tok.quoted:
            cur.take()
            return tok.text
        text = tok.text
        if re.fullmatch(r"-?\d+", text):
            cur.take()
            return int(text)
        if re.fullmatch(r"-?\d+\.\d+", text):
            cur.take()
            return float(text)
        if tok.lower in ("true", "false"):
            cur.take()
            return tok.lower == "true"
        return self._take_name(cur)

    def _add_node(self, q: Query, label: str | None, props: dict[str, Any]) -> str:
        var = self._new_var(label or "n")
        q.nodes.append(NodePattern(var, [label] if label else [], props))
        return var

    def _add_edge(self, q: Query, src: str, dst: str, rel: str) -> None:
        direction = Direction.ANY if rel in self.vocab.symmetric else Direction.OUT
        q.edges.append(EdgePattern(src, dst, [rel], direction))


class LLMTranslator:
    """Delegates to any callable(prompt) -> str that returns Query IR as JSON.

    Works with any LLM SDK; the prompt embeds the mesh schema so the model can
    only use labels/relationships that actually exist.
    """

    def __init__(self, complete: Callable[[str], str], schema: dict[str, Any]):
        self.complete = complete
        self.schema = schema

    def translate(self, text: str) -> Translation:
        import json

        prompt = (
            "Translate the question into a graph query IR as JSON with keys "
            "nodes[{var,labels,props}], edges[{src,dst,types,direction}], "
            "filters[{var,prop,op,value}], returns[{var,prop}], aggregate, limit.\n"
            f"Schema: {json.dumps(self.schema)}\nQuestion: {text}\nJSON:"
        )
        raw = self.complete(prompt)
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start : end + 1])
        return Translation(Query.from_dict(data), ["translated by LLM"], source="llm")


class HybridTranslator:
    """Try the deterministic grammar first, fall back to an LLM when it fails."""

    def __init__(self, rules: RuleBasedTranslator, fallback: LLMTranslator | None = None):
        self.rules = rules
        self.fallback = fallback

    def translate(self, text: str) -> Translation:
        try:
            return self.rules.translate(text)
        except TranslationError:
            if self.fallback is None:
                raise
            return self.fallback.translate(text)
