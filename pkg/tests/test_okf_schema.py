"""Tests for OKF schema models."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kyo_mcp.okf_schema import (
    GeneratedInfo,
    OKFConcept,
    ProvenanceSource,
    VerificationEntry,
)


class TestGeneratedInfo:
    def test_create(self):
        info = GeneratedInfo(by="test:process", at="2024-01-01T00:00:00Z")
        assert info.by == "test:process"
        assert info.at == "2024-01-01T00:00:00Z"

    def test_model_dump(self):
        info = GeneratedInfo(by="process:kyo", at="2024-01-01T00:00:00Z")
        dumped = info.model_dump(mode="python")
        assert dumped["by"] == "process:kyo"


class TestVerificationEntry:
    def test_create(self):
        entry = VerificationEntry(by="human:alice", at="2024-01-01T00:00:00Z")
        assert entry.by == "human:alice"


class TestProvenanceSource:
    def test_create_source(self):
        source = ProvenanceSource(
            id="source-1",
            resource="https://example.com/doc",
            title="Test Document",
            author="Alice",
        )
        assert source.id == "source-1"
        assert source.resource == "https://example.com/doc"

    def test_optional_fields(self):
        source = ProvenanceSource(resource="https://example.com/doc")
        assert source.id is None
        assert source.title is None


class TestOKFConcept:
    def test_create_concept(self):
        concept = OKFConcept(
            id="test-001",
            type="concept",
            title="Test Concept",
            description="A test concept",
            generated=GeneratedInfo(by="process:test", at="2024-01-01T00:00:00Z"),
        )
        assert concept.id == "test-001"
        assert concept.type == "concept"

    def test_default_values(self):
        concept = OKFConcept(type="concept")
        assert concept.status == "stable"
        assert concept.tags == []
        assert concept.sources == []

    def test_to_markdown(self):
        concept = OKFConcept(
            type="concept",
            title="Test Concept",
            generated=GeneratedInfo(by="process:test", at="2024-01-01T00:00:00Z"),
        )
        md = concept.to_markdown()
        assert "Test Concept" in md
        assert "process:test" in md

    def test_model_dump(self):
        concept = OKFConcept(
            id="test-001",
            type="concept",
            title="Test",
            generated=GeneratedInfo(by="process:test", at="2024-01-01T00:00:00Z"),
        )
        dumped = concept.model_dump(mode="python")
        assert dumped["id"] == "test-001"
        assert dumped["type"] == "concept"
