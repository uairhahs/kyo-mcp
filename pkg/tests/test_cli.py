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
