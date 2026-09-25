import json

import pytest

from hybrid_graph_mesh import HybridGraph, Op, TranslationError
from hybrid_graph_mesh.demo import INVERSE_SYNONYMS, SYNONYMS, build_demo

from .conftest import names


@pytest.mark.parametrize(
    "question, expected",
    [
        ("find all people", ["Alice", "Bob", "Carol", "Dave", "Erin"]),
        ("who works at Acme", ["Alice", "Bob", "Erin"]),
        ("employees of Acme", ["Alice", "Bob", "Erin"]),
        ("friends of Alice", ["Bob", "Carol", "Erin"]),
        ("who knows Bob", ["Alice", "Dave"]),
        ("people older than 30", ["Alice", "Carol", "Dave", "Erin"]),
        ("people whose job title is Engineer", ["Alice", "Erin"]),
        ("companies founded before 2000", ["Acme", "Initech"]),
        ("companies located in Austin", ["Globex", "Initech"]),
        ("where does Alice work", ["Acme"]),
        ("people skilled in Python", ["Alice", "Carol"]),
        # cross-store questions -> federated joins
        ("friends of Alice who work at Acme", ["Bob", "Erin"]),
        ("employees of Acme older than 30", ["Alice", "Erin"]),
        ("people who know Alice and work at Globex", ["Carol"]),
        ("which people are skilled in SPARQL and work at Acme", ["Alice", "Erin"]),
    ],
)
def test_questions(graph, question, expected):
    assert names(graph.ask(question).results) == expected


def test_projection_and_ordering(graph):
    rs = graph.ask("names of employees of Acme").results
    assert rs.columns == ["n2.name"]
    assert names(rs) == ["Alice", "Bob", "Erin"]

    rs = graph.ask("people sorted by age descending").results
    assert [r["p1"].properties["age"] for r in rs.rows] == [45, 39, 34, 31, 28]

    rs = graph.ask("top 2 people sorted by age").results
    assert [r["p1"].properties["name"] for r in rs.rows] == ["Bob", "Erin"]


def test_count(graph):
    assert graph.ask("how many companies are there").results.rows == [{"count": 3}]
    assert graph.ask("how many people work at Acme").results.rows == [{"count": 3}]


def test_translation_ir(graph):
    t = graph.translate('people whose name contains "ar"')
    f = t.query.filters[0]
    assert (f.prop, f.op, f.value) == ("name", Op.CONTAINS, "ar")
    assert names(graph.query(t.query)) == ["Carol"]


def test_compile_shows_both_dialects(graph):
    compiled = graph.compile("friends of Alice who work at Acme")
    assert "MATCH" in compiled["social-lpg"]
    assert "SELECT" in compiled["orgs-rdf"]


def test_unknown_phrase_raises(graph):
    with pytest.raises(TranslationError):
        graph.translate("people who juggle flamingos")


def test_llm_fallback_receives_schema():
    seen = {}

    def fake_llm(prompt: str) -> str:
        seen["prompt"] = prompt
        return json.dumps({"nodes": [{"var": "p", "labels": ["Person"]}], "returns": [{"var": "p"}], "limit": 2})

    g = build_demo(llm=fake_llm)
    answer = g.ask("people who juggle flamingos")
    assert answer.translation.source == "llm"
    assert "Organization" in seen["prompt"]
    assert len(answer.results.rows) == 2


def test_vocabulary_is_schema_driven():
    g = HybridGraph(build_demo().mesh.backends, synonyms=SYNONYMS, inverse_synonyms=INVERSE_SYNONYMS)
    schema = g.schema()
    assert set(schema["labels"]) == {"City", "Organization", "Person", "Skill"}
    assert set(schema["relationships"]) == {"HAS_SKILL", "KNOWS", "LOCATED_IN", "WORKS_AT"}
