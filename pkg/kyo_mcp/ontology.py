"""
Lightweight ontology annotations for OKF v0.2.

Provides minimal ontology support without requiring RDFLib dependency.
Maps OKF types to standard ontology concepts (SKOS, Dublin Core).
"""

from typing import Optional

from pydantic import BaseModel


class OntologyAnnotation(BaseModel):
    """Lightweight ontology annotation for OKF concepts."""

    scheme: str  # "SKOS", "DublinCore", "custom"
    concept_uri: str  # e.g., "skos:Concept", "dc:Agent"
    in_scheme: Optional[str] = None


# Standard ontology mappings
ONTOLOGY_SCHEMES = {
    "SKOS": {
        "concept": "skos:Concept",
        "event": "skos:Event",
        "process": "skos:Process",
        "agent": "skos:Agent",
    },
    "DublinCore": {
        "concept": "dc:BibliographicResource",
        "event": "dc:Event",
        "process": "dc:Process",
        "agent": "dc:Agent",
    },
}


# OKF type to ontology URI mapping
def map_type_to_ontology(okf_type: str, scheme: str = "SKOS") -> Optional[str]:
    """Map OKF type to ontology URI."""
    if scheme not in ONTOLOGY_SCHEMES:
        return None

    return ONTOLOGY_SCHEMES[scheme].get(okf_type)


def create_ontology_annotation(
    okf_type: str, scheme: str = "SKOS"
) -> Optional[OntologyAnnotation]:
    """Create ontology annotation for an OKF type."""
    uri = map_type_to_ontology(okf_type, scheme)
    if not uri:
        return None

    return OntologyAnnotation(
        scheme=scheme,
        concept_uri=uri,
        in_scheme=(
            f"https://www.w3.org/2004/02/skos/core#{scheme.lower()}"
            if scheme == "SKOS"
            else None
        ),
    )


def export_to_rdf_turtle(
    concept_id: str,
    concept_type: str,
    title: str,
    description: str = "",
    ontology: Optional[OntologyAnnotation] = None,
) -> str:
    """
    Export OKF concept to Turtle RDF format.

    Args:
        concept_id: Unique identifier for the concept
        concept_type: OKF type (concept, event, process, agent)
        title: Human-readable title
        description: Optional description
        ontology: Optional ontology annotation

    Returns:
        Turtle RDF string
    """
    # Determine ontology URI
    if ontology:
        ontology_uri = ontology.concept_uri
        prefix = ontology.scheme[0].lower()
    else:
        ontology_uri = "skos:Concept"
        prefix = "skos"

    # Build Turtle output
    turtle = f"""
@prefix {prefix}: <https://www.w3.org/2004/02/skos/core#> .
@prefix dc: <http://purl.org/dc/terms/> .
@prefix kyo: <kyo:{concept_id}> .

kyo:{concept_id} a {ontology_uri} ;
    {prefix}:prefLabel "{title}" ;
    dc:identifier "{concept_id}" .
"""

    if description:
        turtle += f'    {prefix}:definition "{description}" .\n'

    return turtle
