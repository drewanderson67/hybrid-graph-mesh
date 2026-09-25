import pytest

from hybrid_graph_mesh import (
    Direction,
    EdgePattern,
    Filter,
    InMemoryLpgBackend,
    NodePattern,
    Op,
    PlanningError,
    Projection,
    Query,
    VirtualMesh,
)

from .conftest import names


def test_pushdown_to_single_covering_backend(graph):
    q = Query(
        nodes=[NodePattern("p", ["Person"]), NodePattern("o", [], {"name": "Acme"})],
        edges=[EdgePattern("p", "o", ["WORKS_AT"])],
        returns=[Projection("p")],
    )
    plan = graph.mesh.plan(q)
    assert plan.strategy == "pushdown"
    assert [s.backend.name for s in plan.steps] == ["orgs-rdf"]
    assert names(graph.query(q)) == ["Alice", "Bob", "Erin"]


def test_union_merges_entities_across_backends(graph):
    q = Query(nodes=[NodePattern("p", ["Person"])], returns=[Projection("p")])
    rs = graph.query(q)
    assert {s["backend"] for s in rs.plan} == {"orgs-rdf", "social-lpg"}
    alice = next(r["p"] for r in rs.rows if r["p"].properties["name"] == "Alice")
    # properties from both stores are stitched onto one virtual node
    assert alice.properties["age"] == 34 and alice.properties["jobTitle"] == "Engineer"
    assert alice.source == "orgs-rdf,social-lpg"
    assert len(rs.rows) == 5


def test_federated_join(graph):
    q = Query(
        nodes=[
            NodePattern("a", [], {"name": "Alice"}),
            NodePattern("f", ["Person"]),
            NodePattern("o", ["Organization"], {"name": "Acme"}),
        ],
        edges=[EdgePattern("a", "f", ["KNOWS"], Direction.ANY), EdgePattern("f", "o", ["WORKS_AT"])],
        returns=[Projection("f")],
    )
    plan = graph.mesh.plan(q)
    assert plan.strategy == "federated"
    assert set(plan.join_vars) == {"f"}
    assert names(graph.query(q)) == ["Bob", "Erin"]


def test_federated_filter_on_property_from_other_store(graph):
    q = Query(
        nodes=[NodePattern("p", ["Person"]), NodePattern("o", [], {"name": "Acme"})],
        edges=[EdgePattern("p", "o", ["WORKS_AT"])],
        filters=[Filter("p", "age", Op.GT, 30)],
        returns=[Projection("p", "name")],
    )
    rs = graph.query(q)
    assert rs.plan[0]["strategy"] == "federated"
    assert names(rs, "p.name") == ["Alice", "Erin"]


def test_count_pushdown(graph):
    q = Query(nodes=[NodePattern("o", ["Organization"])], aggregate="count", returns=[Projection("o")])
    assert graph.query(q).rows == [{"count": 3}]


def test_planning_error_for_unknown_property(graph):
    q = Query(nodes=[NodePattern("p", ["Person"])], filters=[Filter("p", "shoeSize", Op.GT, 9)])
    with pytest.raises(PlanningError):
        graph.mesh.plan(q)


def test_lpg_engine_directions():
    b = InMemoryLpgBackend("g")
    b.add_node("1", ["Person"], {"name": "A"})
    b.add_node("2", ["Person"], {"name": "B"})
    b.add_edge("1", "2", "KNOWS")
    mesh = VirtualMesh([b])

    def run(direction):
        q = Query(
            nodes=[NodePattern("x", [], {"name": "B"}), NodePattern("y")],
            edges=[EdgePattern("x", "y", ["KNOWS"], direction)],
            returns=[Projection("y", "name")],
        )
        return names(mesh.execute(q), "y.name")

    assert run(Direction.OUT) == []
    assert run(Direction.IN) == ["A"]
    assert run(Direction.ANY) == ["A"]
