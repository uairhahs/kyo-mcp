"""Tests for MCP server tools."""

import re
from unittest.mock import AsyncMock, patch

import pytest
import tests.hindsight_fixtures as hindsight
from kyo_mcp.bridge import BridgeLayer
from kyo_mcp.database import get_concept_by_id
from kyo_mcp.mcp_server import (
    create_kyo_node,
    delete_kyo_node,
    find_path,
    get_kyo_node,
    get_node_links,
    get_node_trust_status,
    is_stale,
    link_kyo_nodes,
    search_knowledge,
    trigger_reflection,
    unlink_kyo_nodes,
    update_kyo_node,
    verify_kyo_node,
)
from mcp.server.mcpserver.exceptions import ToolError


def node_id_of(result: str) -> str:
    """Extract the id from "Node created successfully: <id> (<title>) | ..."."""
    return re.search(r"successfully: (\S+) \(", result).group(1)


async def make_node(title: str, **kwargs) -> str:
    return node_id_of(
        await create_kyo_node(
            title=title, description=kwargs.pop("description", "Test"), **kwargs
        )
    )


class TestCreateKyoNode:
    @pytest.mark.asyncio
    async def test_create_node(self):
        result = await create_kyo_node(
            title="Test Node",
            description="A test node",
            concept_type="concept",
        )
        assert "Node created successfully" in result
        assert "kyo-" in result

    @pytest.mark.asyncio
    async def test_create_node_with_tags(self):
        node_id = await make_node("Tagged Node", tags=["test", "example"])
        assert get_concept_by_id(node_id)["tags"] == ["test", "example"]

    @pytest.mark.asyncio
    async def test_create_node_with_source(self):
        node_id = await make_node(
            "Sourced Node",
            sources=[
                {"id": "src-1", "resource": "https://example.com", "title": "Doc"}
            ],
        )
        assert (
            get_concept_by_id(node_id)["sources"][0]["resource"]
            == "https://example.com"
        )

    @pytest.mark.asyncio
    async def test_stale_after_is_persisted(self):
        """Regression: stale_after used to be silently dropped on save."""
        node_id = await make_node("Fresh Until", stale_after="2030-01-01")
        assert get_concept_by_id(node_id)["stale_after"] == "2030-01-01"

    @pytest.mark.asyncio
    async def test_ids_are_unique_across_processes(self):
        """Regression: ids came from the in-memory node count, so two server
        processes sharing a database produced the same id and the second
        create silently overwrote the first node."""
        ids = {await make_node(f"Node {i}") for i in range(20)}
        assert len(ids) == 20
        for node_id in ids:
            assert get_concept_by_id(node_id) is not None


class TestGetKyoNode:
    @pytest.mark.asyncio
    async def test_markdown(self):
        node_id = await make_node("Doc Node", tags=["a"], stale_after="2030-01-01")
        md = await get_kyo_node(node_id)
        assert md.startswith("---\ntype: concept\n")
        assert "stale_after: '2030-01-01'" in md
        assert "process:kyo-mcp" in md

    @pytest.mark.asyncio
    async def test_markdown_lists_links(self):
        a = await make_node("A")
        b = await make_node("B")
        await link_kyo_nodes(a, b, "references")
        md = await get_kyo_node(a)
        assert f"{a} -[references]-> {b}" in md

    @pytest.mark.asyncio
    async def test_turtle(self):
        a = await make_node('A "quoted"')
        b = await make_node("B")
        await link_kyo_nodes(a, b, "references")
        ttl = await get_kyo_node(a, format="turtle")
        assert f"<urn:kyo:{a}> a skos:Concept" in ttl
        assert '"A \\"quoted\\""' in ttl
        assert f"<urn:kyo:relation:references> <urn:kyo:{b}>" in ttl

    @pytest.mark.asyncio
    async def test_missing(self):
        with pytest.raises(ToolError, match="not found"):
            await get_kyo_node("nope")


class TestUpdateAndDelete:
    @pytest.mark.asyncio
    async def test_update_fields(self):
        node_id = await make_node("Old Title")
        await update_kyo_node(node_id, title="New Title", status="deprecated")
        node = get_concept_by_id(node_id)
        assert node["title"] == "New Title"
        assert node["status"] == "deprecated"
        assert node["description"] == "Test"

    @pytest.mark.asyncio
    async def test_update_keeps_verification(self):
        node_id = await make_node("Verified Then Edited")
        await verify_kyo_node(node_id, "alice")
        await update_kyo_node(node_id, description="Edited")
        assert "Human-Reviewed" in await get_node_trust_status(node_id)

    @pytest.mark.asyncio
    async def test_update_nothing(self):
        node_id = await make_node("Untouched")
        with pytest.raises(ToolError, match="Nothing to update"):
            await update_kyo_node(node_id)

    @pytest.mark.asyncio
    async def test_delete_removes_links(self):
        a = await make_node("A")
        b = await make_node("B")
        await link_kyo_nodes(a, b, "references")
        await delete_kyo_node(b)
        assert get_concept_by_id(b) is None
        assert "no both links" in await get_node_links(a)

    @pytest.mark.asyncio
    async def test_delete_missing(self):
        with pytest.raises(ToolError):
            await delete_kyo_node("nope")


class TestSearchKnowledge:
    @pytest.mark.asyncio
    async def test_search_empty(self):
        result = await search_knowledge(search_term="nonexistent")
        assert "Found 0 result(s)" in result

    @pytest.mark.asyncio
    async def test_search_after_create(self):
        await make_node("Searchable Node")
        result = await search_knowledge(search_term="Searchable")
        assert "Searchable Node" in result

    @pytest.mark.asyncio
    async def test_search_by_type(self):
        await make_node("Type Test", concept_type="dataset")
        result = await search_knowledge(search_term="Type Test", concept_type="dataset")
        assert "dataset" in result

    @pytest.mark.asyncio
    async def test_search_matches_description_and_tags(self):
        await make_node(
            "Plain", description="talks about photosynthesis", tags=["botany"]
        )
        assert "Plain" in await search_knowledge(search_term="photosynthesis")
        assert "Plain" in await search_knowledge(search_term="botany")

    @pytest.mark.asyncio
    async def test_search_survives_fts_syntax(self):
        await make_node('Weird "quote" - AND OR')
        result = await search_knowledge(search_term='"quote" - AND (')
        assert "Found" in result

    @pytest.mark.asyncio
    async def test_limit(self):
        for i in range(5):
            await make_node(f"Many {i}")
        result = await search_knowledge(search_term="Many", limit=2)
        assert "Found 2 result(s)" in result


class TestLinks:
    @pytest.mark.asyncio
    async def test_link_nodes(self):
        a = await make_node("Source")
        b = await make_node("Target")
        result = await link_kyo_nodes(a, b, "references")
        assert f"{a} -> {b}" in result

    @pytest.mark.asyncio
    async def test_link_invalid_nodes(self):
        with pytest.raises(ToolError, match="not found"):
            await link_kyo_nodes("nonexistent", "also-nonexistent", "references")

    @pytest.mark.asyncio
    async def test_link_node_created_elsewhere(self):
        """Regression: link validation used a per-process in-memory graph,
        so nodes created by the CLI or another server process couldn't be
        linked until a restart."""
        from kyo_mcp.database import create_concept

        create_concept({"id": "from-cli", "type": "concept", "title": "CLI Node"})
        a = await make_node("Server Node")
        assert "Linked" in await link_kyo_nodes(a, "from-cli", "references")

    @pytest.mark.asyncio
    async def test_get_links_and_unlink(self):
        a = await make_node("A")
        b = await make_node("B")
        await link_kyo_nodes(a, b, "references")
        assert f"-> [references] {b} (B)" in await get_node_links(a, direction="out")
        assert f"<- [references] {a} (A)" in await get_node_links(b, direction="in")
        await unlink_kyo_nodes(a, b)
        assert "no out links" in await get_node_links(a, direction="out")

    @pytest.mark.asyncio
    async def test_unlink_missing(self):
        with pytest.raises(ToolError):
            await unlink_kyo_nodes("x", "y")

    @pytest.mark.asyncio
    async def test_find_path(self):
        a = await make_node("A")
        b = await make_node("B")
        c = await make_node("C")
        await link_kyo_nodes(a, b, "references")
        await link_kyo_nodes(b, c, "derives_from")
        path = await find_path(a, c)
        assert "-[references]->" in path and "-[derives_from]->" in path
        assert "No path" in await find_path(c, a)
        assert "No path" not in await find_path(c, a, directed=False)


class TestVerifyKyoNode:
    @pytest.mark.asyncio
    async def test_verify_node(self):
        node_id = await make_node("Verify Me")
        verify_result = await verify_kyo_node(node_id, "alice")
        assert "Human-Reviewed" in verify_result
        assert "human:alice" in verify_result

    @pytest.mark.asyncio
    async def test_verify_prefixed_actor(self):
        """Regression: an already-prefixed actor was stored as human:human:x."""
        node_id = await make_node("Prefixed")
        await verify_kyo_node(node_id, "human:bob")
        assert get_concept_by_id(node_id)["verified"][0]["by"] == "human:bob"

    @pytest.mark.asyncio
    async def test_verify_nonexistent(self):
        with pytest.raises(ToolError, match="not found"):
            await verify_kyo_node("nonexistent", "alice")


class TestGetNodeTrustStatus:
    @pytest.mark.asyncio
    async def test_unverified_status(self):
        node_id = await make_node("Fresh Node")
        status = await get_node_trust_status(node_id)
        assert "Unverified" in status
        assert "Freshness: Fresh" in status

    @pytest.mark.asyncio
    async def test_verified_status(self):
        node_id = await make_node("Verified Node")
        await verify_kyo_node(node_id, "bob")
        assert "Human-Reviewed" in await get_node_trust_status(node_id)

    @pytest.mark.asyncio
    async def test_stale_status(self):
        node_id = await make_node("Old News", stale_after="2000-01-01")
        assert "Freshness: Stale" in await get_node_trust_status(node_id)

    @pytest.mark.asyncio
    async def test_nonexistent_node(self):
        with pytest.raises(ToolError, match="not found"):
            await get_node_trust_status("nonexistent")


class TestTriggerReflection:
    """Hindsight's real ReflectResponse only ever carries a `text` field
    (confirmed against its OpenAPI schema); this tool used to look for
    "observations"/"insights" instead, a shape the real API never
    returns, so a genuine reflection always fell through to a raw dict
    dump. Mocked at the BridgeLayer level (the HTTP call itself is
    covered in test_bridge.py) so this only tests the tool's own
    response-to-text formatting."""

    @pytest.mark.asyncio
    async def test_surfaces_real_text_field(self):
        with patch.object(
            BridgeLayer,
            "trigger_reflection",
            AsyncMock(return_value=hindsight.reflect_response("## Findings\n\nIt works.")),
        ):
            output = await trigger_reflection("query")
        assert "## Findings\n\nIt works." in output

    @pytest.mark.asyncio
    async def test_empty_result_reports_no_reflections(self):
        with patch.object(
            BridgeLayer, "trigger_reflection", AsyncMock(return_value={})
        ):
            output = await trigger_reflection("query")
        assert output == "No reflections generated."


class TestIsStale:
    def test_values(self):
        assert is_stale(None) is False
        assert is_stale("2000-01-01") is True
        assert is_stale("2999-01-01T00:00:00Z") is False
        assert is_stale("not a date") is False
