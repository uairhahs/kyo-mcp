"""
Lightweight ontology annotations for OKF v0.2.

Provides minimal ontology support without requiring RDFLib dependency.
Maps OKF types to classes that actually exist in standard vocabularies
(SKOS, DCMI Metadata Terms, DCMI Type Vocabulary) and exports concepts as
Turtle.
"""

from typing import Dict, Iterable, List, Optional
from urllib.parse import quote

from pydantic import BaseModel

PREFIXES = {
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dc": "http://purl.org/dc/terms/",
    "dcmitype": "http://purl.org/dc/dcmitype/",
}


class OntologyAnnotation(BaseModel):
    """Lightweight ontology annotation for OKF concepts."""

    scheme: str  # "SKOS", "DublinCore", "custom"
    concept_uri: str  # e.g., "skos:Concept", "dc:Agent"
    in_scheme: Optional[str] = None  # IRI of the skos:ConceptScheme, if any


# Standard ontology mappings. OKF types with no real class in a vocabulary
# are left out rather than invented (SKOS, for example, only defines
# Concept, ConceptScheme, and Collection).
ONTOLOGY_SCHEMES: Dict[str, Dict[str, str]] = {
    "SKOS": {
        "concept": "skos:Concept",
    },
    "DublinCore": {
        "concept": "dc:BibliographicResource",
        "dataset": "dcmitype:Dataset",
        "event": "dcmitype:Event",
        "service": "dcmitype:Service",
        "software": "dcmitype:Software",
        "agent": "dc:Agent",
    },
}

DEFAULT_CLASS = "skos:Concept"


# OKF type to ontology URI mapping
def map_type_to_ontology(okf_type: str, scheme: str = "SKOS") -> Optional[str]:
    """Map OKF type to ontology URI."""
    if scheme not in ONTOLOGY_SCHEMES:
        return None

    return ONTOLOGY_SCHEMES[scheme].get(okf_type)


def create_ontology_annotation(
    okf_type: str, scheme: str = "SKOS", in_scheme: Optional[str] = None
) -> Optional[OntologyAnnotation]:
    """Create ontology annotation for an OKF type."""
    uri = map_type_to_ontology(okf_type, scheme)
    if not uri:
        return None

    return OntologyAnnotation(scheme=scheme, concept_uri=uri, in_scheme=in_scheme)


def concept_iri(concept_id: str) -> str:
    """IRI for a Kyo concept. Concept ids are arbitrary strings, so they are
    percent-encoded into a URN rather than used as prefixed local names."""
    return f"<urn:kyo:{quote(concept_id, safe='-._~')}>"


def relation_iri(relation_type: str) -> str:
    return f"<urn:kyo:relation:{quote(relation_type, safe='-._~')}>"


def turtle_literal(value: str) -> str:
    """Quote a string as a Turtle literal, escaping per the Turtle grammar."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _class_for(concept_type: str, ontology: Optional[OntologyAnnotation]) -> str:
    if ontology:
        return ontology.concept_uri
    for scheme in ("SKOS", "DublinCore"):
        mapped = map_type_to_ontology(concept_type, scheme)
        if mapped:
            return mapped
    return DEFAULT_CLASS


def export_to_rdf_turtle(
    concept_id: str,
    concept_type: str,
    title: str,
    description: str = "",
    ontology: Optional[OntologyAnnotation] = None,
    tags: Iterable[str] = (),
    links: Iterable[Dict[str, str]] = (),
) -> str:
    """
    Export OKF concept to Turtle RDF format.

    Args:
        concept_id: Unique identifier for the concept
        concept_type: OKF type (concept, event, agent, ...)
        title: Human-readable title
        description: Optional description
        ontology: Optional ontology annotation overriding the type mapping
        tags: Optional tags, exported as dc:subject
        links: Optional outgoing links, as dicts with target_id and
            relation_type (the shape database.get_links returns)

    Returns:
        Turtle RDF string
    """
    prefixes = "".join(f"@prefix {p}: <{iri}> .\n" for p, iri in PREFIXES.items())

    statements: List[str] = [
        f"a {_class_for(concept_type, ontology)}",
        f"skos:prefLabel {turtle_literal(title)}",
        f"dc:identifier {turtle_literal(concept_id)}",
        f"dc:type {turtle_literal(concept_type)}",
    ]
    if description:
        statements.append(f"skos:definition {turtle_literal(description)}")
    if ontology and ontology.in_scheme:
        statements.append(f"skos:inScheme <{ontology.in_scheme}>")
    for tag in tags:
        statements.append(f"dc:subject {turtle_literal(tag)}")
    for link in links:
        statements.append(
            f"{relation_iri(link['relation_type'])} {concept_iri(link['target_id'])}"
        )

    body = " ;\n    ".join(statements)
    return f"{prefixes}\n{concept_iri(concept_id)} {body} .\n"
