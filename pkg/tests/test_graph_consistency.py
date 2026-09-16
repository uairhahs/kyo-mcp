import datetime
import shutil

import pytest
from kyo_mcp.database import create_concept
from kyo_mcp.mcp_server import G, load_graph_data  # Import global state
from kyo_mcp.okf_schema import GeneratedInfo


@pytest.fixture(scope="module")
def setup_db_and_graph(tmp_path_factory):
    # Use a temporary path for a clean test run
    test_dir = tmp_path_factory.mktemp("test_kyo_db")

    # Manually set environment for database module to use the temporary path
    import os

    original_data_dir = os.environ.get("KYO_DATA_DIR")
    os.environ["KYO_DATA_DIR"] = str(test_dir)

    # Ensure the database module picks up the new path
    import kyo_mcp.database as db_mod

    db_mod.DB_PATH = str(test_dir / "test_consistency.db")
    db_mod.conn = None

    yield test_dir

    # Cleanup
    if original_data_dir is not None:
        os.environ["KYO_DATA_DIR"] = original_data_dir
    elif "KYO_DATA_DIR" in os.environ:
        del os.environ["KYO_DATA_DIR"]
    shutil.rmtree(test_dir)


def test_graph_consistency_after_write_and_restart(setup_db_and_graph):
    """
    Tests that the in-memory graph (G) correctly reflects a node
    created in the database, even after a simulated service restart.
    """

    # --- Phase 1: Write to Database (Simulate running service) ---

    # 1. Prepare concept data
    concept_id = "test-consistency-node-001"
    concept_data = {
        "id": concept_id,
        "type": "concept",
        "title": "Graph Consistency Test Node",
        "description": "This node proves graph integrity.",
        "resource": {"uri": "http://test.uri"},
        "tags": ["test", "integrity"],
        "status": "stable",
        "generated": GeneratedInfo(
            by="test:test-script",
            at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        ),
    }

    # 2. Persist the concept to the database

    create_concept(concept_data, markdown_body="Test content")

    # 3. Simulate service crash/shutdown: Clear the in-memory graph (G)
    # This is the key step for testing recovery/restart.
    G.clear()

    # Assert initial state: The graph should be empty
    assert len(G.nodes) == 0

    # --- Phase 2: Service Restart (Simulate load_graph_data call) ---

    # 4. Load the graph data from the persistent database
    # This function rebuilds G from the DB contents.
    load_graph_data()

    # --- Phase 3: Verification ---

    # 5. Assert that the node created in Phase 1 is now present in the graph
    assert len(G.nodes) > 0
    assert concept_id in G.nodes

    # 6. Further verify the node's properties were loaded correctly
    node_data = G.nodes[concept_id]
    assert node_data["type"] == "concept"
    assert node_data["id"] == concept_id

    print(f"\nSuccessfully verified graph consistency for node: {concept_id}")

    print(f"\nSuccessfully verified graph consistency for node: {concept_id}")


# Note: This test relies on the fixture setup handling the DB path configuration.
