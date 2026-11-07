"""Memories of concepts removed outside the delete tool.

With the bundle as the store (kyo_mcp/store.py), a concept can leave the
catalogue without delete_kyo_node running: its file is deleted, moved
without a kyo_id, or replaced by another concept. Its Hindsight document and
Mnemosyne memory would then be orphaned, so the index leaves a tombstone
(pending_deletions) that the bridge processes on the next sync."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from kyo_mcp import database as db
from kyo_mcp import store
from kyo_mcp.bridge import BridgeLayer
from kyo_mcp.bundle_format import path_derived_id
from kyo_mcp.cli import run as cli_run
from tests.test_bridge import _fake_mnemosyne, _mock_response


def synced_node(node_id="kyo-a", title="Alpha", memory_id="mem-1", operation=None):
    """A node that has been synced to Hindsight and Mnemosyne."""
    store.save_concept({"id": node_id, "type": "concept", "title": title}, create=True)
    db.set_sync_hash(node_id, "hindsight", "h")
    db.set_mnemosyne_memory_id(node_id, memory_id)
    if operation:
        db.set_hindsight_operation(node_id, operation, "pending-hash")
    return store.concept_file(db.get_concept_by_id(node_id)["bundle_path"])


def edit(path: Path, text: str) -> None:
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if path.stat().st_mtime_ns == before:
        os.utime(path, ns=(before + 1_000_000, before + 1_000_000))


def hindsight(status=200, text=""):
    return patch(
        "httpx.AsyncClient.request",
        new_callable=AsyncMock,
        return_value=_mock_response(status, text=text),
    )


def calls(request):
    return [
        (c.args[0], c.args[1].rsplit("/banks/kyo/", 1)[1])
        for c in request.call_args_list
    ]


class TestTombstones:
    def test_deleted_file_leaves_a_tombstone(self):
        synced_node(operation="op-9").unlink()
        report = store.refresh()
        assert report["removed"] == 1 and report["pending_memory_deletions"] == 1
        tombstone = db.get_pending_deletion("kyo-a")
        assert tombstone["mnemosyne_memory_id"] == "mem-1"
        assert tombstone["hindsight_operation_id"] == "op-9"

    def test_never_synced_node_leaves_no_tombstone(self):
        """Nothing to clean up remotely, so no network calls later; Kyo
        without Hindsight or Mnemosyne never sees one."""
        store.save_concept({"id": "kyo-a", "type": "t", "title": "A"}, create=True)
        store.concept_file(db.get_concept_by_id("kyo-a")["bundle_path"]).unlink()
        store.refresh()
        assert db.list_pending_deletions() == []

    def test_moved_file_without_kyo_id_tombstones_the_old_id(self):
        edit(store.bundle_dir() / "a.md", "---\ntype: t\n---\n")
        store.refresh()
        old = path_derived_id("a")
        db.set_sync_hash(old, "hindsight", "h")
        (store.bundle_dir() / "a.md").rename(store.bundle_dir() / "b.md")
        store.refresh()
        assert [t["node_id"] for t in db.list_pending_deletions()] == [old]

    def test_moved_file_with_kyo_id_leaves_no_tombstone(self):
        old = synced_node()
        new = store.bundle_dir() / "elsewhere" / "alpha.md"
        new.parent.mkdir()
        old.rename(new)
        store.refresh()
        assert db.list_pending_deletions() == []
        assert db.get_mnemosyne_memory_id("kyo-a") == "mem-1"

    def test_replaced_file_tombstones_the_old_concept(self):
        path = synced_node()
        edit(path, "---\ntype: t\nkyo_id: kyo-other\n---\n")
        store.refresh()
        assert [t["node_id"] for t in db.list_pending_deletions()] == ["kyo-a"]

    def test_restored_file_cancels_the_tombstone(self):
        path = synced_node()
        text = path.read_text()
        path.unlink()
        store.refresh()
        assert db.get_pending_deletion("kyo-a")
        edit(path, text)
        store.refresh()
        assert db.get_pending_deletion("kyo-a") is None

    def test_explicit_delete_without_tombstone(self):
        synced_node()
        store.delete_concept("kyo-a", tombstone=False)
        assert db.list_pending_deletions() == []

    def test_store_delete_tombstones_by_default(self):
        synced_node()
        store.delete_concept("kyo-a")
        assert [t["node_id"] for t in db.list_pending_deletions()] == ["kyo-a"]


class TestProcessing:
    @pytest.mark.asyncio
    async def test_sync_removes_orphaned_memories(self, monkeypatch):
        mnemosyne = _fake_mnemosyne(monkeypatch)
        synced_node(operation="op-9").unlink()
        store.refresh()
        bridge = BridgeLayer()
        with hindsight() as request:
            stats = await bridge.process_pending_deletions()
        assert stats == {"completed": 1, "failed": 0}
        assert calls(request) == [
            ("DELETE", "operations/op-9"),
            ("DELETE", "documents/kyo-a"),
        ]
        mnemosyne.forget.assert_called_once_with("mem-1")
        assert db.list_pending_deletions() == []
        assert bridge.last_error is None

    @pytest.mark.asyncio
    async def test_failure_keeps_the_tombstone_for_a_retry(self, monkeypatch):
        _fake_mnemosyne(monkeypatch)
        synced_node().unlink()
        store.refresh()
        bridge = BridgeLayer()
        with hindsight(500, "boom"):
            stats = await bridge.process_pending_deletions()
        assert stats == {"completed": 0, "failed": 1}
        tombstone = db.get_pending_deletion("kyo-a")
        assert tombstone["attempts"] == 1 and "HTTP 500" in tombstone["last_error"]
        assert "will be retried" in bridge.last_error
        with hindsight(404):
            assert (await BridgeLayer().process_pending_deletions())["completed"] == 1
        assert db.list_pending_deletions() == []

    @pytest.mark.asyncio
    async def test_finished_operation_does_not_block_the_deletion(self, monkeypatch):
        """Regression: Hindsight answers 409 to cancelling an operation that
        already completed or failed, which used to fail the whole deletion,
        so the tombstone was retried forever."""
        _fake_mnemosyne(monkeypatch)
        synced_node(operation="op-done").unlink()
        store.refresh()
        finished = _mock_response(
            409, text="cannot be cancelled: status is 'failed', only 'pending'"
        )
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            side_effect=[finished, _mock_response(404)],
        ) as request:
            stats = await BridgeLayer().process_pending_deletions()
        assert stats == {"completed": 1, "failed": 0}
        assert calls(request) == [
            ("DELETE", "operations/op-done"),
            ("DELETE", "documents/kyo-a"),
        ]
        assert db.list_pending_deletions() == []

    @pytest.mark.asyncio
    async def test_sync_all_processes_tombstones_first(self, monkeypatch):
        _fake_mnemosyne(monkeypatch)
        synced_node().unlink()
        store.refresh()
        with hindsight(200):
            stats = await BridgeLayer().sync_all_concepts()
        assert stats["deletions_completed"] == 1 and stats["deletions_failed"] == 0
        assert db.list_pending_deletions() == []

    @pytest.mark.asyncio
    async def test_sync_all_reports_a_failed_deletion(self, monkeypatch):
        _fake_mnemosyne(monkeypatch)
        synced_node().unlink()
        store.refresh()
        bridge = BridgeLayer()
        with hindsight(500, "boom"):
            stats = await bridge.sync_all_concepts()
        assert stats["deletions_failed"] == 1
        assert bridge.last_error


class TestInterfaces:
    @pytest.mark.asyncio
    async def test_delete_tool_cleans_up_a_hand_deleted_file(self, monkeypatch):
        """The catch-22: every tool refreshes first, so after a file is
        deleted by hand the node is gone before delete_kyo_node runs. Its
        tombstone still lets the tool remove the memories now."""
        from kyo_mcp.mcp_server import delete_kyo_node

        mnemosyne = _fake_mnemosyne(monkeypatch)
        synced_node().unlink()
        with hindsight(200):
            output = await delete_kyo_node("kyo-a")
        assert "already gone from the catalogue" in output
        mnemosyne.forget.assert_called_once_with("mem-1")
        assert db.list_pending_deletions() == []

    @pytest.mark.asyncio
    async def test_delete_tool_local_only_keeps_memories(self, monkeypatch):
        from kyo_mcp.mcp_server import delete_kyo_node

        mnemosyne = _fake_mnemosyne(monkeypatch)
        synced_node().unlink()
        with hindsight(200) as request:
            output = await delete_kyo_node("kyo-a", local_only=True)
        assert "memories were kept" in output
        request.assert_not_called()
        mnemosyne.forget.assert_not_called()
        assert db.list_pending_deletions() == []

    @pytest.mark.asyncio
    async def test_delete_tool_on_a_live_node_leaves_no_tombstone(self, monkeypatch):
        from kyo_mcp.mcp_server import delete_kyo_node

        _fake_mnemosyne(monkeypatch)
        synced_node()
        with hindsight(200):
            assert "Node kyo-a deleted" in await delete_kyo_node("kyo-a")
        assert db.get_concept_by_id("kyo-a") is None
        assert db.list_pending_deletions() == []

    @pytest.mark.asyncio
    async def test_delete_tool_failure_keeps_the_tombstone(self, monkeypatch):
        from kyo_mcp.mcp_server import delete_kyo_node
        from mcp.server.mcpserver.exceptions import ToolError

        _fake_mnemosyne(monkeypatch)
        synced_node().unlink()
        with hindsight(500, "boom"), pytest.raises(ToolError, match="Pending deletion"):
            await delete_kyo_node("kyo-a")
        assert db.get_pending_deletion("kyo-a")

    @pytest.mark.asyncio
    async def test_refresh_tool_reports_pending_deletions(self):
        from kyo_mcp.mcp_server import refresh_okf_bundle

        synced_node().unlink()
        assert "still to be deleted: 1" in await refresh_okf_bundle()

    @pytest.mark.asyncio
    async def test_cli_delete_of_a_tombstoned_id(self, monkeypatch, capsys):
        _fake_mnemosyne(monkeypatch)
        synced_node().unlink()
        with hindsight(200):
            assert await cli_run(["delete_concept", "kyo-a"]) == 0
        assert db.list_pending_deletions() == []
        assert await cli_run(["delete_concept", "kyo-a"]) == 1
        assert "not found" in capsys.readouterr().err
