"""Tests for MCP server tools."""

import pytest
from kyo_mcp.mcp_server import (
    G,
    QueryInput,
    create_kyo_node,
    get_node_trust_status,
    link_kyo_nodes,
    load_graph_data,
    search_knowledge,
    verify_kyo_node,
)


@pytest.fixture(autouse=True)
def reset_graph():
    """Reset the in-memory graph between tests."""
    G.clear()
    yield
    G.clear()


class TestCreateKyoNode:
    @pytest.mark.asyncio
    async def test_create_node(self):
        result = await create_kyo_node(
            title="Test Node",
            description="A test node",
            concept_type="concept",
        )
        assert "Node created successfully" in result
        assert "kyo-node-" in result

    @pytest.mark.asyncio
    async def test_create_node_with_tags(self):
        result = await create_kyo_node(
            title="Tagged Node",
            description="With tags",
            tags=["test", "example"],
        )
        assert "Node created successfully" in result

    @pytest.mark.asyncio
    async def test_create_node_with_source(self):
        result = await create_kyo_node(
            title="Sourced Node",
            description="With source",
            sources=[
                {"id": "src-1", "resource": "https://example.com", "title": "Doc"}
            ],
        )
        assert "Node created successfully" in result


class TestSearchKnowledge:
    @pytest.mark.asyncio
    async def test_search_empty(self):
        query = QueryInput(search_term="nonexistent")
        result = await search_knowledge(query)
        assert "Found 0 result(s)" in result

    @pytest.mark.asyncio
    async def test_search_after_create(self):
        await create_kyo_node(title="Searchable Node", description="Test")
        query = QueryInput(search_term="Searchable")
        result = await search_knowledge(query)
        assert "Searchable Node" in result

    @pytest.mark.asyncio
    async def test_search_by_type(self):
        await create_kyo_node(
            title="Type Test", description="Test", concept_type="dataset"
        )
        query = QueryInput(search_term="Type Test", concept_type="dataset")
        result = await search_knowledge(query)
        assert "dataset" in result


class TestLinkKyoNodes:
    @pytest.mark.asyncio
    async def test_link_nodes(self):
        node1 = await create_kyo_node(title="Source", description="Source")
        node2 = await create_kyo_node(title="Target", description="Target")
        # Extract node IDs from response (format: "Node created successfully (kyo-node-xxx)")
        source_id = node1.split(": ")[1].split(" (")[0]
        target_id = node2.split(": ")[1].split(" (")[0]
        result = await link_kyo_nodes(source_id, target_id, "references")
        assert f"{source_id} -> {target_id}" in result

    @pytest.mark.asyncio
    async def test_link_invalid_nodes(self):
        result = await link_kyo_nodes("nonexistent", "also-nonexistent", "references")
        assert "Error" in result


class TestVerifyKyoNode:
    @pytest.mark.asyncio
    async def test_verify_node(self):
        result = await create_kyo_node(title="Verify Me", description="Test")
        node_id = result.split(": ")[1].split(" (")[0]
        verify_result = await verify_kyo_node(node_id, "alice")
        assert "Human-Reviewed" in verify_result
        assert "human:alice" in verify_result

    @pytest.mark.asyncio
    async def test_verify_nonexistent(self):
        result = await verify_kyo_node("nonexistent", "alice")
        assert "not found" in result.lower()


class TestGetNodeTrustStatus:
    @pytest.mark.asyncio
    async def test_unverified_status(self):
        result = await create_kyo_node(title="Fresh Node", description="Test")
        node_id = result.split("(")[1].rstrip(")")
        status = await get_node_trust_status(node_id)
        assert "Unverified" in status

    @pytest.mark.asyncio
    async def test_verified_status(self):
        result = await create_kyo_node(title="Verified Node", description="Test")
        node_id = result.split(": ")[1].split(" (")[0]
        await verify_kyo_node(node_id, "bob")
        status = await get_node_trust_status(node_id)
        assert "Human-Reviewed" in status

    @pytest.mark.asyncio
    async def test_nonexistent_node(self):
        result = await get_node_trust_status("nonexistent")
        assert "not found" in result.lower()


class TestLoadGraphData:
    @pytest.mark.asyncio
    async def test_load_empty_graph(self):
        # Clear graph and recreate database
        G.clear()
        import kyo_mcp.database as db_mod

        # Disable foreign keys, drop tables, re-enable
        conn = db_mod.get_connection()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM knowledge_links")
        conn.execute("DELETE FROM knowledge_concepts")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        load_graph_data()
        assert len(G.nodes) == 0
