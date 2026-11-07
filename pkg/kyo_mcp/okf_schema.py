"""
Core types for kyo-mcp, modelling the Open Knowledge Format (OKF) v0.2.

Section references (§) are to docs/SPEC.md, vendored from
GoogleCloudPlatform/knowledge-catalog okf/SPEC.md. A concept document is
YAML frontmatter plus a markdown body (§4). `OKFConcept.to_markdown` and
`OKFConcept.from_markdown` convert between the two, and round-trip
losslessly: parse(to_markdown(c)) == c.
"""

import datetime
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

import yaml
from kyo_mcp.ontology import OntologyAnnotation
from pydantic import BaseModel, ConfigDict, field_validator

ConceptStatus = Literal["draft", "stable", "deprecated"]

# Frontmatter key carrying Kyo's own node id. OKF's concept ID is the file's
# path in a bundle (§2), so Kyo's opaque id travels as a producer extension
# key (§4.1) rather than claiming the meaning of an OKF id.
KYO_ID_KEY = "kyo_id"

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_instant(value: Any) -> Any:
    """Coerce a timestamp to the form §5 requires: an ISO 8601 datetime with
    an explicit UTC offset. A bare date becomes midnight UTC, and a naive
    datetime is read as UTC. Values that already carry an offset are kept
    exactly as written, so a conformant document round-trips byte-for-byte.
    Unparseable values are kept too: consumers must not reject a document
    over an optional field (§11)."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if _DATE_ONLY.match(text):
        return f"{text}T00:00:00Z"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return value
    return f"{text}Z" if parsed.tzinfo is None else text


class GeneratedInfo(BaseModel):
    """§5.2 `generated`: how the current content was produced."""

    model_config = ConfigDict(extra="allow")

    by: str  # an actor (§7), e.g. "kyo-mcp/0.2.0"
    at: Optional[str] = None  # ISO 8601 datetime with offset

    @field_validator("at")
    @classmethod
    def _normalize_at(cls, value: Any) -> Any:
        return normalize_instant(value)


class VerificationEntry(BaseModel):
    """§5.2 `verified`: a single verification event."""

    model_config = ConfigDict(extra="allow")

    by: str
    at: Optional[str] = None

    @field_validator("at")
    @classmethod
    def _normalize_at(cls, value: Any) -> Any:
        return normalize_instant(value)


class ProvenanceSource(BaseModel):
    """§5.1 `sources`: a material a concept derives from, with its optional
    credibility signals. Unknown keys on an entry are kept (§4.1)."""

    model_config = ConfigDict(extra="allow")

    id: Optional[str] = None
    resource: str
    title: Optional[str] = None
    author: Optional[str] = None
    usage_count: Optional[int] = None
    last_modified: Optional[str] = None

    @field_validator("last_modified")
    @classmethod
    def _normalize_last_modified(cls, value: Any) -> Any:
        return normalize_instant(value)


def trust_tier(verified: Any) -> str:
    """Derive the §5.3 trust tier from a concept's `verified` entries: any
    `human:` actor wins, any other verifier means machine-confirmed, and no
    verification at all means unverified. Accepts a bare mapping as a
    one-element list (§5.2)."""
    if isinstance(verified, dict):
        verified = [verified]
    entries = [v for v in verified or [] if isinstance(v, dict)]
    if any(str(v.get("by", "")).startswith("human:") for v in entries):
        return "Human-Reviewed"
    if entries:
        return "Machine-Confirmed"
    return "Unverified"


class _OKFLoader(yaml.SafeLoader):
    """SafeLoader that leaves timestamps as strings. PyYAML would otherwise
    turn an unquoted `2026-06-30T14:00:00Z` into a datetime, which neither
    validates as a string field nor serialises back to what was written."""


_OKFLoader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}

# Frontmatter opens with `---` on the first line and closes with the next
# line that is exactly `---` (§4).
_FRONTMATTER = re.compile(
    r"\A\ufeff?---[ \t]*\r?\n(?P<yaml>.*?)^---[ \t]*(?:\r?\n|\Z)(?P<body>.*)\Z",
    re.DOTALL | re.MULTILINE,
)


class OKFConcept(BaseModel):
    """One OKF v0.2 concept (§4). Frontmatter keys outside the fields below
    are kept in `metadata` and written back out unchanged (§4.1)."""

    # Kyo's node id; carried in frontmatter as `kyo_id` (see KYO_ID_KEY).
    id: Optional[str] = None

    # Required (§4.1)
    type: str

    # Recommended (§4.1)
    title: Optional[str] = None
    description: Optional[str] = None
    resource: Optional[Dict[str, str]] = None  # {"uri": "..."}; a bare URI in OKF
    tags: List[str] = []

    # Lifecycle and trust (§5)
    status: ConceptStatus = "stable"
    stale_after: Optional[str] = None
    generated: Optional[GeneratedInfo] = None
    verified: Optional[List[VerificationEntry]] = None
    sources: List[ProvenanceSource] = []

    # Every other frontmatter key: producer extensions, §10 computation
    # fields, `usage_window`, and so on.
    metadata: Dict[str, Any] = {}

    # Ontology annotations (Kyo extension)
    ontology: Optional[OntologyAnnotation] = None

    # The concept's OKF Concept ID (§2): its file path in a bundle, relative
    # to the bundle root, without the `.md` suffix. Kyo storage only, never
    # frontmatter: inside a bundle the file's location *is* the id.
    bundle_path: Optional[str] = None

    @field_validator("resource", mode="before")
    @classmethod
    def _resource_from_uri(cls, value: Any) -> Any:
        # OKF writes `resource` as a bare URI string (§4.1).
        if isinstance(value, str):
            return {"uri": value} if value else None
        return value

    @field_validator("verified", mode="before")
    @classmethod
    def _verified_as_list(cls, value: Any) -> Any:
        # §5.2: consumers MUST treat a bare mapping as a one-element list.
        # An empty list is the same as absent (unverified, §5.3); the
        # database stores it as [], a document omits the key.
        if isinstance(value, dict):
            return [value]
        return value or None

    @field_validator("tags", mode="before")
    @classmethod
    def _tags_as_list(cls, value: Any) -> Any:
        if value is None:
            return []
        return [value] if isinstance(value, str) else value

    @field_validator("status", mode="before")
    @classmethod
    def _status_default(cls, value: Any) -> Any:
        # §5.4: absent status means stable.
        return "stable" if value is None else value

    @field_validator("stale_after")
    @classmethod
    def _normalize_stale_after(cls, value: Any) -> Any:
        return normalize_instant(value)

    def to_frontmatter(self) -> Dict[str, Any]:
        """The concept's frontmatter as a dict, in OKF key order."""
        frontmatter: Dict[str, Any] = {"type": self.type}

        if self.title:
            frontmatter["title"] = self.title
        if self.description:
            frontmatter["description"] = self.description
        if self.resource and (self.resource.get("uri") or self.resource.get("url")):
            frontmatter["resource"] = self.resource.get("uri") or self.resource["url"]
        if self.tags:
            frontmatter["tags"] = list(self.tags)

        # Always written, even when "stable": absent and "stable" mean the
        # same (§5.4), and the database can't remember which one a source
        # file used, so writing it every time keeps output deterministic.
        frontmatter["status"] = self.status
        if self.stale_after:
            frontmatter["stale_after"] = self.stale_after
        if self.generated:
            frontmatter["generated"] = self.generated.model_dump(exclude_none=True)
        if self.verified:
            frontmatter["verified"] = [
                v.model_dump(exclude_none=True) for v in self.verified
            ]
        if self.sources:
            frontmatter["sources"] = [
                s.model_dump(exclude_none=True) for s in self.sources
            ]

        if self.id:
            frontmatter[KYO_ID_KEY] = self.id
        if self.ontology:
            frontmatter["ontology"] = self.ontology.model_dump(exclude_none=True)

        # Extensions are kept but must not shadow the keys above.
        for key, value in self.metadata.items():
            frontmatter.setdefault(key, value)
        return frontmatter

    def default_body(self) -> str:
        """The minimal body used when a concept has none of its own."""
        body = f"# {self.title or 'Untitled'}\n"
        if self.description:
            body += f"\n{self.description}\n"
        return body

    def to_markdown(self, body: str = "") -> str:
        """Serialize the concept as an OKF concept document (§4).

        `body` is the concept's markdown body. When empty, a minimal body is
        generated from the title and description instead.
        """
        return render_document(self.to_frontmatter(), body or self.default_body())

    @classmethod
    def from_frontmatter(cls, frontmatter: Dict[str, Any]) -> "OKFConcept":
        """Build a concept from parsed frontmatter, moving every key that
        isn't a modelled field into `metadata` (§4.1)."""
        if not isinstance(frontmatter, dict):
            raise ValueError("OKF frontmatter must be a YAML mapping")
        # bundle_path is storage-only (see the field), so a frontmatter key
        # of that name is an ordinary extension.
        modelled = set(cls.model_fields) - {"id", "metadata", "bundle_path"}
        data: Dict[str, Any] = {}
        extras: Dict[str, Any] = {}
        for key, value in frontmatter.items():
            if key == KYO_ID_KEY:
                data["id"] = None if value is None else str(value)
            elif key in modelled:
                data[key] = value
            else:
                extras[key] = value
        data["metadata"] = extras
        return cls.model_validate(data)

    @classmethod
    def from_markdown(cls, text: str) -> Tuple["OKFConcept", str]:
        """Parse an OKF concept document into (concept, body).

        Raises ValueError when there is no frontmatter block, and pydantic's
        ValidationError when `type`, the one required key (§4.1), is missing.
        """
        if not _FRONTMATTER.match(text):
            raise ValueError("Not an OKF concept document: no frontmatter block")
        frontmatter, body = parse_frontmatter(text)
        return cls.from_frontmatter(frontmatter or {}), body


def render_document(frontmatter: Dict[str, Any], body: str) -> str:
    """Render frontmatter and a body as an OKF markdown document (§4)."""
    dumped = yaml.safe_dump(
        frontmatter, default_flow_style=False, sort_keys=False, allow_unicode=True
    )
    return f"---\n{dumped}---\n{body}"


def _join_surrogates(value: Any) -> Any:
    r"""Recombine UTF-16 surrogate pairs in loaded strings. JSON-style
    escapes such as "\ud83d\ude00" (how json.dumps writes an emoji) are
    common in machine-written YAML, and PyYAML decodes each half as a lone
    surrogate: a str that can't be encoded to UTF-8 or stored. A lone
    surrogate with no partner becomes U+FFFD."""
    if isinstance(value, str):
        if any("\ud800" <= ch <= "\udfff" for ch in value):
            return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
        return value
    if isinstance(value, list):
        return [_join_surrogates(v) for v in value]
    if isinstance(value, dict):
        return {_join_surrogates(k): _join_surrogates(v) for k, v in value.items()}
    return value


def parse_frontmatter(text: str) -> Tuple[Optional[Any], str]:
    """Split a document into (parsed frontmatter, body). Frontmatter is None
    when the document has no frontmatter block. Raises yaml.YAMLError when
    the block isn't valid YAML."""
    match = _FRONTMATTER.match(text)
    if not match:
        return None, text
    loaded = yaml.load(match.group("yaml"), Loader=_OKFLoader)
    return _join_surrogates(loaded), match.group("body")
