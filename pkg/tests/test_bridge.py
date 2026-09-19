"""Unit tests for BridgeLayer's Hindsight HTTP calls.

Unlike test_hindsight_integration.py (which calls the real Hindsight API
directly and skips if none is reachable), these mock every `requests`
call, so they always run and never touch a network. They exist mainly to
guard the optional Authorization header for a hosted Hindsight instance
(e.g. Hindsight Cloud, which requires `Authorization: Bearer <key>`):
since bridge.py builds each requests.post/get call individually rather
than through one shared client, a future edit to any one call site could
silently forget to wire the header through, or (the regression that
actually matters) send it when no key is configured, breaking every
self-hosted Hindsight deployment that has no auth of its own.
"""

from unittest.mock import MagicMock, patch

import pytest
from kyo_mcp.bridge import BridgeLayer
from kyo_mcp.okf_schema import OKFConcept


def _concept() -> OKFConcept:
    return OKFConcept(
        id="test-concept",
        type="concept",
        title="Test Concept",
        description="A test concept for bridge unit tests",
    )


def _mock_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


class TestHindsightHeaders:
    """_hindsight_headers() is the single source every Hindsight call
    reads from, so testing it directly covers the on/off behavior once
    instead of duplicating it per endpoint."""

    def test_no_key_returns_empty(self):
        bridge = BridgeLayer(hindsight_api_key=None)
        assert bridge._hindsight_headers() == {}

    def test_explicit_key_returns_bearer_header(self):
        bridge = BridgeLayer(hindsight_api_key="hsk_test123")
        assert bridge._hindsight_headers() == {"Authorization": "Bearer hsk_test123"}

    def test_env_var_used_when_no_explicit_key(self, monkeypatch):
        monkeypatch.setenv("HINDSIGHT_API_KEY", "hsk_fromenv")
        bridge = BridgeLayer()
        assert bridge._hindsight_headers() == {"Authorization": "Bearer hsk_fromenv"}

    def test_explicit_key_overrides_env_var(self, monkeypatch):
        monkeypatch.setenv("HINDSIGHT_API_KEY", "hsk_fromenv")
        bridge = BridgeLayer(hindsight_api_key="hsk_explicit")
        assert bridge._hindsight_headers() == {"Authorization": "Bearer hsk_explicit"}

    def test_no_env_var_no_explicit_key_is_empty(self, monkeypatch):
        monkeypatch.delenv("HINDSIGHT_API_KEY", raising=False)
        bridge = BridgeLayer()
        assert bridge._hindsight_headers() == {}


class TestHindsightCallsSendCorrectHeaders:
    """One pair of tests (no key / with key) per Hindsight HTTP call
    site, so a future call site that forgets to wire the header through,
    in either direction, fails here instead of only surfacing against
    a real hosted Hindsight deployment."""

    @pytest.mark.asyncio
    async def test_sync_concept_to_hindsight_no_key(self, monkeypatch):
        monkeypatch.delenv("HINDSIGHT_API_KEY", raising=False)
        bridge = BridgeLayer(hindsight_api_key=None)
        with patch(
            "requests.post", return_value=_mock_response(200, {"success": True})
        ) as mock_post:
            await bridge.sync_concept_to_hindsight(_concept())
        assert mock_post.call_args.kwargs["headers"] == {}

    @pytest.mark.asyncio
    async def test_sync_concept_to_hindsight_with_key(self):
        bridge = BridgeLayer(hindsight_api_key="hsk_test123")
        with patch(
            "requests.post", return_value=_mock_response(200, {"success": True})
        ) as mock_post:
            await bridge.sync_concept_to_hindsight(_concept())
        assert mock_post.call_args.kwargs["headers"] == {
            "Authorization": "Bearer hsk_test123"
        }

    @pytest.mark.asyncio
    async def test_check_hindsight_operation_no_key(self, monkeypatch):
        monkeypatch.delenv("HINDSIGHT_API_KEY", raising=False)
        bridge = BridgeLayer(hindsight_api_key=None)
        with (
            patch(
                "kyo_mcp.bridge.get_hindsight_operation",
                return_value=("op-123", "hash-abc"),
            ),
            patch(
                "requests.get",
                return_value=_mock_response(200, {"status": "pending"}),
            ) as mock_get,
        ):
            await bridge.check_hindsight_operation("test-concept")
        assert mock_get.call_args.kwargs["headers"] == {}

    @pytest.mark.asyncio
    async def test_check_hindsight_operation_with_key(self):
        bridge = BridgeLayer(hindsight_api_key="hsk_test123")
        with (
            patch(
                "kyo_mcp.bridge.get_hindsight_operation",
                return_value=("op-123", "hash-abc"),
            ),
            patch(
                "requests.get",
                return_value=_mock_response(200, {"status": "pending"}),
            ) as mock_get,
        ):
            await bridge.check_hindsight_operation("test-concept")
        assert mock_get.call_args.kwargs["headers"] == {
            "Authorization": "Bearer hsk_test123"
        }

    @pytest.mark.asyncio
    async def test_recall_from_hindsight_no_key(self, monkeypatch):
        monkeypatch.delenv("HINDSIGHT_API_KEY", raising=False)
        bridge = BridgeLayer(hindsight_api_key=None)
        with patch(
            "requests.post", return_value=_mock_response(200, {"results": []})
        ) as mock_post:
            await bridge.recall_from_hindsight("query")
        assert mock_post.call_args.kwargs["headers"] == {}

    @pytest.mark.asyncio
    async def test_recall_from_hindsight_with_key(self):
        bridge = BridgeLayer(hindsight_api_key="hsk_test123")
        with patch(
            "requests.post", return_value=_mock_response(200, {"results": []})
        ) as mock_post:
            await bridge.recall_from_hindsight("query")
        assert mock_post.call_args.kwargs["headers"] == {
            "Authorization": "Bearer hsk_test123"
        }

    @pytest.mark.asyncio
    async def test_trigger_reflection_no_key(self, monkeypatch):
        monkeypatch.delenv("HINDSIGHT_API_KEY", raising=False)
        bridge = BridgeLayer(hindsight_api_key=None)
        with patch(
            "requests.post", return_value=_mock_response(200, {"observations": []})
        ) as mock_post:
            await bridge.trigger_reflection("query")
        assert mock_post.call_args.kwargs["headers"] == {}

    @pytest.mark.asyncio
    async def test_trigger_reflection_with_key(self):
        bridge = BridgeLayer(hindsight_api_key="hsk_test123")
        with patch(
            "requests.post", return_value=_mock_response(200, {"observations": []})
        ) as mock_post:
            await bridge.trigger_reflection("query")
        assert mock_post.call_args.kwargs["headers"] == {
            "Authorization": "Bearer hsk_test123"
        }

    @pytest.mark.asyncio
    async def test_trigger_consolidation_no_key(self, monkeypatch):
        monkeypatch.delenv("HINDSIGHT_API_KEY", raising=False)
        bridge = BridgeLayer(hindsight_api_key=None)
        with patch(
            "requests.post", return_value=_mock_response(200, {})
        ) as mock_post:
            await bridge.trigger_consolidation()
        assert mock_post.call_args.kwargs["headers"] == {}

    @pytest.mark.asyncio
    async def test_trigger_consolidation_with_key(self):
        bridge = BridgeLayer(hindsight_api_key="hsk_test123")
        with patch(
            "requests.post", return_value=_mock_response(200, {})
        ) as mock_post:
            await bridge.trigger_consolidation()
        assert mock_post.call_args.kwargs["headers"] == {
            "Authorization": "Bearer hsk_test123"
        }
