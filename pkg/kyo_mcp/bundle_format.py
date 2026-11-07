"""OKF v0.2 bundle conventions shared by the store and the bundle tools:
concept paths and ids, links, and Kyo's generated links section.

Section references (§) are to docs/SPEC.md. A concept's OKF id is its file
path in the bundle minus `.md` (§2), concepts link with standard markdown
links (§6.1), and `index.md` / `log.md` are reserved (§3.1).

OKF links are untyped (§6.1), so Kyo's typed links travel as a
`kyo_links` extension key (§4.1) and, so that plain OKF consumers still
see the edge, as an ordinary relative link in a generated section between
LINKS_START and LINKS_END. Reading a file strips that section again.
"""

import posixpath
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from urllib.parse import unquote

OKF_VERSION = "0.2"
RESERVED_FILENAMES = frozenset({"index.md", "log.md"})  # §3.1
KYO_LINKS_KEY = "kyo_links"
DEFAULT_RELATION = "references"
LINKS_START = "<!-- kyo:links -->"
LINKS_END = "<!-- /kyo:links -->"

# Fixed namespace for path-derived ids. Never change it: every device must
# derive the same id from the same path.
_ID_NAMESPACE = uuid.UUID("6f6b662d-6b79-6f2d-6964-2d7061746821")

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
# Export appends "\n" + the block to a body ending in "\n"; removing exactly
# that restores the original body.
_LINKS_BLOCK = re.compile(
    r"\n" + re.escape(LINKS_START) + r"\n.*?\n" + re.escape(LINKS_END) + r"\n",
    re.DOTALL,
)
_FENCED_CODE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.DOTALL | re.MULTILINE)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
# [text](href) or [text](href "title"), but not an image ![alt](src).
_MARKDOWN_LINK = re.compile(
    r"(?<!!)\[(?:[^\]\\\n]|\\.)*\]\(\s*<?([^)\s>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
)
_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


# --- Identity and paths ------------------------------------------------------


def path_derived_id(bundle_path: str) -> str:
    """The id of a concept file that carries no `kyo_id`: derived from its
    path, so every device indexing the same bundle agrees on it, and it
    survives edits. A file that moves without a `kyo_id` is a new concept,
    as in OKF itself, where the path is the identity (§2)."""
    return f"kyo-{uuid.uuid5(_ID_NAMESPACE, bundle_path).hex[:12]}"


def is_valid_kyo_id(value: object) -> bool:
    return isinstance(value, str) and bool(_ID_PATTERN.match(value))


def slugify(text: str) -> str:
    """A lowercase, filesystem-safe path segment ("" if nothing survives)."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80].strip("-")


def is_valid_bundle_path(path: Optional[str]) -> bool:
    """A concept id (§2): relative, no `..`, no `.md`, not a reserved name,
    and no hidden segment (those are skipped when scanning)."""
    if not path or path.startswith("/") or path.endswith(".md"):
        return False
    parts = path.split("/")
    if any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        return False
    return f"{parts[-1]}.md".lower() not in RESERVED_FILENAMES


def suggested_path(
    concept_type: Optional[str], title: Optional[str], node_id: str
) -> str:
    """`<type>/<title>` as slugs, for a concept that has no path yet."""
    folder = slugify(concept_type or "") or "concepts"
    name = slugify(title or "") or slugify(node_id) or "concept"
    return f"{folder}/{name}"


def concept_files(root: Path) -> Iterable[Tuple[Path, str]]:
    """(file, concept path) for every concept document in a bundle,
    skipping reserved files (§3.1), hidden files and directories (such as
    .git, or a sync tool's temporary files), and non-markdown files."""
    for file in sorted(root.rglob("*.md")):
        rel = file.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if file.name.lower() in RESERVED_FILENAMES or not file.is_file():
            continue
        yield file, rel.with_suffix("").as_posix()


# --- Links -------------------------------------------------------------------


def body_link_targets(body: str) -> List[str]:
    """Every markdown link target in a body, ignoring code."""
    text = _INLINE_CODE.sub("", _FENCED_CODE.sub("", body))
    return [m.group(1) for m in _MARKDOWN_LINK.finditer(text)]


def resolve_link(href: str, from_path: str) -> Optional[str]:
    """The concept path an in-bundle link points at (§6.1), or None for an
    external link, a non-concept target, or one that leaves the bundle."""
    href = unquote(href.split("#", 1)[0].split("?", 1)[0])
    if not href or _URL_SCHEME.match(href) or not href.endswith(".md"):
        return None
    if href.startswith("/"):
        target = posixpath.normpath(href.lstrip("/"))
    else:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), href))
    if target.startswith("..") or target in (".", ""):
        return None
    return target[: -len(".md")]


def relative_link(target_path: str, from_path: str) -> str:
    """A relative markdown link (§6.1) from one concept to another. The
    spec recommends the absolute `/...` form, but Google's reference
    viewer ignores absolute links (okf/src/reference_agent/viewer/
    generator.py, `_extract_links`), so the relative form reaches more
    consumers. Both resolve to the same concept."""
    start = posixpath.dirname(from_path) or "."
    href = posixpath.relpath(f"{target_path}.md", start)
    return href if href.startswith("../") else f"./{href}"


def link_label(text: str) -> str:
    return re.sub(r"([\[\]\\])", r"\\\1", " ".join(text.split()))


def one_line(text: Optional[str]) -> str:
    return " ".join((text or "").split())


def strip_links_block(body: str) -> str:
    """Remove the links section Kyo generates."""
    return _LINKS_BLOCK.sub("", body)


def append_links_block(body: str, entries: List[str]) -> str:
    """Append Kyo's generated links section. A body without a trailing
    newline gets one, so strip_links_block can undo this exactly."""
    if not body.endswith("\n"):
        body += "\n"
    lines = "\n".join(entries)
    return f"{body}\n{LINKS_START}\n## Links\n\n{lines}\n{LINKS_END}\n"
