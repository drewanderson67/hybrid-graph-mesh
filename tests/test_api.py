from fastapi.testclient import TestClient

from hybrid_graph_mesh.api import create_app


def test_api_roundtrip(graph):
    client = TestClient(create_app(graph))

    assert client.get("/").status_code == 200
    assert "Person" in client.get("/schema").json()["labels"]

    r = client.post("/ask", json={"question": "friends of Alice who work at Acme"})
    assert r.status_code == 200
    body = r.json()
    assert {s["language"] for s in body["plan"]} == {"sparql", "cypher"}
    assert sorted(row["n2"]["properties"]["name"] for row in body["rows"]) == ["Bob", "Erin"]

    r = client.post("/translate", json={"question": "who works at Acme"})
    assert "orgs-rdf" in r.json()["compiled"]

    r = client.post("/query", json={"query": body["translation"]["query"]})
    assert len(r.json()["rows"]) == 2

    r = client.post(
        "/sparql",
        json={"query": "SELECT ?n WHERE { ?s <http://schema.org/worksFor> ?o . ?s <http://schema.org/name> ?n }"},
    )
    assert len(r.json()["rows"]) == 5

    assert client.post("/ask", json={"question": "juggling flamingos"}).status_code == 422
    assert client.post("/cypher", json={"query": "MATCH (n) RETURN n"}).status_code == 501
