"""Shared test fixtures for Kyo MCP tests."""

import os
import shutil
import sys
from pathlib import Path

import pytest

# Ensure package is importable
pkg_dir = str(Path(__file__).parent.parent)
sys.path.insert(0, pkg_dir)


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path):
    """Each test gets a fresh tmpdir and a fresh database module."""
    os.environ["KYO_DATA_DIR"] = str(tmp_path)

    # Reload the database module so it picks up the new DB_PATH
    import importlib

    import kyo_mcp.database as db_mod

    importlib.reload(db_mod)
    db_mod.DB_PATH = str(tmp_path / "test_kyo_catalog.db")
    db_mod.conn = None
    db_mod.metadata = None
    db_mod.get_connection()
    yield

    shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.fixture
def db_path():
    """Return a path string; tests should use the autouse fixture instead."""
    return ""


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
