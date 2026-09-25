import pytest

from hybrid_graph_mesh.demo import build_demo


@pytest.fixture(scope="session")
def graph():
    return build_demo()


def names(rs, col=None):
    col = col or rs.columns[0]
    out = []
    for row in rs.rows:
        v = row[col]
        out.append(v.properties.get("name") if hasattr(v, "properties") else v)
    return sorted(out, key=str)
