"""Shared test fixtures for Kyo MCP tests."""

import sys
from pathlib import Path

import pytest

# Ensure package is importable
pkg_dir = str(Path(__file__).parent.parent)
sys.path.insert(0, pkg_dir)


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):
    """Each test gets a fresh database in its own tmpdir."""
    import kyo_mcp.database as db_mod

    db_mod.close_connections()
    monkeypatch.setattr(db_mod, "DB_PATH", None)
    monkeypatch.delenv("KYO_DATA_DIR", raising=False)
    monkeypatch.setenv("KYO_DB_PATH", str(tmp_path / "test_kyo_catalog.db"))
    yield tmp_path
    db_mod.close_connections()


@pytest.fixture
def sample_concept():
    """Return a minimal OKF concept dict."""
    return {
        "id": "test-node-001",
        "type": "concept",
        "title": "Test Concept",
        "description": "A test concept for unit testing",
        "resource": {"uri": "https://example.com/test"},
        "tags": ["test", "unit"],
        "status": "stable",
        "generated": {"by": "test:process", "at": "2024-01-01T00:00:00Z"},
    }
