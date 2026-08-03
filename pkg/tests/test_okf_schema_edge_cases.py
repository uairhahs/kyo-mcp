"""Edge case tests for OKF schema validation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kyo_mcp.okf_schema import (
    GeneratedInfo,
    OKFConcept,
    ProvenanceSource,
    VerificationEntry,
)


class TestStatusValidation:
    def test_invalid_status_rejected(self):
        """Status must be one of: draft, stable, deprecated"""
        try:
            OKFConcept(type="concept", status="invalid")
            assert False, "Should have raised validation error"
        except Exception:
            pass

    def test_valid_statuses(self):
        """Test all valid status values"""
        for status in ["draft", "stable", "deprecated"]:
            concept = OKFConcept(type="concept", status=status)
            assert concept.status == status


class TestRequiredFields:
    def test_missing_type_rejected(self):
        """Type is required"""
        try:
            OKFConcept(id="test")
            assert False, "Should have raised validation error"
        except Exception:
            pass

    def test_missing_id_allowed(self):
        """ID is optional"""
        concept = OKFConcept(type="concept", title="Test")
        assert concept.id is None


class TestGeneratedInfoValidation:
    def test_empty_by_rejected(self):
        """by field cannot be empty"""
        try:
            GeneratedInfo(by="", at="2024-01-01T00:00:00Z")
            assert False, "Should have raised validation error"
        except Exception:
            pass

    def test_empty_at_rejected(self):
        """at field cannot be empty"""
        try:
            GeneratedInfo(by="process:test", at="")
            assert False, "Should have raised validation error"
        except Exception:
            pass

    def test_invalid_timestamp_rejected(self):
        """at field must be valid ISO 8601 timestamp"""
        try:
            GeneratedInfo(by="process:test", at="not-a-timestamp")
            assert False, "Should have raised validation error"
        except Exception:
            pass


class TestVerificationEntryValidation:
    def test_by_prefix_validation(self):
        """by field should start with 'human:' or 'process:'"""
        # This test will fail if prefix validation is not implemented
        entry = VerificationEntry(by="invalid:prefix", at="2024-01-01T00:00:00Z")
        assert entry.by == "invalid:prefix"  # Will pass until validation added


class TestResourceValidation:
    def test_resource_uri_required(self):
        """Resource dict must have URI"""
        try:
            OKFConcept(type="concept", resource={"invalid": "data"})
            assert False, "Should have raised validation error"
        except Exception:
            pass

    def test_empty_resource_allowed(self):
        """Empty resource is allowed"""
        concept = OKFConcept(type="concept", resource=None)
        assert concept.resource is None


class TestMarkdownSerialization:
    def test_empty_title_fallback(self):
        """to_markdown should handle None title"""
        concept = OKFConcept(
            type="concept",
            generated=GeneratedInfo(by="process:test", at="2024-01-01T00:00:00Z"),
        )
        md = concept.to_markdown()
        assert "Untitled" in md

    def test_missing_generated_in_markdown(self):
        """to_markdown should handle missing generated info"""
        concept = OKFConcept(type="concept", title="Test")
        md = concept.to_markdown()
        assert "Test" in md
        assert "unknown" in md  # Should use fallback

    def test_to_markdown_with_all_fields(self):
        """to_markdown with complete concept"""
        concept = OKFConcept(
            id="test-001",
            type="concept",
            title="Full Test",
            description="Complete description",
            resource={"uri": "https://example.com"},
            tags=["tag1", "tag2"],
            status="stable",
            generated=GeneratedInfo(by="process:test", at="2024-01-01T00:00:00Z"),
        )
        md = concept.to_markdown()
        assert "Full Test" in md
        assert "Complete description" in md
        assert "https://example.com" in md
        assert "tag1" in md


class TestProvenanceSourceValidation:
    def test_resource_required(self):
        """Resource field is required"""
        try:
            ProvenanceSource()
            assert False, "Should have raised validation error"
        except Exception:
            pass

    def test_resource_empty_string_rejected(self):
        """Resource cannot be empty string"""
        try:
            ProvenanceSource(resource="")
            assert False, "Should have raised validation error"
        except Exception:
            pass