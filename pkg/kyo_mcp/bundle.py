"""OKF v0.2 bundle tools: export the store as a standalone bundle, import a
bundle into the store, and check a bundle against the conformance rules.

Section references (§) are to docs/SPEC.md. Kyo's store is itself a bundle
(kyo_mcp/store.py), so both export and import copy concept files byte for
byte: an imported file keeps its formatting until Kyo itself changes it.
Export adds an `index.md` per directory (§8) for progressive disclosure.
"""

import datetime
import json
import posixpath
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml
from kyo_mcp import database as db
from kyo_mcp import store
from kyo_mcp.bundle_format import (
    OKF_VERSION,
    RESERVED_FILENAMES,
    body_link_targets,
    concept_files,
    is_valid_kyo_id,
    link_label,
    one_line,
    path_derived_id,
    resolve_link,
)
from kyo_mcp.common import GENERATOR
from kyo_mcp.okf_schema import OKFConcept, parse_frontmatter, render_document

EXPORT_MANIFEST = ".kyo-export.json"

_LOG_DATE_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*$")


class BundleError(ValueError):
    """A bundle operation that can't proceed, such as exporting over files
    Kyo didn't write."""


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
            label = link_label(concept.get("title") or posixpath.basename(path))
            entry = f"* [{label}]({posixpath.basename(path)}.md)"
            description = one_line(concept.get("description"))
            lines.append(f"{entry} - {description}" if description else entry)
        lines.append("")
    if subdirs:
        lines += ["# Directories", ""]
        for subdir in sorted(subdirs):
            lines.append(f"* [{link_label(subdir)}]({subdir}/)")
        lines.append("")
    body = "\n".join(lines)
    if directory == "":
        # §8, §12: the bundle-root index may declare okf_version.
        return render_document({"okf_version": OKF_VERSION}, body)
    return body


def export_bundle(
    out_dir: str, overwrite: bool = False, db_path: Optional[Path] = None
) -> Dict[str, Any]:
    """Write a standalone copy of the store to `out_dir`, with an index.md
    in every directory (§8).

    `out_dir` must be missing or empty, unless it holds a previous Kyo
    export and `overwrite` is set: then only the files that export wrote
    are replaced. Output is deterministic, so an unchanged catalogue
    exports to identical files. Returns counts plus the validation result.
    """
    out = Path(out_dir).expanduser()
    source = store.bundle_dir(db_path)
    resolved = out.resolve()
    if resolved == source or source in resolved.parents or resolved in source.parents:
        raise BundleError(
            f"{out} overlaps the store ({source}); choose another directory"
        )
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

    store.refresh(db_path)
    rows = {
        r["bundle_path"]: r
        for r in db.query_catalog(db_path=db_path)
        if r.get("bundle_path")
    }
    written: List[str] = []
    by_dir: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for file, path in concept_files(source):
        if path not in rows:
            continue  # failed to parse, so not part of the catalogue
        target = out / f"{path}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file.read_bytes())
        written.append(f"{path}.md")
        by_dir.setdefault(posixpath.dirname(path), []).append((path, rows[path]))

    directories: Set[str] = {""}
    for path in written:
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
        "concepts": sum(len(v) for v in by_dir.values()),
        "index_files": len(directories),
        "validation": validate_bundle(str(out)),
    }


# --- Import ------------------------------------------------------------------


def import_bundle(bundle_dir: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Copy an OKF bundle's concept files into the store, byte for byte, at
    the same paths, then index them.

    A file is the same concept as one already in the store if it has the
    same id: its `kyo_id`, else the id derived from its path. Such a file
    replaces the stored one. A file whose path holds a different concept,
    or whose `kyo_id` is used at another path, is skipped and reported, as
    is a file that isn't a concept document. As a consumer must (§11),
    unknown types and keys, missing index files, and broken links are
    accepted. `index.md` and `log.md` aren't concepts and aren't copied.
    """
    root = Path(bundle_dir).expanduser()
    if not root.is_dir():
        raise BundleError(f"{root} is not a directory")
    target_root = store.bundle_dir(db_path)

    store.refresh(db_path)
    by_path = {
        r["bundle_path"]: r
        for r in db.query_catalog(db_path=db_path)
        if r.get("bundle_path")
    }
    path_of = {r["id"]: p for p, r in by_path.items()}

    created = updated = unchanged = 0
    skipped: List[Dict[str, str]] = []
    imported: List[str] = []
    for file, path in concept_files(root):
        data = file.read_bytes()
        try:
            concept, _ = OKFConcept.from_markdown(data.decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - reported per file
            skipped.append({"path": f"{path}.md", "reason": str(e).splitlines()[0]})
            continue
        node_id = concept.id if is_valid_kyo_id(concept.id) else path_derived_id(path)
        occupant = by_path.get(path)
        if occupant and occupant["id"] != node_id:
            skipped.append(
                {
                    "path": f"{path}.md",
                    "reason": "the store already has a different concept "
                    f"({occupant['id']}) at this path",
                }
            )
            continue
        if path_of.get(node_id, path) != path:
            skipped.append(
                {
                    "path": f"{path}.md",
                    "reason": f"{node_id} is already in the store at "
                    f"{path_of[node_id]}.md",
                }
            )
            continue
        dest = target_root / f"{path}.md"
        if dest.exists() and dest.read_bytes() == data:
            unchanged += 1
        else:
            store.write_file(dest, data)
            if occupant:
                updated += 1
            else:
                created += 1
        path_of[node_id] = path
        imported.append(path)

    report = store.refresh(db_path)
    indexed = {
        r["bundle_path"]
        for r in db.query_catalog(db_path=db_path)
        if r.get("bundle_path")
    }
    broken = []
    for path in imported:
        row = db.find_concept_by_bundle_path(path, db_path=db_path)
        for href in body_link_targets((row or {}).get("body_text") or ""):
            target = resolve_link(href, path)
            if target is not None and target not in indexed:
                broken.append({"from": f"{path}.md", "to": href})
    return {
        "bundle": str(root),
        "store": str(target_root),
        "concepts": len(imported),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "broken_links": broken,
        "skipped": skipped,
        "warnings": report["warnings"],
    }


# --- Validation --------------------------------------------------------------


def _problem(path: str, rule: str, message: str) -> Dict[str, str]:
    return {"path": path, "rule": rule, "message": message}


def _check_index(name: str, at_root: bool, frontmatter: Any, body: str, out) -> None:
    errors, warnings = out
    if frontmatter is not None:
        extra = (
            set(frontmatter) - {"okf_version"}
            if isinstance(frontmatter, dict)
            else {"?"}
        )
        if not at_root:
            errors.append(
                _problem(
                    name, "§8", "only the bundle-root index.md may have frontmatter"
                )
            )
        elif extra:
            errors.append(
                _problem(
                    name,
                    "§8",
                    "bundle-root index.md frontmatter may only carry "
                    f"okf_version, found {sorted(extra)}",
                )
            )
    if not re.search(r"^#{1,6} ", body, re.MULTILINE):
        warnings.append(_problem(name, "§8", "no section heading"))


def _check_log(name: str, body: str, out) -> None:
    errors, warnings = out
    dates: List[str] = []
    for line in body.splitlines():
        if not line.startswith("## "):
            continue
        match = _LOG_DATE_HEADING.match(line)
        if not match:
            errors.append(
                _problem(name, "§9", f"date heading is not YYYY-MM-DD: {line!r}")
            )
            continue
        try:
            datetime.date.fromisoformat(match.group(1))
        except ValueError:
            errors.append(_problem(name, "§9", f"not a real date: {line!r}"))
            continue
        dates.append(match.group(1))
    if dates != sorted(dates, reverse=True):
        warnings.append(_problem(name, "§9", "entries are not newest first"))


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
        if any(part.startswith(".") for part in rel.parts) or not file.is_file():
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

        if file.name.lower() == "index.md":
            at_root = rel.parent == Path(".")
            _check_index(name, at_root, frontmatter, body, (errors, warnings))
            continue
        if file.name.lower() == "log.md":
            _check_log(name, body, (errors, warnings))
            continue

        concepts += 1
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
        concept_id = rel.with_suffix("").as_posix()
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


__all__ = [
    "BundleError",
    "EXPORT_MANIFEST",
    "RESERVED_FILENAMES",
    "export_bundle",
    "import_bundle",
    "validate_bundle",
]
