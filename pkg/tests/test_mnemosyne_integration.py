"""Tests for Mnemosyne integration with OKF v0.2."""

from kyo_mcp.okf_schema import OKFConcept


class TestMnemosyneConnection:
    """Test Mnemosyne connection and basic operations."""

    def test_import_mnemosyne(self):
        """Test that Mnemosyne can be imported."""
        import mnemosyne

        # Verify package is importable
        assert mnemosyne is not None

    def test_mnemosyne_cli_available(self):
        """Test that Mnemosyne CLI is available."""
        import subprocess

        result = subprocess.run(["mnemosyne", "--help"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "Mnemosyne - Local AI Memory System" in result.stdout


class TestOKFMnemosyneSync:
    """Test syncing OKF concepts to Mnemosyne via CLI."""

    def test_sync_concept_to_mnemosyne(self):
        """Test syncing an OKF concept to Mnemosyne."""
        import subprocess

        concept = OKFConcept(
            id="test-concept-1",
            type="concept",
            title="Test Concept",
            description="A test concept for Mnemosyne integration",
        )

        # Sync to Mnemosyne via CLI
        content = f"{concept.title}: {concept.description}"
        result = subprocess.run(
            ["mnemosyne", "store", content], capture_output=True, text=True
        )
        assert result.returncode == 0

    def test_sync_concept_with_metadata(self):
        """Test syncing an OKF concept with metadata to Mnemosyne."""
        import subprocess

        concept = OKFConcept(
            id="test-concept-2",
            type="concept",
            title="NixOS Configuration",
            description="NixOS uses declarative configuration",
            tags=["nixos", "configuration"],
        )

        # Sync via CLI with importance
        content = f"{concept.title}: {concept.description}"
        result = subprocess.run(
            ["mnemosyne", "store", content, "kyo", "5"], capture_output=True, text=True
        )
        assert result.returncode == 0


class TestMnemosyneConsolidation:
    """Test Mnemosyne consolidation (sleep)."""

    def test_trigger_consolidation(self):
        """Test triggering Mnemosyne consolidation."""
        import subprocess

        # Trigger consolidation via CLI
        result = subprocess.run(["mnemosyne", "sleep"], capture_output=True, text=True)
        assert result.returncode == 0


class TestOKFConceptExtensions:
    """Test OKF Concept extensions for Mnemosyne."""

    def test_concept_creation(self):
        """Test creating an OKF concept."""
        concept = OKFConcept(
            id="test-1",
            type="concept",
            title="Test",
            description="Test description",
        )

        assert concept.id == "test-1"
        assert concept.type == "concept"
        assert concept.title == "Test"
        assert concept.description == "Test description"

    def test_concept_defaults(self):
        """Test default values for OKF concept."""
        concept = OKFConcept(type="concept")

        assert concept.status == "stable"
        assert concept.tags == []
        assert concept.sources == []
        assert concept.metadata == {}
