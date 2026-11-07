"""OKF v0.2 bundles: export the catalogue as a bundle, import a bundle into
it, and check a bundle against the conformance rules.

Section references (§) are to docs/SPEC.md. A bundle is a directory of
markdown concept documents (§3); a concept's id is its path in the bundle
minus `.md` (§2); concepts link with standard markdown links (§6.1); and
`index.md` / `log.md` are reserved (§3.1, §8, §9).

Kyo's typed links have no OKF equivalent: OKF links are untyped, with the
relationship given by the surrounding prose (§6.1). They travel as a
`kyo_links` extension key (§4.1) and, so that plain OKF consumers still see
the edge, as an ordinary markdown link in a generated section delimited by
LINKS_START / LINKS_END, using relative links (see relative_link). Import
strips that section again.
"""

import datetime
import json
import posixpath
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import unquote

import yaml
from kyo_mcp.common import GENERATOR, new_node_id
from kyo_mcp.database import (
    create_concept,
    create_link,
    delete_outgoing_links,
    find_concept_by_bundle_path,
    get_all_links,
    get_concept_by_id,
    query_catalog,
    set_bundle_path,
)
from kyo_mcp.okf_schema import OKFConcept, parse_frontmatter, render_document

OKF_VERSION = "0.2"
RESERVED_FILENAMES = frozenset({"index.md", "log.md"})  # §3.1
KYO_LINKS_KEY = "kyo_links"
DEFAULT_RELATION = "references"
LINKS_START = "<!-- kyo:links -->"
LINKS_END = "<!-- /kyo:links -->"
EXPORT_MANIFEST = ".kyo-export.json"

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
_LOG_DATE_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*$")


class BundleError(ValueError):
    """A bundle operation that can't proceed, such as exporting over
    files Kyo didn't write."""


# --- Paths and links ---------------------------------------------------------


def slugify(text: str) -> str:
    """A lowercase, filesystem-safe path segment ("" if nothing survives)."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80].strip("-")


def is_valid_bundle_path(path: Optional[str]) -> bool:
    """A concept id (§2): relative, no `..`, no `.md`, not a reserved name."""
    if not path or path.startswith("/") or path.endswith(".md"):
        return False
    parts = path.split("/")
    if any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        return False
    return f"{parts[-1]}.md" not in RESERVED_FILENAMES


def _derived_path(concept: Dict[str, Any]) -> str:
    folder = slugify(concept.get("type") or "") or "concepts"
    name = slugify(concept.get("title") or "") or slugify(concept["id"])
    return f"{folder}/{name or 'concept'}"


def assign_bundle_paths(concepts: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """Map concept id -> bundle path. A concept keeps the path it was last
    exported to or imported from, so links into the bundle stay stable;
    new concepts get `<type>/<title>`, disambiguated with the id on a
    collision or a reserved name."""
    concepts = list(concepts)
    taken: Set[str] = set()
    paths: Dict[str, str] = {}
    # Stored paths first, so a new concept can't take an existing one's.
    for concept in concepts:
        path = concept.get("bundle_path")
        if is_valid_bundle_path(path) and path not in taken:
            paths[concept["id"]] = path
            taken.add(path)
    for concept in concepts:
        if concept["id"] in paths:
            continue
        path = _derived_path(concept)
        if path in taken or not is_valid_bundle_path(path):
            path = f"{path}-{slugify(concept['id'])[-12:]}"
        suffix = 2
        base = path
        while path in taken:
            path = f"{base}-{suffix}"
            suffix += 1
        paths[concept["id"]] = path
        taken.add(path)
    return paths


def strip_links_block(body: str) -> str:
    """Remove the links section Kyo generates on export."""
    return _LINKS_BLOCK.sub("", body)


def append_links_block(body: str, entries: List[str]) -> str:
    """Append Kyo's generated links section. A body without a trailing
    newline gets one, so strip_links_block can undo this exactly."""
    if not body.endswith("\n"):
        body += "\n"
    lines = "\n".join(entries)
    return f"{body}\n{LINKS_START}\n## Links\n\n{lines}\n{LINKS_END}\n"


def body_link_targets(body: str) -> List[str]:
    """Every markdown link target in a body, ignoring code."""
    text = _INLINE_CODE.sub("", _FENCED_CODE.sub("", body))
    return [m.group(1) for m in _MARKDOWN_LINK.finditer(text)]


def resolve_link(href: str, from_path: str) -> Optional[str]:
    """The concept id an in-bundle link points at (§6.1), or None for an
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


def _link_label(text: str) -> str:
    return re.sub(r"([\[\]\\])", r"\\\1", " ".join(text.split()))


def _one_line(text: Optional[str]) -> str:
    return " ".join((text or "").split())


# --- Export ------------------------------------------------------------------


def _clear_previous_export(out: Path) -> None:
    """Delete the files a previous Kyo export wrote, and nothing else."""
    manifest = json.loads((out / EXPORT_MANIFEST).read_text(encoding="utf-8"))
    root = out.resolve()
    parents: Set[Path] = set()
    for rel in manifest.get("files", []):
        target = (out / rel).resolve()
        if root in target.parents and target.suffix == ".md":
            target.unlink(missing_ok=True)
            parent = target.parent
            while parent != root:
                parents.add(parent)
                parent = parent.parent
    for directory in sorted(parents, key=lambda p: len(p.parts), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def _index_document(
    directory: str, concepts: List[Tuple[str, Dict[str, Any]]], subdirs: List[str]
) -> str:
    """An index.md (§8): concepts in this directory, then subdirectories."""
    lines: List[str] = []
    if concepts:
        lines += ["# Concepts", ""]
        for path, concept in sorted(
            concepts, key=lambda c: ((c[1].get("title") or c[0]).lower(), c[0])
        ):
            label = _link_label(concept.get("title") or posixpath.basename(path))
            entry = f"* [{label}]({posixpath.basename(path)}.md)"
            description = _one_line(concept.get("description"))
            lines.append(f"{entry} - {description}" if description else entry)
        lines.append("")
    if subdirs:
        lines += ["# Directories", ""]
        for subdir in sorted(subdirs):
            lines.append(f"* [{_link_label(subdir)}]({subdir}/)")
        lines.append("")
    body = "\n".join(lines)
    if directory == "":
        # §8, §12: the bundle-root index may declare okf_version.
        return render_document({"okf_version": OKF_VERSION}, body)
    return body


def export_bundle(
    out_dir: str, overwrite: bool = False, db_path: Optional[Path] = None
) -> Dict[str, Any]:
    """Write the whole catalogue to `out_dir` as an OKF v0.2 bundle.

    `out_dir` must be missing or empty, unless it holds a previous Kyo
    export and `overwrite` is set: then only the files that export wrote
    are replaced. Each concept keeps its bundle path across exports.
    Returns counts plus the bundle's validation result.
    """
    out = Path(out_dir).expanduser()
    if out.exists() and not out.is_dir():
        raise BundleError(f"{out} exists and is not a directory")
    if out.exists() and any(out.iterdir()):
        if not (out / EXPORT_MANIFEST).exists():
            raise BundleError(
                f"{out} is not empty and is not a previous Kyo export; "
                "choose an empty directory"
            )
        if not overwrite:
            raise BundleError(
                f"{out} holds a previous Kyo export; pass overwrite to replace it"
            )
        _clear_previous_export(out)
    out.mkdir(parents=True, exist_ok=True)

    concepts = query_catalog(db_path=db_path)
    by_id = {concept["id"]: concept for concept in concepts}
    paths = assign_bundle_paths(concepts)
    assigned = 0
    for concept in concepts:
        if concept.get("bundle_path") != paths[concept["id"]]:
            set_bundle_path(concept["id"], paths[concept["id"]], db_path=db_path)
            assigned += 1

    outgoing: Dict[str, List[Dict[str, Any]]] = {}
    for link in get_all_links(db_path=db_path):
        if link["source_id"] in paths and link["target_id"] in paths:
            outgoing.setdefault(link["source_id"], []).append(link)

    written: List[str] = []
    by_dir: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    typed_links = 0
    for concept in concepts:
        path = paths[concept["id"]]
        model = OKFConcept.model_validate(concept)
        body = strip_links_block(concept.get("body_text") or "") or model.default_body()
        inline = {resolve_link(href, path) for href in body_link_targets(body)}

        kyo_links: List[Dict[str, str]] = []
        block: List[str] = []
        for link in sorted(
            outgoing.get(concept["id"], []),
            key=lambda link: (paths[link["target_id"]], link["relation_type"]),
        ):
            target_path = paths[link["target_id"]]
            linked_inline = target_path in inline
            if linked_inline and link["relation_type"] == DEFAULT_RELATION:
                continue  # the body's own link already says it (§6.1)
            target = f"/{target_path}.md"
            kyo_links.append({"rel": link["relation_type"], "target": target})
            if not linked_inline:
                label = _link_label(
                    by_id[link["target_id"]].get("title") or target_path
                )
                href = relative_link(target_path, path)
                block.append(f"- {link['relation_type']}: [{label}]({href})")
                inline.add(target_path)
        typed_links += len(kyo_links)

        frontmatter = model.to_frontmatter()
        frontmatter.pop(KYO_LINKS_KEY, None)
        if kyo_links:
            frontmatter[KYO_LINKS_KEY] = kyo_links
        if block:
            body = append_links_block(body, block)

        file = out / f"{path}.md"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(render_document(frontmatter, body), encoding="utf-8")
        written.append(f"{path}.md")
        by_dir.setdefault(posixpath.dirname(path), []).append((path, concept))

    # Every directory on the way to a concept gets an index.md (§8).
    directories: Set[str] = {""}
    for path in paths.values():
        parent = posixpath.dirname(path)
        while parent:
            directories.add(parent)
            parent = posixpath.dirname(parent)
    for directory in sorted(directories):
        subdirs = sorted(
            posixpath.basename(d)
            for d in directories
            if d and posixpath.dirname(d) == directory
        )
        index = out / directory / "index.md"
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(
            _index_document(directory, by_dir.get(directory, []), subdirs),
            encoding="utf-8",
        )
        written.append(posixpath.join(directory, "index.md"))

    # No timestamp: re-exporting an unchanged catalogue must produce
    # byte-identical files, so a bundle kept in git only shows real changes.
    (out / EXPORT_MANIFEST).write_text(
        json.dumps(
            {
                "generator": GENERATOR,
                "okf_version": OKF_VERSION,
                "files": sorted(written),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "bundle": str(out),
        "concepts": len(concepts),
        "index_files": len(directories),
        "typed_links": typed_links,
        "paths_assigned": assigned,
        "validation": validate_bundle(str(out)),
    }


# --- Import ------------------------------------------------------------------


def _concept_files(root: Path) -> Iterable[Tuple[Path, str]]:
    """(file, concept id) for every concept document, skipping reserved
    files (§3.1) and hidden directories such as .git."""
    for file in sorted(root.rglob("*.md")):
        rel = file.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if file.name in RESERVED_FILENAMES:
            continue
        yield file, rel.with_suffix("").as_posix()


def import_bundle(bundle_dir: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load an OKF bundle into the catalogue.

    A concept is matched to an existing node by its `kyo_id`, then by the
    bundle path it was last exported to or imported from; otherwise a new
    node is created. Markdown links between concepts become `references`
    links, and `kyo_links` entries become typed links. As a consumer must
    (§11), the import tolerates unknown types and keys, missing index
    files, and broken links. A file that isn't a concept document is
    skipped and reported rather than failing the whole import.
    """
    root = Path(bundle_dir).expanduser()
    if not root.is_dir():
        raise BundleError(f"{root} is not a directory")

    imported: List[Tuple[str, str, OKFConcept, str]] = []  # (id, path, concept, body)
    skipped: List[Dict[str, str]] = []
    created = updated = 0
    for file, path in _concept_files(root):
        try:
            concept, body = OKFConcept.from_markdown(file.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 - reported per file, see docstring
            skipped.append({"path": f"{path}.md", "reason": str(e).splitlines()[0]})
            continue
        # A kyo_id is the node's identity; without one, the bundle path is.
        if concept.id:
            existing = get_concept_by_id(concept.id, db_path=db_path)
        else:
            existing = find_concept_by_bundle_path(path, db_path=db_path)
        node_id = concept.id or (existing["id"] if existing else new_node_id())
        concept.id = node_id
        concept.bundle_path = path
        body = strip_links_block(body)
        imported.append((node_id, path, concept, body))
        created += not existing
        updated += bool(existing)

    # Write every concept before any link, so link targets exist.
    ids_by_path = {path: node_id for node_id, path, _, _ in imported}
    for _, _, concept, body in imported:
        stored = concept.model_copy(
            update={
                "metadata": {
                    k: v for k, v in concept.metadata.items() if k != KYO_LINKS_KEY
                }
            }
        )
        create_concept(
            stored.model_dump(mode="python"),
            body,
            update_existing=True,
            db_path=db_path,
        )

    def target_id(path: Optional[str]) -> Optional[str]:
        if path is None:
            return None
        if path in ids_by_path:
            return ids_by_path[path]
        found = find_concept_by_bundle_path(path, db_path=db_path)
        return found["id"] if found else None

    inline_links = typed_links = 0
    broken: List[Dict[str, str]] = []
    for node_id, path, concept, body in imported:
        # The file is the source of truth for its concept's links.
        delete_outgoing_links(node_id, db_path=db_path)
        for href in body_link_targets(body):
            target = resolve_link(href, path)
            if target is None:
                continue  # external or non-concept link
            tid = target_id(target)
            if tid is None:
                broken.append({"from": f"{path}.md", "to": href})
                continue
            create_link(node_id, tid, DEFAULT_RELATION, db_path=db_path)
            inline_links += 1
        entries = concept.metadata.get(KYO_LINKS_KEY) or []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or not entry.get("target"):
                continue
            tid = target_id(resolve_link(str(entry["target"]), path))
            if tid is None:
                broken.append({"from": f"{path}.md", "to": str(entry["target"])})
                continue
            create_link(
                node_id, tid, str(entry.get("rel") or DEFAULT_RELATION), db_path=db_path
            )
            typed_links += 1

    return {
        "bundle": str(root),
        "concepts": len(imported),
        "created": created,
        "updated": updated,
        "inline_links": inline_links,
        "typed_links": typed_links,
        "broken_links": broken,
        "skipped": skipped,
    }


# --- Validation --------------------------------------------------------------


def _problem(path: str, rule: str, message: str) -> Dict[str, str]:
    return {"path": path, "rule": rule, "message": message}


def validate_bundle(bundle_dir: str) -> Dict[str, Any]:
    """Check a bundle against the OKF v0.2 conformance rules (§11).

    Errors break conformance: a concept document without parseable YAML
    frontmatter or without a non-empty `type` (§11.1, §11.2), or an
    `index.md` / `log.md` that doesn't follow §8 / §9 (§11.3). Warnings
    are soft guidance a consumer must tolerate, such as broken links.
    """
    root = Path(bundle_dir).expanduser()
    if not root.is_dir():
        raise BundleError(f"{root} is not a directory")
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    concept_ids: Set[str] = set()
    bodies: List[Tuple[str, str]] = []
    concepts = 0

    for file in sorted(root.rglob("*.md")):
        rel = file.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        name = rel.as_posix()
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(_problem(name, "§4", "not valid UTF-8"))
            continue
        try:
            frontmatter, body = parse_frontmatter(text)
        except yaml.YAMLError as e:
            errors.append(
                _problem(name, "§11.1", f"frontmatter is not valid YAML: {e}")
            )
            continue

        if file.name == "index.md":
            if frontmatter is not None:
                at_root = rel.parent == Path(".")
                extra = (
                    set(frontmatter) - {"okf_version"}
                    if isinstance(frontmatter, dict)
                    else {"?"}
                )
                if not at_root:
                    errors.append(
                        _problem(
                            name,
                            "§8",
                            "only the bundle-root index.md may have frontmatter",
                        )
                    )
                elif extra:
                    errors.append(
                        _problem(
                            name,
                            "§8",
                            f"bundle-root index.md frontmatter may only carry okf_version, found {sorted(extra)}",
                        )
                    )
            if not re.search(r"^#{1,6} ", body, re.MULTILINE):
                warnings.append(_problem(name, "§8", "no section heading"))
            continue

        if file.name == "log.md":
            dates: List[str] = []
            for line in body.splitlines():
                if line.startswith("## "):
                    match = _LOG_DATE_HEADING.match(line)
                    if not match:
                        errors.append(
                            _problem(
                                name, "§9", f"date heading is not YYYY-MM-DD: {line!r}"
                            )
                        )
                        continue
                    try:
                        datetime.date.fromisoformat(match.group(1))
                    except ValueError:
                        errors.append(
                            _problem(name, "§9", f"not a real date: {line!r}")
                        )
                        continue
                    dates.append(match.group(1))
            if dates != sorted(dates, reverse=True):
                warnings.append(_problem(name, "§9", "entries are not newest first"))
            continue

        concepts += 1
        concept_id = rel.with_suffix("").as_posix()
        if frontmatter is None:
            errors.append(_problem(name, "§11.1", "no YAML frontmatter block"))
            continue
        if not isinstance(frontmatter, dict):
            errors.append(_problem(name, "§11.1", "frontmatter is not a mapping"))
            continue
        kind = frontmatter.get("type")
        if not isinstance(kind, str) or not kind.strip():
            errors.append(_problem(name, "§11.2", "missing or empty `type`"))
            continue
        concept_ids.add(concept_id)
        bodies.append((concept_id, body))

    for concept_id, body in bodies:
        for href in body_link_targets(body):
            target = resolve_link(href, concept_id)
            if target is not None and target not in concept_ids:
                warnings.append(
                    _problem(
                        f"{concept_id}.md", "§6.1", f"broken link (tolerated): {href}"
                    )
                )

    return {
        "conformant": not errors,
        "okf_version": OKF_VERSION,
        "concepts": concepts,
        "errors": errors,
        "warnings": warnings,
    }
