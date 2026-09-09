"""Tests for Hindsight integration with OKF v0.2."""

import os

import pytest
import requests
from kyo_mcp.okf_schema import OKFConcept

# Add parent directory to path for imports


HINDSIGHT_BASE_URL = os.environ.get("HINDSIGHT_API_BASE_URL", "http://localhost:8888")
TEST_BANK_ID = "kyo-test"

# None of these calls used to set a requests timeout, so a slow or
# overloaded Hindsight backend hung the whole test run indefinitely instead
# of failing (confirmed 2026-09-09 against a CPU-only LLM backend under
# concurrent load). HTTP_TIMEOUT covers plain reads; LLM_TIMEOUT covers
# anything that touches Hindsight's extraction/reflection/consolidation
# pipeline and is generous on purpose, since CPU-only prompt processing on
# a large context can legitimately take well over a minute.
HTTP_TIMEOUT = 30
LLM_TIMEOUT = 180

# Check if LLM is available (for fact extraction and reflection)
# In 'none' mode, we can store/recall but not reflect
HAS_LLM = (
    bool(os.environ.get("HINDSIGHT_API_LLM_API_KEY"))
    and os.environ.get("HINDSIGHT_API_LLM_API_KEY") != "sk-test-key-for-development"
    and os.environ.get("HINDSIGHT_API_LLM_PROVIDER", "none") != "none"
)


class TestHindsightConnection:
    """Test Hindsight API connection."""

    def test_api_health(self):
        """Test that Hindsight API is healthy."""
        response = requests.get(f"{HINDSIGHT_BASE_URL}/health", timeout=HTTP_TIMEOUT)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database"] == "connected"

    def test_api_version(self):
        """Test that we can get API version."""
        response = requests.get(f"{HINDSIGHT_BASE_URL}/version", timeout=HTTP_TIMEOUT)
        assert response.status_code == 200
        data = response.json()
        assert "api_version" in data or "version" in data

    def test_list_banks(self):
        """Test listing banks."""
        response = requests.get(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks", timeout=HTTP_TIMEOUT
        )
        assert response.status_code == 200
        data = response.json()
        assert "banks" in data
        assert isinstance(data["banks"], list)


class TestHindsightMemoryOperations:
    """Test Hindsight memory operations."""

    def setup_method(self):
        """Setup test bank."""
        # Banks are created automatically on first memory store
        # We'll create one by storing a test memory
        response = requests.post(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks/{TEST_BANK_ID}/memories",
            json={"items": [{"content": "Test setup memory", "importance": 1}]},
            timeout=LLM_TIMEOUT,
        )
        assert response.status_code in [200, 201]

    def test_store_memory(self):
        """Test storing a memory."""
        response = requests.post(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks/{TEST_BANK_ID}/memories",
            json={
                "items": [
                    {"content": "Test memory for Kyo integration", "importance": 5}
                ]
            },
            timeout=LLM_TIMEOUT,
        )
        assert response.status_code in [200, 201]
        data = response.json()
        assert data.get("success") is True or "bank_id" in data

    @pytest.mark.skipif(not HAS_LLM, reason="Requires LLM for semantic search")
    def test_recall_memory(self):
        """Test recalling memories."""
        # First store a memory
        self.test_store_memory()

        # Then recall
        response = requests.post(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks/{TEST_BANK_ID}/memories/recall",
            json={"query": "test memory", "top_k": 5},
            timeout=LLM_TIMEOUT,
        )
        assert response.status_code == 200
        data = response.json()
        assert "results" in data or "memories" in data

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
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)  # Just verify we get a response


@pytest.mark.skipif(not HAS_LLM, reason="Requires LLM for reflection")
class TestHindsightReflection:
    """Test Hindsight reflection operations."""

    def setup_method(self):
        """Setup test bank with memories."""
        # Create bank by storing first memory
        requests.post(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks/{TEST_BANK_ID}/memories",
            json={"items": [{"content": "Setup memory", "importance": 1}]},
            timeout=LLM_TIMEOUT,
        )

        # Store some memories
        for i in range(3):
            requests.post(
                f"{HINDSIGHT_BASE_URL}/v1/default/banks/{TEST_BANK_ID}/memories",
                json={
                    "items": [
                        {"content": f"Test memory {i} for reflection", "importance": 3}
                    ]
                },
                timeout=LLM_TIMEOUT,
            )

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
        assert response.status_code == 200
        data = response.json()
        assert "observations" in data or "results" in data


class TestOKFHindsightSync:
    """Test syncing OKF concepts to Hindsight."""

    def setup_method(self):
        """Setup test bank."""
        # Create bank by storing first memory
        response = requests.post(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks/{TEST_BANK_ID}/memories",
            json={"items": [{"content": "Setup memory", "importance": 1}]},
            timeout=LLM_TIMEOUT,
        )
        assert response.status_code in [200, 201]

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
        response = requests.post(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks/{TEST_BANK_ID}/memories",
            json={"items": [{"content": content, "importance": 5}]},
            timeout=LLM_TIMEOUT,
        )
        assert response.status_code in [200, 201]

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
        tags = concept.tags
        response = requests.post(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks/{TEST_BANK_ID}/memories",
            json={"items": [{"content": content, "tags": tags, "importance": 5}]},
            timeout=LLM_TIMEOUT,
        )
        assert response.status_code in [200, 201]


@pytest.mark.skipif(not HAS_LLM, reason="Requires LLM for consolidation")
class TestHindsightConsolidation:
    """Test Hindsight consolidation operations."""

    def setup_method(self):
        """Setup test bank with memories."""
        # Create bank by storing first memory
        requests.post(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks/{TEST_BANK_ID}/memories",
            json={"items": [{"content": "Setup memory", "importance": 1}]},
            timeout=LLM_TIMEOUT,
        )

        # Store some memories
        for i in range(5):
            requests.post(
                f"{HINDSIGHT_BASE_URL}/v1/default/banks/{TEST_BANK_ID}/memories",
                json={
                    "items": [
                        {
                            "content": f"Test memory {i} for consolidation",
                            "importance": 3,
                        }
                    ]
                },
                timeout=LLM_TIMEOUT,
            )

    def test_consolidate(self):
        """Test triggering consolidation."""
        response = requests.post(
            f"{HINDSIGHT_BASE_URL}/v1/default/banks/{TEST_BANK_ID}/consolidate",
            json={"mode": "full"},
            timeout=LLM_TIMEOUT,
        )
        # Consolidation may be async, so we just check it starts
        assert response.status_code in [200, 202]
