"""The database is the only source of truth: the graph view the server
builds must reflect writes made by any process, with no restart."""

import datetime

from kyo_mcp.database import create_concept, create_link
from kyo_mcp.mcp_server import build_graph


def test_graph_reflects_writes_without_restart():
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for concept_id in ("node-a", "node-b"):
        create_concept(
            {
                "id": concept_id,
                "type": "concept",
                "title": f"Graph Consistency {concept_id}",
                "generated": {"by": "test:test-script", "at": now},
            },
            markdown_body="Test content",
        )

    graph = build_graph()
    assert set(graph.nodes) == {"node-a", "node-b"}
    assert graph.number_of_edges() == 0

    # A write after the first build (as another process would make) shows
    # up in the next build.
    create_link("node-a", "node-b", "references")
    create_link("node-a", "node-b", "derives_from")
    graph = build_graph()
    assert graph.nodes["node-a"]["type"] == "concept"
    relations = {d["type"] for d in graph.get_edge_data("node-a", "node-b").values()}
    assert relations == {"references", "derives_from"}
