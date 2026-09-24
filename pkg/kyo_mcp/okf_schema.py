"""
Core types for kyo-mcp, strictly enforcing the Open Knowledge Format (OKF) v0.2 standard.
Aligns exactly with the source-of-truth Markdown structure defined by Google's knowledge-catalog SPEC.md.
"""

from typing import Any, Dict, List, Literal, Optional

from kyo_mcp.ontology import OntologyAnnotation
from pydantic import BaseModel

ConceptStatus = Literal["draft", "stable", "deprecated"]


class GeneratedInfo(BaseModel):
    """§5.2 `generated`: Records how the current content was produced."""

    by: str  # e.g., "process:kyo-mcp"
    at: str  # ISO 8601 timestamp


class VerificationEntry(BaseModel):
    """§5.2 `verified`: List of individual verification events."""

    by: str
    at: str


class ProvenanceSource(BaseModel):
    """§5.1 `sources`: A material a concept derives from."""

    id: Optional[str] = None
    resource: str
    title: Optional[str] = None
    author: Optional[str] = None


def trust_tier(verified: Optional[List[Dict[str, Any]]]) -> str:
    """Derive the OKF §5.3 trust tier from a concept's `verified` entries:
    any human verification wins, then any process verification, else the
    concept is unverified."""
    actors = [v.get("by", "") for v in verified or []]
    if any(a.startswith("human:") for a in actors):
        return "Human-Reviewed"
    if any(a.startswith("process:") for a in actors):
        return "Machine-Confirmed"
    return "Unverified"


class OKFConcept(BaseModel):
    """The standard conceptual structure for an OKF v0.2 bundle file."""

    # Required fields
    id: Optional[str] = None
    type: str

    # Recommended fields
    title: Optional[str] = None
    description: Optional[str] = None

    # Resource and Tags
    resource: Optional[Dict[str, str]] = None  # {"uri": "..."}
    tags: List[str] = []

    # Lifecycle and Trust
    status: ConceptStatus = "stable"
    stale_after: Optional[str] = None  # ISO 8601 date or timestamp
    generated: Optional[GeneratedInfo] = None
    verified: Optional[List[VerificationEntry]] = None
    sources: List[ProvenanceSource] = []
    metadata: Dict[str, Any] = {}

    # Ontology annotations (lightweight, optional)
    ontology: Optional[OntologyAnnotation] = None

    def to_markdown(self, body: str = "") -> str:
        """Serializes the concept into an OKF-compliant Markdown bundle file.

        `body` is the concept's markdown body. When empty, a minimal body is
        generated from the title and description instead.
        """
        import yaml

        # Required: type
        frontmatter: Dict[str, Any] = {"type": self.type}

        # Recommended
        if self.title:
            frontmatter["title"] = self.title
        if self.description:
            frontmatter["description"] = self.description
        if self.resource and self.resource.get("uri"):
            frontmatter["resource"] = self.resource["uri"]
        if self.tags:
            frontmatter["tags"] = self.tags

        # Lifecycle / Trust (strict v0.2 structures)
        if self.status != "stable":
            frontmatter["status"] = self.status
        if self.stale_after:
            frontmatter["stale_after"] = self.stale_after
        if self.generated:
            frontmatter["generated"] = self.generated.model_dump()
        if self.verified:
            frontmatter["verified"] = [v.model_dump() for v in self.verified]
        if self.sources:
            frontmatter["sources"] = [
                s.model_dump(exclude_none=True) for s in self.sources
            ]

        # Extra keys are allowed by the spec, but must not shadow the
        # standard ones above.
        for key, value in self.metadata.items():
            frontmatter.setdefault(key, value)

        if not body:
            body = f"# {self.title or 'Untitled'}\n"
            if self.description:
                body += f"\n{self.description}\n"

        dumped = yaml.safe_dump(frontmatter, default_flow_style=False, sort_keys=False)
        return f"---\n{dumped}---\n{body}"
