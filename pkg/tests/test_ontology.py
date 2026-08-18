"""Tests for lightweight ontology annotations."""

from kyo_mcp.ontology import (
    OntologyAnnotation,
    create_ontology_annotation,
    export_to_rdf_turtle,
    map_type_to_ontology,
)


class TestOntologyAnnotation:
    """Test ontology annotation model."""

    def test_create_annotation(self):
        """Test creating an ontology annotation."""
        annotation = OntologyAnnotation(
            scheme="SKOS",
            concept_uri="skos:Concept",
        )
        assert annotation.scheme == "SKOS"
        assert annotation.concept_uri == "skos:Concept"
        assert annotation.in_scheme is None

    def test_annotation_with_scheme(self):
        """Test annotation with in_scheme."""
        annotation = OntologyAnnotation(
            scheme="SKOS",
            concept_uri="skos:Concept",
            in_scheme="https://www.w3.org/2004/02/skos/core#skos",
        )
        assert annotation.in_scheme is not None


class TestTypeMapping:
    """Test OKF type to ontology mapping."""

    def test_map_concept_to_skos(self):
        """Test mapping 'concept' type to SKOS."""
        uri = map_type_to_ontology("concept", "SKOS")
        assert uri == "skos:Concept"

    def test_map_event_to_skos(self):
        """Test mapping 'event' type to SKOS."""
        uri = map_type_to_ontology("event", "SKOS")
        assert uri == "skos:Event"

    def test_map_unknown_type(self):
        """Test mapping unknown type returns None."""
        uri = map_type_to_ontology("unknown", "SKOS")
        assert uri is None

    def test_map_unknown_scheme(self):
        """Test mapping with unknown scheme returns None."""
        uri = map_type_to_ontology("concept", "Unknown")
        assert uri is None


class TestCreateAnnotation:
    """Test creating ontology annotations."""

    def test_create_skos_annotation(self):
        """Test creating SKOS annotation."""
        annotation = create_ontology_annotation("concept", "SKOS")
        assert annotation is not None
        assert annotation.scheme == "SKOS"
        assert annotation.concept_uri == "skos:Concept"

    def test_create_dublin_core_annotation(self):
        """Test creating Dublin Core annotation."""
        annotation = create_ontology_annotation("concept", "DublinCore")
        assert annotation is not None
        assert annotation.scheme == "DublinCore"
        assert annotation.concept_uri == "dc:BibliographicResource"

    def test_create_unknown_type(self):
        """Test creating annotation for unknown type."""
        annotation = create_ontology_annotation("unknown", "SKOS")
        assert annotation is None


class TestRDFExport:
    """Test RDF export functionality."""

    def test_export_basic(self):
        """Test basic RDF export."""
        rdf = export_to_rdf_turtle(
            concept_id="test-1",
            concept_type="concept",
            title="Test Concept",
        )
        assert "skos:Concept" in rdf
        assert "Test Concept" in rdf
        assert "test-1" in rdf

    def test_export_with_description(self):
        """Test RDF export with description."""
        rdf = export_to_rdf_turtle(
            concept_id="test-2",
            concept_type="concept",
            title="Test",
            description="A test concept",
        )
        assert "A test concept" in rdf

    def test_export_with_ontology(self):
        """Test RDF export with custom ontology."""
        ontology = OntologyAnnotation(
            scheme="DublinCore",
            concept_uri="dc:BibliographicResource",
        )
        rdf = export_to_rdf_turtle(
            concept_id="test-3",
            concept_type="concept",
            title="Test",
            ontology=ontology,
        )
        assert "dc:BibliographicResource" in rdf
        assert "dc:" in rdf
