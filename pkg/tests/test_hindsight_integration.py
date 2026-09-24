"""Tests for Hindsight integration with OKF v0.2."""

import os
import time
import uuid

import pytest
import requests
from kyo_mcp.okf_schema import OKFConcept

# Add parent directory to path for imports


HINDSIGHT_BASE_URL = os.environ.get("HINDSIGHT_API_BASE_URL", "http://localhost:8888")
TEST_BANK_ID = "kyo-test"

# None of these calls used to set a requests timeout, so a slow or
# overloaded Hindsight backend could hang the whole test run indefinitely
# instead of failing. HTTP_TIMEOUT covers plain reads; LLM_TIMEOUT covers a
# single synchronous LLM-backed request/response cycle (recall/reflect/
# consolidate) and is generous on purpose, since CPU-only prompt processing
# on a large context can legitimately take well over a minute.
HTTP_TIMEOUT = 30
LLM_TIMEOUT = 180

# A queued retain operation (async store) is a separate budget from
# LLM_TIMEOUT: on a slow or CPU-only backend, prompt eval alone can take
# well over a minute before generation even starts, and retain's own
# generation has no output-token cap, so it can keep running for a long
# time with no natural stopping point. This is deliberately much larger
# than LLM_TIMEOUT to give a real store a fair chance to reach "completed"
# on such a backend.
OPERATION_TIMEOUT = 900


def _store_memory(content, tags=None, importance=5, bank_id=TEST_BANK_ID):
    """Submit a memory with async=True, matching bridge.py's
    sync_concept_to_hindsight. A synchronous (async=False) store blocks on
    the full retain/extraction pipeline, which has no output-token cap on
    the LLM side and can run for a long time with no natural stopping
    point on a slow backend. async=True returns as soon as the item is
    enqueued, regardless of how long extraction itself takes.
    """
    operation_id = str(uuid.uuid4())
    item = {"content": content, "importance": importance}
    if tags:
        item["tags"] = tags
    response = requests.post(
        f"{HINDSIGHT_BASE_URL}/v1/default/banks/{bank_id}/memories",
        json={"items": [item], "async": True, "operation_id": operation_id},
        timeout=HTTP_TIMEOUT,
    )
    return response, operation_id


def _wait_for_operation(
    operation_id, bank_id=TEST_BANK_ID, timeout=OPERATION_TIMEOUT, poll_interval=2
):
    """Poll .../operations/{operation_id} (the same endpoint
    check_hindsight_operation in bridge.py polls) until it reaches a
    terminal status or the timeout elapses. Bounded, unlike blocking on the
    store call itself: a slow backend fails this with a clear assertion
    instead of hanging the whole test run."""
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        response = requests.get(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks/{bank_id}/operations/{operation_id}",
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            pytest.fail(
                f"Operation status request failed with HTTP {response.status_code}: "
                f"{response.text}"
            )
        status = response.json().get("status")
        if status in ("completed", "failed", "cancelled", "not_found"):
            return status
        time.sleep(poll_interval)
    pytest.fail(
        f"Hindsight operation {operation_id} did not reach a terminal status "
        f"within {timeout}s (last status: {status})"
    )


# Check if LLM is available (for fact extraction and reflection)
# In 'none' mode, we can store/recall but not reflect
HAS_LLM = (
    bool(os.environ.get("HINDSIGHT_API_LLM_API_KEY"))
    and os.environ.get("HINDSIGHT_API_LLM_API_KEY") != "sk-test-key-for-development"
    and os.environ.get("HINDSIGHT_API_LLM_PROVIDER", "none") != "none"
)


def _hindsight_reachable() -> bool:
    """Probe Hindsight with a short timeout so a missing backend skips this
    module in seconds instead of burning HTTP_TIMEOUT/LLM_TIMEOUT per test."""
    try:
        requests.get(f"{HINDSIGHT_BASE_URL}/health", timeout=3)
        return True
    except requests.exceptions.RequestException:
        return False


# Opt-in only: these tests write real memories into the kyo-test bank and
# queue real LLM fact extraction. Running them whenever Hindsight merely
# happened to be reachable (e.g. from daishin) queued hundreds of retain jobs
# that kept the single-slot LLM router busy for hours.
INTEGRATION_ENABLED = os.environ.get("KYO_INTEGRATION_TESTS") == "1"

pytestmark = pytest.mark.skipif(
    not INTEGRATION_ENABLED or not _hindsight_reachable(),
    reason=(
        "set KYO_INTEGRATION_TESTS=1 to run against a real Hindsight"
        if not INTEGRATION_ENABLED
        else f"Hindsight not reachable at {HINDSIGHT_BASE_URL}"
    ),
)


class TestHindsightConnection:
    """Test Hindsight API connection."""

    def test_api_health(self):
        """Test that Hindsight API is healthy."""
        response = requests.get(f"{HINDSIGHT_BASE_URL}/health", timeout=HTTP_TIMEOUT)
        if response.status_code != 200:
            pytest.fail(
                f"Operation status request failed with HTTP {response.status_code}: "
                f"{response.text}"
            )
        data = response.json()
        if data.get("status") != "healthy":
            pytest.fail(f"Expected a healthy Hindsight API, got: {data}")
        if data.get("database") != "connected":
            pytest.fail(f"Expected a connected Hindsight database, got: {data}")

    def test_api_version(self):
        """Test that we can get API version."""
        response = requests.get(f"{HINDSIGHT_BASE_URL}/version", timeout=HTTP_TIMEOUT)
        if response.status_code != 200:
            pytest.fail(
                f"Operation status request failed with HTTP {response.status_code}: "
                f"{response.text}"
            )
        data = response.json()
        if "api_version" not in data and "version" not in data:
            pytest.fail("Response does not contain an 'api_version' or 'version' field")

    def test_list_banks(self):
        """Test listing banks."""
        response = requests.get(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks", timeout=HTTP_TIMEOUT
        )
        if response.status_code != 200:
            pytest.fail(
                f"Operation status request failed with HTTP {response.status_code}: "
                f"{response.text}"
            )
        data = response.json()
        if "banks" not in data:
            pytest.fail("Response does not contain a 'banks' field")
        if not isinstance(data["banks"], list):
            pytest.fail("Response 'banks' field is not a list")


class TestHindsightMemoryOperations:
    """Test Hindsight memory operations."""

    def setup_method(self):
        """Setup test bank."""
        # Banks are created automatically on first memory store
        # We'll create one by storing a test memory
        response, _ = _store_memory("Test setup memory", importance=1)
        if response.status_code not in [200, 201]:
            pytest.fail(
                f"Operation status request failed with HTTP {response.status_code}: "
                f"{response.text}"
            )

    def test_store_memory(self):
        """Test storing a memory. Only checks that the item was accepted
        (matching the original, pre-async test's own scope), not that
        extraction has finished. Waiting for completion here would
        reintroduce the same long block that switching to async=True was
        meant to remove; see test_recall_memory below for a case that
        genuinely needs to wait."""
        response, _ = _store_memory("Test memory for Kyo integration")
        if response.status_code not in [200, 201]:
            pytest.fail(
                f"Operation status request failed with HTTP {response.status_code}: "
                f"{response.text}"
            )
        data = response.json()
        if not (data.get("success") is True or "bank_id" in data):
            raise AssertionError("Memory store response did not indicate success")

    @pytest.mark.skipif(not HAS_LLM, reason="Requires LLM for semantic search")
    def test_recall_memory(self):
        """Test recalling memories."""
        # Store a memory and wait for extraction, since recall searches
        # over extracted facts, not the raw submitted item.
        _, operation_id = _store_memory("Test memory for Kyo integration")
        operation_status = _wait_for_operation(operation_id)
        if operation_status != "completed":
            pytest.fail(
                f"Memory extraction operation ended with status: {operation_status}"
            )

        # Then recall
        response = requests.post(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks/{TEST_BANK_ID}/memories/recall",
            json={"query": "test memory", "top_k": 5},
            timeout=LLM_TIMEOUT,
        )
        if response.status_code != 200:
            pytest.fail(
                f"Operation status request failed with HTTP {response.status_code}: "
                f"{response.text}"
            )
        data = response.json()
        if "results" not in data and "memories" not in data:
            pytest.fail("Recall response contains neither results nor memories")

    def test_list_memories(self):
        """Test listing memories."""
        # First store a memory
        self.test_store_memory()

        # Then list
        response = requests.get(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks/{TEST_BANK_ID}/memories/list",
            params={"limit": 10},
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            pytest.fail(
                f"Operation status request failed with HTTP {response.status_code}: "
                f"{response.text}"
            )
        data = response.json()
        if not isinstance(data, dict):
            pytest.fail("Expected the memories list response to be a JSON object")


@pytest.mark.skipif(not HAS_LLM, reason="Requires LLM for reflection")
class TestHindsightReflection:
    """Test Hindsight reflection operations."""

    def setup_method(self):
        """Setup test bank with memories."""
        # Create bank by storing first memory
        _, setup_op = _store_memory("Setup memory", importance=1)

        # Store some memories
        operation_ids = [setup_op]
        for i in range(3):
            _, operation_id = _store_memory(
                f"Test memory {i} for reflection", importance=3
            )
            operation_ids.append(operation_id)
        for operation_id in operation_ids:
            _wait_for_operation(operation_id)

    def test_reflect(self):
        """Test triggering reflection."""
        response = requests.post(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks/{TEST_BANK_ID}/reflect",
            json={
                "query": "What do I know about test memories?",
                "mode": "observations",
            },
            timeout=LLM_TIMEOUT,
        )
        if response.status_code != 200:
            pytest.fail(
                f"Operation status request failed with HTTP {response.status_code}: "
                f"{response.text}"
            )
        data = response.json()
        if "observations" not in data and "results" not in data:
            pytest.fail("Reflection response contains neither observations nor results")


class TestOKFHindsightSync:
    """Test syncing OKF concepts to Hindsight."""

    def setup_method(self):
        """Setup test bank."""
        # Create bank by storing first memory
        response, _ = _store_memory("Setup memory", importance=1)
        if response.status_code not in [200, 201]:
            pytest.fail(
                f"Failed to create test bank with HTTP {response.status_code}: {response.text}"
            )

    def test_sync_concept_to_hindsight(self):
        """Test syncing an OKF concept to Hindsight."""
        concept = OKFConcept(
            id="test-concept-1",
            type="concept",
            title="Test Concept",
            description="A test concept for Hindsight integration",
        )

        # Sync to Hindsight
        content = f"{concept.title}: {concept.description}"
        response, _ = _store_memory(content)
        if response.status_code not in [200, 201]:
            pytest.fail(
                f"Failed to sync concept to Hindsight with HTTP {response.status_code}: {response.text}"
            )

    def test_sync_concept_with_context(self):
        """Test syncing an OKF concept with context."""
        concept = OKFConcept(
            id="test-concept-2",
            type="concept",
            title="NixOS Configuration",
            description="NixOS uses declarative configuration",
            tags=["nixos", "configuration"],
        )

        # Sync with context
        content = f"{concept.title}: {concept.description}"
        response, _ = _store_memory(content, tags=concept.tags)
        if response.status_code not in [200, 201]:
            pytest.fail(
                f"Failed to sync concept with context to Hindsight with HTTP {response.status_code}: {response.text}"
            )


@pytest.mark.skipif(not HAS_LLM, reason="Requires LLM for consolidation")
class TestHindsightConsolidation:
    """Test Hindsight consolidation operations."""

    def setup_method(self):
        """Setup test bank with memories."""
        # Create bank by storing first memory
        _, setup_op = _store_memory("Setup memory", importance=1)

        # Store some memories
        operation_ids = [setup_op]
        for i in range(5):
            _, operation_id = _store_memory(
                f"Test memory {i} for consolidation", importance=3
            )
            operation_ids.append(operation_id)
        for operation_id in operation_ids:
            _wait_for_operation(operation_id)

    def test_consolidate(self):
        """Test triggering consolidation."""
        response = requests.post(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks/{TEST_BANK_ID}/consolidate",
            json={"mode": "full"},
            timeout=LLM_TIMEOUT,
        )
        # Consolidation may be async, so we just check it starts
        if response.status_code not in [200, 202]:
            pytest.fail(
                f"Failed to trigger consolidation with HTTP {response.status_code}: {response.text}"
            )
