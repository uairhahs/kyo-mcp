"""Unit tests for BridgeLayer's Hindsight HTTP calls.

Unlike test_hindsight_integration.py (which calls the real Hindsight API
directly and skips if none is reachable), these mock every httpx
request, so they always run and never touch a network. They exist mainly to
guard the optional Authorization header for a hosted Hindsight instance
(e.g. Hindsight Cloud, which requires `Authorization: Bearer <key>`): a
future edit to any one call site could silently forget to wire the header
through, or (the regression that actually matters) send it when no key is
configured, breaking every self-hosted Hindsight deployment that has no
auth of its own.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import tests.hindsight_fixtures as hindsight
from kyo_mcp.bridge import DEFAULT_REFLECT_TIMEOUT, BridgeLayer, reflect_timeout
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
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(200, hindsight.retain_response()),
        ) as mock_post:
            await bridge.sync_concept_to_hindsight(_concept())
        assert mock_post.call_args.kwargs["headers"] == {}

    @pytest.mark.asyncio
    async def test_sync_concept_to_hindsight_with_key(self):
        bridge = BridgeLayer(hindsight_api_key="hsk_test123")
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(200, hindsight.retain_response()),
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
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=_mock_response(200, hindsight.operation_status("pending")),
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
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=_mock_response(200, hindsight.operation_status("pending")),
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
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(200, hindsight.recall_response([])),
        ) as mock_post:
            await bridge.recall_from_hindsight("query")
        assert mock_post.call_args.kwargs["headers"] == {}

    @pytest.mark.asyncio
    async def test_recall_from_hindsight_with_key(self):
        bridge = BridgeLayer(hindsight_api_key="hsk_test123")
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(200, hindsight.recall_response([])),
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
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(200, hindsight.reflect_response("")),
        ) as mock_post:
            await bridge.trigger_reflection("query")
        assert mock_post.call_args.kwargs["headers"] == {}

    @pytest.mark.asyncio
    async def test_trigger_reflection_with_key(self):
        bridge = BridgeLayer(hindsight_api_key="hsk_test123")
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(200, hindsight.reflect_response("")),
        ) as mock_post:
            await bridge.trigger_reflection("query")
        assert mock_post.call_args.kwargs["headers"] == {
            "Authorization": "Bearer hsk_test123"
        }

    @pytest.mark.asyncio
    async def test_trigger_reflection_uses_reflect_timeout(self, monkeypatch):
        monkeypatch.setenv("KYO_REFLECT_TIMEOUT", "900")
        bridge = BridgeLayer(hindsight_api_key=None)
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(200, hindsight.reflect_response("")),
        ) as mock_post:
            await bridge.trigger_reflection("query")
        assert mock_post.call_args.kwargs["timeout"] == 900.0

    @pytest.mark.asyncio
    async def test_trigger_consolidation_no_key(self, monkeypatch):
        monkeypatch.delenv("HINDSIGHT_API_KEY", raising=False)
        bridge = BridgeLayer(hindsight_api_key=None)
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(200, hindsight.consolidation_response()),
        ) as mock_post:
            await bridge.trigger_consolidation()
        assert mock_post.call_args.kwargs["headers"] == {}

    @pytest.mark.asyncio
    async def test_trigger_consolidation_with_key(self):
        bridge = BridgeLayer(hindsight_api_key="hsk_test123")
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(200, hindsight.consolidation_response()),
        ) as mock_post:
            await bridge.trigger_consolidation()
        assert mock_post.call_args.kwargs["headers"] == {
            "Authorization": "Bearer hsk_test123"
        }


class TestHindsightLocation:
    """The namespace and bank used to be hardcoded as default/kyo."""

    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("HINDSIGHT_NAMESPACE", raising=False)
        monkeypatch.delenv("HINDSIGHT_BANK", raising=False)
        bridge = BridgeLayer(hindsight_url="http://h:8888/")
        assert (
            bridge._bank_url("memories")
            == "http://h:8888/v1/default/banks/kyo/memories"
        )

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("HINDSIGHT_NAMESPACE", "team")
        monkeypatch.setenv("HINDSIGHT_BANK", "notes")
        bridge = BridgeLayer(hindsight_url="http://h:8888")
        assert (
            bridge._bank_url("reflect") == "http://h:8888/v1/team/banks/notes/reflect"
        )


class TestErrorReporting:
    @pytest.mark.asyncio
    async def test_http_error_sets_last_error(self):
        bridge = BridgeLayer()
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(500, text="boom"),
        ):
            assert await bridge.trigger_consolidation() is False
        assert "500" in bridge.last_error and "boom" in bridge.last_error

    @pytest.mark.asyncio
    async def test_network_error_sets_last_error(self):
        import httpx

        bridge = BridgeLayer()
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            assert await bridge.recall_from_hindsight("q") == []
        assert "refused" in bridge.last_error


class TestSyncAll:
    @pytest.mark.asyncio
    async def test_polls_pending_operations_first(self):
        from kyo_mcp.database import (
            create_concept,
            get_hindsight_operation,
            get_sync_hash,
            set_hindsight_operation,
        )

        create_concept({"id": "n1", "type": "concept", "title": "One"})
        set_hindsight_operation("n1", "op-1", "hash-1")
        bridge = BridgeLayer()
        with (
            patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=_mock_response(
                    200, hindsight.operation_status("completed")
                ),
            ),
            patch.object(
                BridgeLayer, "sync_concept_to_mnemosyne", AsyncMock(return_value=True)
            ),
            patch.object(
                BridgeLayer, "sync_concept_to_hindsight", AsyncMock(return_value=True)
            ),
        ):
            stats = await bridge.sync_all_concepts()
        assert stats["hindsight_completed"] == 1
        assert stats["total"] == 1
        assert get_hindsight_operation("n1") is None
        assert get_sync_hash("n1", "hindsight") == "hash-1"


class TestBulkEfficiency:
    @pytest.mark.asyncio
    async def test_sync_all_shares_one_client(self, monkeypatch):
        """Bulk sync reuses one HTTP client instead of opening a connection
        per request, and submits every changed concept to Hindsight."""
        import httpx
        from kyo_mcp.database import create_concept

        for i in range(12):
            create_concept({"id": f"n{i}", "type": "concept", "title": f"Node {i}"})

        opened = []
        real_init = httpx.AsyncClient.__init__

        def counting_init(self, *args, **kwargs):
            opened.append(self)
            real_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", counting_init)
        with (
            patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=_mock_response(200, hindsight.retain_response()),
            ) as request,
            patch.object(
                BridgeLayer, "sync_concept_to_mnemosyne", AsyncMock(return_value=True)
            ),
        ):
            stats = await BridgeLayer().sync_all_concepts()

        assert stats["total"] == 12
        assert stats["hindsight_success"] == 12
        assert stats["mnemosyne_success"] == 12
        # One status check of the derived operation id, then the submit.
        methods = [c.args[0] for c in request.await_args_list]
        assert methods.count("POST") == 12
        assert methods.count("GET") == 12
        assert len(opened) == 1

    @pytest.mark.asyncio
    async def test_unchanged_mnemosyne_sync_skips_import(self, monkeypatch):
        """An unchanged concept must not pay the ~0.6s mnemosyne import."""
        import sys

        from kyo_mcp.bridge import _content_hash
        from kyo_mcp.database import create_concept, set_sync_hash

        create_concept(
            {"id": "same", "type": "concept", "title": "T", "description": "D"}
        )
        set_sync_hash("same", "mnemosyne", _content_hash("T: D"))
        # Makes any `import mnemosyne` raise ImportError.
        monkeypatch.setitem(sys.modules, "mnemosyne", None)

        concept = OKFConcept(id="same", type="concept", title="T", description="D")
        assert await BridgeLayer().sync_concept_to_mnemosyne(concept) is True


def _fake_mnemosyne(monkeypatch, memory_id="mem-new", forgotten=True):
    import sys
    import types

    module = types.ModuleType("mnemosyne")
    module.remember = MagicMock(return_value=memory_id)
    module.forget = MagicMock(return_value=forgotten)
    monkeypatch.setitem(sys.modules, "mnemosyne", module)
    return module


class TestMnemosyneMemoryTracking:
    @pytest.mark.asyncio
    async def test_resync_replaces_previous_memory(self, monkeypatch):
        from kyo_mcp.database import create_concept, get_mnemosyne_memory_id

        create_concept(
            {"id": "n1", "type": "concept", "title": "T", "description": "D"}
        )
        mnemosyne = _fake_mnemosyne(monkeypatch, memory_id="mem-1")
        bridge = BridgeLayer()
        await bridge.sync_concept_to_mnemosyne(
            OKFConcept(id="n1", type="concept", title="T", description="D")
        )
        assert get_mnemosyne_memory_id("n1") == "mem-1"
        mnemosyne.forget.assert_not_called()

        mnemosyne.remember.return_value = "mem-2"
        await bridge.sync_concept_to_mnemosyne(
            OKFConcept(id="n1", type="concept", title="T", description="Edited")
        )
        mnemosyne.forget.assert_called_once_with("mem-1")
        assert get_mnemosyne_memory_id("n1") == "mem-2"


class TestDeleteFromMemorySystems:
    @pytest.fixture(autouse=True)
    def node(self):
        from kyo_mcp.database import create_concept

        create_concept(
            {"id": "n1", "type": "concept", "title": "T", "description": "D"}
        )

    @pytest.mark.asyncio
    async def test_cancels_pending_retain_then_deletes_document(self, monkeypatch):
        from kyo_mcp.database import set_hindsight_operation

        set_hindsight_operation("n1", "op-1", "hash")
        bridge = BridgeLayer(hindsight_api_key=None)
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(200),
        ) as request:
            result = await bridge.delete_concept_from_memory_systems("n1")
        calls = [
            (c.args[0], c.args[1].rsplit("/banks/kyo/", 1)[1])
            for c in request.call_args_list
        ]
        assert calls == [("DELETE", "operations/op-1"), ("DELETE", "documents/n1")]
        assert result == {"hindsight": "deleted", "mnemosyne": "untracked"}
        assert bridge.last_error is None

    @pytest.mark.asyncio
    async def test_missing_document_is_not_an_error(self):
        bridge = BridgeLayer()
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(404, text="Document not found"),
        ):
            result = await bridge.delete_concept_from_memory_systems("n1")
        assert result["hindsight"] == "not_found"
        assert bridge.last_error is None

    @pytest.mark.asyncio
    async def test_http_error_sets_last_error(self):
        bridge = BridgeLayer()
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(500, text="boom"),
        ):
            result = await bridge.delete_concept_from_memory_systems("n1")
        assert result["hindsight"] == "error"
        assert "HTTP 500" in bridge.last_error

    @pytest.mark.asyncio
    async def test_forgets_tracked_mnemosyne_memory(self, monkeypatch):
        from kyo_mcp.database import set_mnemosyne_memory_id

        set_mnemosyne_memory_id("n1", "mem-1")
        mnemosyne = _fake_mnemosyne(monkeypatch)
        bridge = BridgeLayer()
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(404),
        ):
            result = await bridge.delete_concept_from_memory_systems("n1")
        mnemosyne.forget.assert_called_once_with("mem-1")
        assert result == {"hindsight": "not_found", "mnemosyne": "deleted"}

    @pytest.mark.asyncio
    async def test_sends_auth_header_when_key_set(self):
        bridge = BridgeLayer(hindsight_api_key="hsk_test")
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(200),
        ) as request:
            await bridge.delete_concept_from_memory_systems("n1")
        assert request.call_args.kwargs["headers"] == {
            "Authorization": "Bearer hsk_test"
        }


class TestRetainRetry:
    @pytest.mark.asyncio
    async def test_failed_retain_is_retried_under_new_operation_id(self):
        """Hindsight replays a known operation_id even if that operation
        failed, so a retry must use a new id; an unacknowledged resubmit
        must still reuse the same one."""
        from kyo_mcp.database import create_concept, get_hindsight_operation

        create_concept({"id": "n1", "type": "concept", "title": "Node"})
        concept = _concept().model_copy(
            update={"id": "n1", "title": "Node", "description": None}
        )
        bridge = BridgeLayer()

        async def submit():
            with patch(
                "httpx.AsyncClient.request",
                new_callable=AsyncMock,
                return_value=_mock_response(200, hindsight.retain_response()),
            ) as request:
                assert await bridge.sync_concept_to_hindsight(concept)
            return request

        first = (await submit()).call_args.kwargs["json"]["operation_id"]

        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(200, hindsight.operation_status("failed")),
        ):
            assert (await bridge.check_hindsight_operation("n1"))["state"] == "failed"
        assert get_hindsight_operation("n1") is None

        retry = (await submit()).call_args.kwargs["json"]["operation_id"]
        assert retry != first
        assert get_hindsight_operation("n1")[0] == retry

        # A resubmit after a lost acknowledgement derives the same id.
        from kyo_mcp.database import clear_hindsight_operation

        clear_hindsight_operation("n1")
        assert (await submit()).call_args.kwargs["json"]["operation_id"] == retry


class _FakeHindsight:
    """A Hindsight that knows some operations and holds some documents,
    routing each request by method and path."""

    def __init__(self, operations=None, documents=None):
        self.operations = dict(operations or {})
        self.documents = dict(documents or {})  # node id -> content_hash
        self.broken = set()  # document ids whose check fails
        self.posted = []

    def __call__(self, method, url, **kwargs):  # AsyncMock awaits the result
        path = url.rsplit("/banks/kyo/", 1)[1]
        kind, _, name = path.partition("/")
        if method == "GET" and kind == "operations":
            if name in self.operations:
                return _mock_response(
                    200, hindsight.operation_status(self.operations[name], name)
                )
            return _mock_response(404, text="Operation not found")
        if method == "GET" and kind == "documents" and name in self.broken:
            return _mock_response(500, text="boom")
        if method == "GET" and kind == "documents":
            if name in self.documents:
                return _mock_response(
                    200, {"id": name, "content_hash": self.documents[name]}
                )
            return _mock_response(404, text="Document not found")
        if method == "POST" and path == "memories":
            operation_id = kwargs["json"]["operation_id"]
            self.posted.append(operation_id)
            self.operations.setdefault(operation_id, "pending")
            return _mock_response(200, hindsight.retain_response(operation_id))
        raise AssertionError(f"unexpected {method} {path}")


def _derived_id(node_id, content_hash, after=None):
    import uuid

    seed = f"kyo-hindsight:{node_id}:{content_hash}"
    if after:
        seed += f":after:{after}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


class TestSpentOperationIds:
    """Hindsight replays a known operation id whatever happened since: a
    completed operation whose document was deleted reports completed again
    and retains nothing. Seen for real after old Hindsight documents were
    deleted: re-syncing unchanged nodes replayed their September
    operations, every check said completed, and no document came back."""

    @staticmethod
    def _node():
        from kyo_mcp.bridge import _content_hash
        from kyo_mcp.database import create_concept

        create_concept({"id": "n1", "type": "concept", "title": "Node"})
        concept = _concept().model_copy(
            update={"id": "n1", "title": "Node", "description": None}
        )
        return concept, _content_hash("Node: ")

    @pytest.mark.asyncio
    async def test_spent_ids_are_skipped_for_the_next_in_the_chain(self):
        from kyo_mcp.database import get_failed_hindsight_operation

        concept, h = self._node()
        first = _derived_id("n1", h)
        second = _derived_id("n1", h, after=first)
        # The first submit failed; its retry completed, and the document it
        # left was deleted later.
        fake = _FakeHindsight({first: "failed", second: "completed"})
        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as request:
            request.side_effect = fake
            assert await BridgeLayer().sync_concept_to_hindsight(concept)
        assert fake.posted == [_derived_id("n1", h, after=second)]
        assert get_failed_hindsight_operation("n1") == second

    @pytest.mark.asyncio
    async def test_completed_with_its_document_is_not_resubmitted(self):
        from kyo_mcp.database import get_hindsight_operation, get_sync_hash

        concept, h = self._node()
        fake = _FakeHindsight({_derived_id("n1", h): "completed"}, documents={"n1": h})
        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as request:
            request.side_effect = fake
            assert await BridgeLayer().sync_concept_to_hindsight(concept)
        assert fake.posted == []
        assert get_sync_hash("n1", "hindsight") == h
        assert get_hindsight_operation("n1") is None

    @pytest.mark.asyncio
    async def test_completed_id_whose_document_holds_another_version_is_spent(self):
        """Content A synced, then B replaced the document, then the node went
        back to A: A's old operation would replay completed and retain
        nothing, leaving B in Hindsight."""
        concept, h = self._node()
        first = _derived_id("n1", h)
        fake = _FakeHindsight({first: "completed"}, documents={"n1": "hash-of-b"})
        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as request:
            request.side_effect = fake
            assert await BridgeLayer().sync_concept_to_hindsight(concept)
        assert fake.posted == [_derived_id("n1", h, after=first)]

    @pytest.mark.asyncio
    async def test_in_flight_id_is_still_reused(self):
        """A resubmit while the first is still running must replay it, not
        start a second retain of the same content."""
        concept, h = self._node()
        first = _derived_id("n1", h)
        fake = _FakeHindsight({first: "processing"})
        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as request:
            request.side_effect = fake
            assert await BridgeLayer().sync_concept_to_hindsight(concept)
        assert fake.posted == [first]

    @pytest.mark.asyncio
    async def test_check_does_not_trust_a_completion_without_a_document(self):
        from kyo_mcp.database import (
            get_failed_hindsight_operation,
            get_hindsight_operation,
            get_sync_hash,
            set_hindsight_operation,
        )

        concept, h = self._node()
        set_hindsight_operation("n1", "op-old", h)
        fake = _FakeHindsight({"op-old": "completed"})
        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as request:
            request.side_effect = fake
            state = await BridgeLayer().check_hindsight_operation("n1")
        assert state["state"] == "failed"
        assert get_failed_hindsight_operation("n1") == "op-old"
        assert get_hindsight_operation("n1") is None
        assert get_sync_hash("n1", "hindsight") is None

        # Completed, but the document holds another version.
        set_hindsight_operation("n1", "op-mid", h)
        fake.operations["op-mid"] = "completed"
        fake.documents["n1"] = "hash-of-b"
        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as request:
            request.side_effect = fake
            state = await BridgeLayer().check_hindsight_operation("n1")
        assert state["state"] == "failed"
        assert get_sync_hash("n1", "hindsight") is None

        fake.documents["n1"] = h
        set_hindsight_operation("n1", "op-new", h)
        fake.operations["op-new"] = "completed"
        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as request:
            request.side_effect = fake
            state = await BridgeLayer().check_hindsight_operation("n1")
        assert state["state"] == "completed"
        assert get_sync_hash("n1", "hindsight") == h


class TestSyncAllVerify:
    """sync_all skips a node on kyo's own record of what it synced, so a
    document deleted or replaced on the Hindsight side goes unnoticed
    unless verify asks Hindsight."""

    @staticmethod
    def _nodes():
        from kyo_mcp.bridge import _content_hash
        from kyo_mcp.database import create_concept, set_sync_hash

        hashes = {}
        for name in ("held", "gone", "other", "broken"):
            create_concept({"id": name, "type": "concept", "title": name})
            hashes[name] = _content_hash(f"{name}: ")
            set_sync_hash(name, "hindsight", hashes[name])
        return hashes

    async def _sync_all(self, fake, verify):
        with (
            patch("httpx.AsyncClient.request", new_callable=AsyncMock) as request,
            patch.object(
                BridgeLayer, "sync_concept_to_mnemosyne", AsyncMock(return_value=True)
            ),
        ):
            request.side_effect = fake
            return await BridgeLayer().sync_all_concepts(verify=verify)

    @pytest.mark.asyncio
    async def test_verify_resubmits_only_missing_or_replaced_documents(self):
        from kyo_mcp.database import get_hindsight_operation, get_sync_hash

        h = self._nodes()
        fake = _FakeHindsight(
            documents={"held": h["held"], "other": "hash-of-b", "broken": h["broken"]}
        )
        fake.broken.add("broken")
        stats = await self._sync_all(fake, verify=True)
        assert (
            stats["hindsight_verified"],
            stats["hindsight_stale"],
            stats["hindsight_unverified"],
        ) == (1, 2, 1)
        posted = {
            n
            for n in h
            if get_hindsight_operation(n)
            and get_hindsight_operation(n)[0] in fake.posted
        }
        assert posted == {"gone", "other"}
        assert len(fake.posted) == 2
        assert get_sync_hash("held", "hindsight") == h["held"]
        assert get_sync_hash("broken", "hindsight") == h["broken"]

    @pytest.mark.asyncio
    async def test_without_verify_nothing_is_checked_or_resubmitted(self):
        self._nodes()
        fake = _FakeHindsight()
        stats = await self._sync_all(fake, verify=False)
        assert fake.posted == []
        assert stats["hindsight_success"] == 4
        assert stats["hindsight_stale"] == 0


class TestHindsightRetainCarriesProvenance:
    """materialize_from_hindsight can only map a recalled memory back to
    its source kyo node if sync_concept_to_hindsight actually sends that
    node's id along with the content."""

    @pytest.mark.asyncio
    async def test_retain_sends_document_id_and_okf_id_metadata(self):
        bridge = BridgeLayer()
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=_mock_response(200, hindsight.retain_response()),
        ) as request:
            await bridge.sync_concept_to_hindsight(_concept())
        item = request.call_args.kwargs["json"]["items"][0]
        assert item["document_id"] == "test-concept"
        assert item["metadata"] == {
            "okf_id": "test-concept",
            "concept_type": "concept",
        }


class TestMaterializeFromHindsight:
    """recall_from_hindsight alone only ever hands back text for one turn;
    materialize_from_hindsight is what actually pulls a memory back into
    the local catalogue, so a synced-and-dropped concept can be worked
    with again."""

    def _recall_response(self, results):
        return _mock_response(200, hindsight.recall_response(results))

    @pytest.mark.asyncio
    async def test_touches_existing_node_mapped_by_okf_id(self):
        from kyo_mcp.database import create_concept, get_concept_by_id

        create_concept(
            {
                "id": "existing-node",
                "type": "concept",
                "title": "Existing",
                "description": "Original content",
            },
            "Body",
        )
        before = get_concept_by_id("existing-node")["updated_at"]

        bridge = BridgeLayer()
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=self._recall_response(
                [
                    hindsight.recall_result(
                        id="mem-1",
                        text="Existing: Original content",
                        okf_id="existing-node",
                    )
                ]
            ),
        ):
            stats = await bridge.materialize_from_hindsight("existing")

        assert stats == {"recalled": 1, "touched": 1, "created": 0}
        after = get_concept_by_id("existing-node")
        assert after["description"] == "Original content"  # content untouched
        assert after["updated_at"] >= before

    @pytest.mark.asyncio
    async def test_creates_new_node_when_okf_id_unresolvable(self):
        from kyo_mcp.database import get_concept_by_id

        bridge = BridgeLayer()
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=self._recall_response(
                [
                    hindsight.recall_result(
                        id="mem-alice",
                        text="Alice works at Google",
                        context="work info",
                        tags=["work"],
                    )
                ]
            ),
        ):
            stats = await bridge.materialize_from_hindsight("alice")

        assert stats == {"recalled": 1, "touched": 0, "created": 1}
        created = get_concept_by_id("kyo-recall-mem-alice")
        assert created is not None
        assert created["title"] == "work info"
        assert created["description"] == "Alice works at Google"
        assert "source:hindsight-recall" in created["tags"]

    @pytest.mark.asyncio
    async def test_stale_okf_id_for_deleted_node_creates_new_node(self):
        """A memory synced from a node that was later deleted locally has
        no local node to touch, so it must fall back to materializing a
        new one rather than silently doing nothing."""
        from kyo_mcp.database import get_concept_by_id

        bridge = BridgeLayer()
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=self._recall_response(
                [
                    hindsight.recall_result(
                        id="mem-orphan",
                        text="Content from a deleted node",
                        okf_id="long-since-deleted",
                    )
                ]
            ),
        ):
            stats = await bridge.materialize_from_hindsight("orphan")

        assert stats == {"recalled": 1, "touched": 0, "created": 1}
        assert get_concept_by_id("kyo-recall-mem-orphan") is not None

    @pytest.mark.asyncio
    async def test_repeated_materialize_updates_rather_than_duplicates(self):
        """Recalling the same native memory twice must touch the one
        materialized node, not create a second copy."""
        from kyo_mcp.database import query_catalog

        bridge = BridgeLayer()
        response = self._recall_response(
            [hindsight.recall_result(id="mem-repeat", text="Some fact", context="ctx")]
        )
        with patch(
            "httpx.AsyncClient.request", new_callable=AsyncMock, return_value=response
        ):
            await bridge.materialize_from_hindsight("fact")
            stats = await bridge.materialize_from_hindsight("fact")

        assert stats == {"recalled": 1, "touched": 1, "created": 0}
        matches = [c for c in query_catalog() if c["id"] == "kyo-recall-mem-repeat"]
        assert len(matches) == 1

    @pytest.mark.asyncio
    async def test_no_results_returns_zero_stats(self):
        bridge = BridgeLayer()
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            return_value=self._recall_response([]),
        ):
            stats = await bridge.materialize_from_hindsight("nothing")
        assert stats == {"recalled": 0, "touched": 0, "created": 0}

    @pytest.mark.asyncio
    async def test_error_returns_zero_stats(self):
        import httpx

        bridge = BridgeLayer()
        with patch(
            "httpx.AsyncClient.request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            stats = await bridge.materialize_from_hindsight("q")
        assert stats == {"recalled": 0, "touched": 0, "created": 0}
        assert "refused" in bridge.last_error


class TestReflectTimeout:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("KYO_REFLECT_TIMEOUT", raising=False)
        assert reflect_timeout() == DEFAULT_REFLECT_TIMEOUT

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("KYO_REFLECT_TIMEOUT", "450.5")
        assert reflect_timeout() == 450.5

    @pytest.mark.parametrize("raw", ["soon", "0", "-5"])
    def test_invalid_values_fall_back_to_default(self, monkeypatch, raw):
        monkeypatch.setenv("KYO_REFLECT_TIMEOUT", raw)
        assert reflect_timeout() == DEFAULT_REFLECT_TIMEOUT
