"""Error path tests for MCP server tools."""

import sys
import asyncio
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import kyo_mcp.mcp_server as mcp_server_module
from kyo_mcp.mcp_server import G


@pytest.fixture(autouse=True)
def reset_graph():
    """Reset the in-memory graph between tests."""
    G.clear()
    yield
    G.clear()


@pytest.mark.asyncio
class TestCreateNodeErrorPaths:
    """Test error handling for create_kyo_node tool."""

    async def test_node_creation_with_invalid_type(self):
        """Test that invalid concept_type is handled"""
        with patch('kyo_mcp.mcp_server.create_concept'):
            with patch.object(mcp_server_module.G, 'add_node'):
                result = await mcp_server_module.create_kyo_node(
                    title="Test",
                    description="Test node",
                    concept_type="invalid_type"
                )
                assert "created successfully" in result.lower()

    async def test_node_creation_with_none_description(self):
        """Test creating node with None description"""
        with patch('kyo_mcp.mcp_server.create_concept'):
            with patch.object(mcp_server_module.G, 'add_node'):
                result = await mcp_server_module.create_kyo_node(
                    title="Test",
                    description=None
                )
                assert "created successfully" in result.lower()


@pytest.mark.asyncio
class TestLinkNodesErrorPaths:
    """Test error handling for link_kyo_nodes tool."""

    async def test_linking_nonexistent_source(self):
        """Test linking with nonexistent source node - should fail at graph check"""
        result = await mcp_server_module.link_kyo_nodes("nonexistent-1", "nonexistent-2", "references")
        assert "error" in result.lower() or "could not be found" in result.lower()

    async def test_linking_nonexistent_target(self):
        """Test linking with nonexistent target node - should fail at graph check"""
        result = await mcp_server_module.link_kyo_nodes("test-source", "nonexistent-target", "references")
        assert "error" in result.lower() or "could not be found" in result.lower()

    async def test_linking_same_node(self):
        """Test linking a node to itself - should succeed if node exists"""
        G.add_node("node-1", label="Test", type="concept")
        with patch('kyo_mcp.mcp_server.create_link') as mock_create_link:
            result = await mcp_server_module.link_kyo_nodes("node-1", "node-1", "self_ref")
            assert "links" in result.lower()
            mock_create_link.assert_called_once_with("node-1", "node-1", "self_ref")

    async def test_linking_invalid_relation_type(self):
        """Test linking with invalid relation type - should still work"""
        G.add_node("source-1", label="Source", type="concept")
        G.add_node("target-1", label="Target", type="concept")
        with patch('kyo_mcp.mcp_server.create_link') as mock_create_link:
            result = await mcp_server_module.link_kyo_nodes("source-1", "target-1", "invalid_relation")
            assert "links" in result.lower()


@pytest.mark.asyncio
class TestSearchKnowledgeErrorPaths:
    """Test error handling for search_knowledge tool."""

    async def test_search_with_empty_query(self):
        """Test searching with empty query"""
        from pydantic import BaseModel, Field

        class QueryInput(BaseModel):
            search_term: str = Field(default="", description="Search keywords")
            concept_type: str = Field(default="all", description="Filter by type")

        query = QueryInput(search_term="", concept_type="all")
        result = await mcp_server_module.search_knowledge(query)
        assert "found 0 result(s)" in result.lower() or "for:" in result.lower()

    async def test_search_with_special_characters(self):
        """Test searching with special characters"""
        from pydantic import BaseModel, Field

        class QueryInput(BaseModel):
            search_term: str = Field(default="", description="Search keywords")
            concept_type: str = Field(default="all", description="Filter by type")

        query = QueryInput(search_term="test!@#$%", concept_type="all")
        result = await mcp_server_module.search_knowledge(query)
        assert "for:" in result.lower()


@pytest.mark.asyncio
class TestVerifyNodeErrorPaths:
    """Test error handling for verify_kyo_node tool."""

    async def test_verifying_nonexistent_node(self):
        """Test verifying a node that doesn't exist"""
        with patch('kyo_mcp.mcp_server.get_concept_by_id', return_value=None):
            result = await mcp_server_module.verify_kyo_node("nonexistent-node", "human:test")
            assert "error" in result.lower() or "not found" in result.lower()

    async def test_verifying_with_empty_actor(self):
        """Test verifying with empty actor - should still succeed"""
        with patch('kyo_mcp.mcp_server.get_concept_by_id', return_value={"id": "test-1", "title": "Test Node"}):
            with patch('kyo_mcp.mcp_server.update_node_verified', return_value=True):
                result = await mcp_server_module.verify_kyo_node("test-1", "")
                assert "marked as" in result.lower() or "verified" in result.lower()

    async def test_verification_failure_return_false(self):
        """Test verification failure - database returns False"""
        with patch('kyo_mcp.mcp_server.get_concept_by_id', return_value={"id": "test-1"}):
            with patch('kyo_mcp.mcp_server.update_node_verified', return_value=False):
                result = await mcp_server_module.verify_kyo_node("test-1", "human:test")
                assert "error" in result.lower() or "failed" in result.lower()


@pytest.mark.asyncio
class TestGetNodeTrustStatusErrorPaths:
    """Test error handling for get_node_trust_status tool."""

    async def test_get_status_for_nonexistent_node(self):
        """Test getting status for a node that doesn't exist"""
        with patch('kyo_mcp.mcp_server.get_concept_by_id', return_value=None):
            result = await mcp_server_module.get_node_trust_status("nonexistent-node")
            assert "error" in result.lower() or "not found" in result.lower()

    async def test_get_status_for_unverified_node(self):
        """Test getting status for an unverified node"""
        with patch('kyo_mcp.mcp_server.get_concept_by_id', return_value={
            "id": "test-1",
            "title": "Test Node",
            "type": "concept",
            "verified": False,
            "verified_by": None
        }):
            result = await mcp_server_module.get_node_trust_status("test-1")
            assert "unverified" in result.lower() or "trust tier:" in result.lower()


@pytest.mark.asyncio
class TestEdgeCaseScenarios:
    """Test edge cases and consistency."""

    async def test_database_error_propagation(self):
        """Test that database errors propagate (not silently swallowed)"""
        with patch('kyo_mcp.mcp_server.create_concept', side_effect=Exception("DB fail")):
            with patch.object(mcp_server_module.G, 'add_node'):
                with pytest.raises(Exception, match="DB fail"):
                    await mcp_server_module.create_kyo_node(
                        title="Test", description="Test node", concept_type="concept"
                    )

    async def test_id_generation_consistency(self):
        """Test that ID generation is consistent"""
        with patch('kyo_mcp.mcp_server.create_concept', return_value="test-id-1"):
            result = await mcp_server_module.create_kyo_node("Test 1", "Desc 1")
            assert "test-id-1" in result.lower() or "created" in result.lower()
