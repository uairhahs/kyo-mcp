"""Tests for lazy database initialization - only creates DB when MCP starts."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path):
    """Each test gets a fresh tmpdir."""
    import os

    os.environ["KYO_DATA_DIR"] = str(tmp_path)
    yield
    import shutil

    shutil.rmtree(tmp_path, ignore_errors=True)


class TestLazyDBInit:
    """Test that DB is only created when MCP server starts."""

    def test_db_created_on_first_connection(self):
        """DB should be created when get_connection() is first called."""
        import importlib

        import kyo_mcp.database as db_mod

        importlib.reload(db_mod)

        db_path = Path(db_mod.DB_PATH)
        conn = db_mod.get_connection()

        assert db_path.exists(), "DB should exist after get_connection()"
        assert conn is not None

    def test_db_persists_across_calls(self):
        """DB should persist - same connection returned on subsequent calls."""
        import importlib

        import kyo_mcp.database as db_mod

        importlib.reload(db_mod)

        db_path = Path(db_mod.DB_PATH)
        conn1 = db_mod.get_connection()
        conn2 = db_mod.get_connection()

        assert conn1 is conn2, "Should return same connection instance"
        assert db_path.exists()

    def test_db_uses_correct_path(self):
        """DB should be created at the configured DB_PATH."""
        import importlib

        import kyo_mcp.database as db_mod

        importlib.reload(db_mod)

        original_path = Path(db_mod.DB_PATH)
        conn = db_mod.get_connection()

        # Verify DB was created at the exact path
        assert original_path.exists()
        assert original_path.is_file()

    def test_tables_created_on_init(self):
        """Database tables should be created on first connection."""
        import importlib

        import kyo_mcp.database as db_mod

        importlib.reload(db_mod)

        db_mod.get_connection()

        # Check tables exist
        from sqlite3 import connect

        db_path = Path(db_mod.DB_PATH)
        with connect(str(db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('knowledge_concepts', 'knowledge_links')"
            )
            tables = {row[0] for row in cursor.fetchall()}
            assert "knowledge_concepts" in tables
            assert "knowledge_links" in tables


class TestDBFilePath:
    """Test that DB file is not in package directory."""

    def test_db_not_in_package_dir(self):
        """DB should not be created in the source package directory."""
        import importlib

        import kyo_mcp.database as db_mod

        importlib.reload(db_mod)

        # The DB_PATH should NOT be in the source directory
        from pathlib import Path

        db_path = Path(db_mod.DB_PATH)

        # Check it's not in the package directory
        package_dir = Path(__file__).parent.parent / "src" / "kyo_mcp"
        assert not db_path.is_relative_to(
            package_dir
        ), f"DB path {db_path} should not be relative to package dir {package_dir}"

    def test_db_in_user_data_dir(self):
        """DB should be in user data directory (not package)."""
        import importlib

        import kyo_mcp.database as db_mod

        importlib.reload(db_mod)

        db_path = Path(db_mod.DB_PATH)

        # DB path should be absolute (not relative)
        assert db_path.is_absolute(), "DB path should be absolute"

        # Should not contain 'src' or 'kyo_mcp' in path
        path_str = str(db_path)
        assert (
            "src" not in path_str.lower()
        ), f"Path should not contain 'src': {path_str}"
        assert (
            "kyo_mcp" not in path_str.lower()
        ), f"Path should not contain 'kyo_mcp': {path_str}"
