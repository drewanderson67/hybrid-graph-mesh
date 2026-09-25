from hybrid_graph_mesh import (
    Direction,
    EdgePattern,
    Filter,
    NodePattern,
    Op,
    Projection,
    Query,
    SchemaMapping,
    compile_cypher,
    compile_sparql,
)
from hybrid_graph_mesh.demo import ORG_MAPPING


def _query() -> Query:
    return Query(
        nodes=[NodePattern("a", ["Person"], {"name": "Alice"}), NodePattern("b", ["Organization"])],
        edges=[EdgePattern("a", "b", ["WORKS_AT"], Direction.OUT)],
        filters=[Filter("b", "foundingDate", Op.LT, 2000)],
        returns=[Projection("b", "name")],
        limit=5,
    )


def test_cypher():
    text = compile_cypher(_query())
    assert 'MATCH (a:`Person` {`name`: "Alice"})-[:`WORKS_AT`]->(b:`Organization`)' in text
    assert "WHERE b.`foundingDate` < 2000" in text
    assert "RETURN DISTINCT b.`name` AS `b.name`" in text
    assert text.endswith("LIMIT 5")


def test_sparql():
    text = compile_sparql(_query(), ORG_MAPPING)
    assert "?a <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://schema.org/Person> ." in text
    assert '?a <http://schema.org/name> "Alice" .' in text
    assert "?a <http://schema.org/worksFor> ?b ." in text
    assert "FILTER(?b__foundingDate < 2000)" in text
    assert "SELECT DISTINCT (?b__name AS ?b_name)" in text
    assert text.endswith("LIMIT 5")


def test_count_and_any_direction():
    q = Query(
        nodes=[NodePattern("a", [], {"name": "Alice"}), NodePattern("b")],
        edges=[EdgePattern("a", "b", ["KNOWS"], Direction.ANY)],
        aggregate="count",
        returns=[Projection("b")],
    )
    assert "-[:`KNOWS`]-(b)" in compile_cypher(q)
    assert "count(DISTINCT b) AS count" in compile_cypher(q)
    mapping = SchemaMapping(properties={"name": "http://x/name"}, relationships={"KNOWS": "http://x/knows"})
    sparql = compile_sparql(q, mapping)
    assert "(<http://x/knows>)|^(<http://x/knows>)" in sparql
    assert "COUNT(DISTINCT ?b) AS ?count" in sparql


def test_ir_roundtrip():
    q = _query()
    assert Query.from_dict(q.to_dict()) == q
