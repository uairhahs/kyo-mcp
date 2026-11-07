"""Tests for the kyo-cli command, which pi's kyo-memory-manager extension
drives by parsing its JSON output."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from kyo_mcp.bridge import BridgeLayer
from kyo_mcp.cli import run


async def run_cli(capsys, *argv: str):
    code = await run(list(argv))
    out, err = capsys.readouterr()
    return code, json.loads(out) if out else None, json.loads(err) if err else None


@pytest.mark.asyncio
async def test_create_concept_reports_id_as_json(capsys):
    code, out, _ = await run_cli(
        capsys, "create_concept", "Label", "Summary", "--content=body with spaces"
    )
    assert code == 0
    assert out["created"] is True
    assert out["concept_id"].startswith("kyo-")

    _, concept, _ = await run_cli(capsys, "get_concept", out["concept_id"])
    assert concept["title"] == "Label"
    assert concept["body_text"] == "body with spaces"


@pytest.mark.asyncio
async def test_missing_concept_is_an_error(capsys):
    code, out, err = await run_cli(capsys, "get_concept", "nope")
    assert code == 1
    assert out is None
    assert "not found" in err["error"]


@pytest.mark.asyncio
async def test_trigger_reflection_on_concept(capsys):
    _, created, _ = await run_cli(capsys, "create_concept", "Caddy", "Reverse proxy")
    with patch.object(
        BridgeLayer,
        "trigger_reflection",
        AsyncMock(return_value={"observations": ["x"]}),
    ) as reflect:
        code, out, _ = await run_cli(
            capsys, "trigger_reflection", "--concept-id", created["concept_id"]
        )
    assert code == 0
    reflect.assert_awaited_once_with("Caddy: Reverse proxy")
    assert out["result"] == {"observations": ["x"]}


@pytest.mark.asyncio
async def test_trigger_reflection_on_query(capsys):
    with patch.object(
        BridgeLayer, "trigger_reflection", AsyncMock(return_value={})
    ) as reflect:
        code, out, _ = await run_cli(capsys, "trigger_reflection", "homelab", "network")
    assert code == 0
    reflect.assert_awaited_once_with("homelab network")


@pytest.mark.asyncio
async def test_trigger_reflection_needs_input(capsys):
    code, _, err = await run_cli(capsys, "trigger_reflection")
    assert code == 1
    assert "query" in err["error"]


@pytest.mark.asyncio
async def test_trigger_reflection_unknown_concept(capsys):
    code, _, err = await run_cli(capsys, "trigger_reflection", "--concept-id", "nope")
    assert code == 1
    assert "not found" in err["error"]


@pytest.mark.asyncio
async def test_delete_concept_removes_it_everywhere(capsys):
    _, created, _ = await run_cli(capsys, "create_concept", "Gone", "Soon")
    concept_id = created["concept_id"]
    with patch.object(
        BridgeLayer,
        "delete_concept_from_memory_systems",
        AsyncMock(return_value={"hindsight": "deleted", "mnemosyne": "untracked"}),
    ):
        code, out, _ = await run_cli(capsys, "delete_concept", concept_id)
    assert code == 0
    assert out == {
        "deleted": True,
        "concept_id": concept_id,
        "hindsight": "deleted",
        "mnemosyne": "untracked",
    }
    code, _, err = await run_cli(capsys, "get_concept", concept_id)
    assert code == 1


@pytest.mark.asyncio
async def test_delete_concept_kept_when_remote_delete_fails(capsys):
    _, created, _ = await run_cli(capsys, "create_concept", "Kept", "Here")
    concept_id = created["concept_id"]

    async def fail(self, node_id):
        self.last_error = "Hindsight down"
        return {"hindsight": "error", "mnemosyne": "untracked"}

    with patch.object(BridgeLayer, "delete_concept_from_memory_systems", fail):
        code, _, err = await run_cli(capsys, "delete_concept", concept_id)
    assert code == 1
    assert "Hindsight down" in err["error"]
    code, _, _ = await run_cli(capsys, "get_concept", concept_id)
    assert code == 0

    code, out, _ = await run_cli(capsys, "delete_concept", concept_id, "--local-only")
    assert code == 0
    assert out == {"deleted": True, "concept_id": concept_id}


@pytest.mark.asyncio
async def test_refresh_reports_its_own_pass(capsys):
    """Regression: run() refreshed before every command, so `refresh`
    printed a second, no-op pass and hid what the first one did."""
    from kyo_mcp.database import create_concept

    # An index-only row has no file yet, so a refresh writes one.
    create_concept({"id": "kyo-unfiled", "type": "t", "title": "Unfiled"}, "x\n")
    assert await run(["refresh"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["written"] == 1 and report["indexed"] == 1
