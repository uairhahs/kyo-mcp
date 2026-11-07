"""The bundle store: OKF concept files are Kyo's source of truth.

The catalogue lives in a directory of OKF v0.2 concept files, the bundle
(docs/SPEC.md §3). SQLite (database.py) is an index derived from it: search,
the link graph, and Kyo's own sync state. Every write goes through this
module, which writes the file first and then re-indexes it, so the index
never holds anything the files don't.

Edits made to the files directly (an editor, git, a sync tool) are picked
up by `refresh`, which every MCP tool and CLI command runs first. It only
re-reads files whose size or mtime changed, and only re-indexes those whose
content hash changed.

Safety rules:
- A row is removed from the index only if it was indexed from a file and
  that file is gone. Rows with no file yet (a catalogue from before the
  bundle store, or written straight to the index) are written out to files
  instead, so upgrading can't lose a catalogue.
- A file that fails to parse (say, mid-edit) keeps its last indexed
  version and is reported, until it parses again.
- A file's identity is its `kyo_id`, else an id derived from its path
  (bundle_format.path_derived_id). If two files claim one `kyo_id` (a copy,
  a sync conflict), the first by path keeps it and the other is indexed
  under its path-derived id, with a warning.
"""

import hashlib
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from kyo_mcp import database as db
from kyo_mcp.bundle_format import (
    DEFAULT_RELATION,
    KYO_LINKS_KEY,
    append_links_block,
    body_link_targets,
    concept_files,
    is_valid_bundle_path,
    is_valid_kyo_id,
    link_label,
    path_derived_id,
    relative_link,
    resolve_link,
    slugify,
    strip_links_block,
    suggested_path,
)
from kyo_mcp.okf_schema import OKFConcept, render_document

_lock = threading.RLock()


def bundle_dir(db_path: Optional[Path] = None) -> Path:
    """The bundle that is the store for a database: $KYO_BUNDLE_DIR for the
    default database, else a `bundle` directory next to the database."""
    if db_path is None:
        configured = os.environ.get("KYO_BUNDLE_DIR")
        if configured:
            return Path(configured).expanduser().resolve()
        db_path = db.default_db_path()
    return Path(db_path).expanduser().resolve().parent / "bundle"


def concept_file(bundle_path: str, db_path: Optional[Path] = None) -> Path:
    return bundle_dir(db_path) / f"{bundle_path}.md"


# --- Files -------------------------------------------------------------------


def _stamp(file: Path, data: bytes) -> Dict[str, Any]:
    stat = file.stat()
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


def write_file(file: Path, content: "str | bytes") -> None:
    """Write via a hidden temporary file and a rename, so a reader (or a
    sync tool) never sees half a file. Hidden names are skipped by scans.
    Text is written as UTF-8 with its newlines unchanged."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{file.name}.", suffix=".tmp", dir=file.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, file)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _normalized_links(entries: Any) -> List[Dict[str, str]]:
    """`kyo_links` entries as {rel, target}, dropping malformed ones."""
    out: List[Dict[str, str]] = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("target"):
            link = {
                "rel": str(entry.get("rel") or DEFAULT_RELATION),
                "target": str(entry["target"]),
            }
            if link not in out:
                out.append(link)
    return out


def render_concept(
    concept: OKFConcept,
    body: str,
    kyo_links: List[Dict[str, str]],
    path: str,
    titles: Dict[str, str],
) -> str:
    """A concept file: frontmatter (with `kyo_id` and `kyo_links`) and the
    body, plus the generated links section for typed links the body
    doesn't already make."""
    frontmatter = concept.to_frontmatter()
    frontmatter.pop(KYO_LINKS_KEY, None)
    links = _normalized_links(kyo_links)
    if links:
        frontmatter[KYO_LINKS_KEY] = links
    body = strip_links_block(body) or concept.default_body()
    inline = {resolve_link(href, path) for href in body_link_targets(body)}
    block: List[str] = []
    for link in links:
        target = resolve_link(link["target"], path)
        if target is None or target in inline:
            continue
        inline.add(target)
        label = link_label(titles.get(target) or target)
        block.append(f"- {link['rel']}: [{label}]({relative_link(target, path)})")
    if block:
        body = append_links_block(body, block)
    return render_document(frontmatter, body)


def _file_identity(file: Path, path: str) -> Optional[str]:
    """The id an existing file would be indexed under, or None if it
    doesn't parse."""
    try:
        concept, _ = OKFConcept.from_markdown(file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - any unreadable file has no identity
        return None
    return concept.id if is_valid_kyo_id(concept.id) else path_derived_id(path)


def _free_path(base: str, node_id: str, taken: Set[str], root: Path) -> str:
    """`base`, or a variant of it, that no other concept or file uses."""

    def free(candidate: str) -> bool:
        if candidate in taken or not is_valid_bundle_path(candidate):
            return False
        file = root / f"{candidate}.md"
        return not file.exists() or _file_identity(file, candidate) == node_id

    if free(base):
        return base
    candidate = f"{base}-{slugify(node_id)[-12:]}"
    suffix = 2
    while not free(candidate):
        candidate = f"{base}-{slugify(node_id)[-12:]}-{suffix}"
        suffix += 1
    return candidate


def _titles(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    return {
        r["bundle_path"]: r.get("title") or "" for r in rows if r.get("bundle_path")
    }


# --- Refresh: files -> index -------------------------------------------------


def _write_unfiled(
    rows: List[Dict[str, Any]], root: Path, db_path: Optional[Path]
) -> int:
    """Write a file for every row that has none yet (a catalogue from before
    the bundle store, or a row written straight to the index)."""
    unfiled = [r for r in rows if not r.get("file_stamp")]
    if not unfiled:
        return 0
    taken = {
        r["bundle_path"] for r in rows if r.get("file_stamp") and r.get("bundle_path")
    }
    paths: Dict[str, str] = {}
    for row in sorted(unfiled, key=lambda r: r["id"]):
        stored = row.get("bundle_path")
        base = (
            stored
            if is_valid_bundle_path(stored)
            else suggested_path(row.get("type"), row.get("title"), row["id"])
        )
        paths[row["id"]] = _free_path(base, row["id"], taken, root)
        taken.add(paths[row["id"]])
    all_paths = {r["id"]: r.get("bundle_path") for r in rows if r.get("bundle_path")}
    all_paths.update(paths)
    titles = {
        all_paths[r["id"]]: r.get("title") or "" for r in rows if r["id"] in all_paths
    }

    for row in unfiled:
        path = paths[row["id"]]
        concept = OKFConcept.model_validate({**row, "bundle_path": path})
        body = row.get("body_text") or ""
        inline = {resolve_link(h, path) for h in body_link_targets(body)}
        links: List[Dict[str, str]] = _normalized_links(row.get("file_links"))
        for link in db.get_links(row["id"], direction="out", db_path=db_path):
            target = all_paths.get(link["target_id"])
            if target is None:
                continue
            if link["relation_type"] == DEFAULT_RELATION and target in inline:
                continue  # the body's own link already says it (§6.1)
            entry = {"rel": link["relation_type"], "target": f"/{target}.md"}
            if entry not in links:
                links.append(entry)
        write_file(
            root / f"{path}.md", render_concept(concept, body, links, path, titles)
        )
        db.update_index_metadata(row["id"], {db.BUNDLE_PATH_KEY: path}, db_path=db_path)
    return len(unfiled)


def _rebuild_links(db_path: Optional[Path]) -> int:
    """Re-derive the whole link table from the files' bodies and
    `kyo_links`. Returns how many links point at no concept (broken links
    are tolerated, §6.1)."""
    rows = db.query_catalog(db_path=db_path)
    ids = {r["bundle_path"]: r["id"] for r in rows if r.get("bundle_path")}
    broken = 0
    for row in rows:
        path = row.get("bundle_path")
        if not path:
            continue
        links: Set[Tuple[str, str]] = set()
        targets = [
            (h, DEFAULT_RELATION) for h in body_link_targets(row.get("body_text") or "")
        ]
        targets += [
            (link["target"], link["rel"])
            for link in _normalized_links(row.get("file_links"))
        ]
        for href, rel in targets:
            target = resolve_link(href, path)
            if target is None:
                continue
            if target in ids:
                links.add((ids[target], rel))
            else:
                broken += 1
        db.delete_outgoing_links(row["id"], db_path=db_path)
        for target_id, rel in sorted(links):
            db.create_link(row["id"], target_id, rel, db_path=db_path)
    return broken


def refresh(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Bring the index in line with the bundle. Returns what changed, plus
    files that couldn't be indexed (`errors`) and identity conflicts
    (`warnings`)."""
    with _lock:
        root = bundle_dir(db_path)
        root.mkdir(parents=True, exist_ok=True)
        report: Dict[str, Any] = {
            "bundle": str(root),
            "written": 0,
            "indexed": 0,
            "removed": 0,
            "unchanged": 0,
            "broken_links": 0,
            "pending_memory_deletions": 0,
            "errors": [],
            "warnings": [],
        }
        rows = db.query_catalog(db_path=db_path)
        report["written"] = _write_unfiled(rows, root, db_path)
        if report["written"]:
            rows = db.query_catalog(db_path=db_path)

        by_path = {r["bundle_path"]: r for r in rows if r.get("bundle_path")}
        files = {path: file for file, path in concept_files(root)}

        changed: Dict[str, Tuple[Path, bytes]] = {}
        for path, file in files.items():
            row = by_path.get(path)
            stamp = (row or {}).get("file_stamp") or {}
            stat = file.stat()
            if (
                stamp.get("mtime_ns") == stat.st_mtime_ns
                and stamp.get("size") == stat.st_size
            ):
                report["unchanged"] += 1
                continue
            data = file.read_bytes()
            if row and stamp.get("sha256") == hashlib.sha256(data).hexdigest():
                db.update_index_metadata(
                    row["id"], {db.FILE_STAMP_KEY: _stamp(file, data)}, db_path=db_path
                )
                report["unchanged"] += 1
                continue
            changed[path] = (file, data)

        parsed: Dict[str, Tuple[OKFConcept, str, Path, bytes]] = {}
        for path, (file, data) in sorted(changed.items()):
            try:
                concept, body = OKFConcept.from_markdown(data.decode("utf-8"))
            except Exception as e:  # noqa: BLE001 - reported per file
                message = str(e).splitlines()[0] if str(e) else type(e).__name__
                kept = " (keeping the last indexed version)" if path in by_path else ""
                report["errors"].append(
                    {"path": f"{path}.md", "message": message + kept}
                )
                continue
            parsed[path] = (concept, body, file, data)

        # Identity. Files that didn't change keep the ids they're indexed
        # under; changed files claim theirs in path order.
        claimed: Dict[str, str] = {
            r["id"]: p for p, r in by_path.items() if p in files and p not in parsed
        }
        ids: Dict[str, str] = {}
        for path, (concept, _, _, _) in sorted(parsed.items()):
            wanted = concept.id
            if is_valid_kyo_id(wanted) and claimed.get(wanted, path) == path:
                node_id = wanted
            else:
                node_id = path_derived_id(path)
                if wanted:
                    reason = (
                        f"kyo_id {wanted!r} is already used by {claimed[wanted]}.md"
                        if wanted in claimed
                        else f"kyo_id {wanted!r} is not a valid id"
                    )
                    report["warnings"].append(
                        {
                            "path": f"{path}.md",
                            "message": f"{reason}; indexed as {node_id}",
                        }
                    )
            claimed[node_id] = path
            ids[path] = node_id

        # Remove concepts whose file is gone (unless the same id moved to
        # another file) or whose file now holds a different concept.
        for path, row in by_path.items():
            gone = path not in files and row.get("file_stamp")
            replaced = path in ids and ids[path] != row["id"]
            if (gone or replaced) and row["id"] not in claimed:
                # Removed outside Kyo's delete tool, so its synced memories
                # (if any) still exist: leave a tombstone for the next sync.
                db.delete_concept(row["id"], db_path=db_path, tombstone=True)
                report["removed"] += 1

        for path, (concept, body, file, data) in parsed.items():
            links = concept.metadata.get(KYO_LINKS_KEY)
            stored = concept.model_copy(
                update={
                    "id": ids[path],
                    "bundle_path": path,
                    "metadata": {
                        k: v for k, v in concept.metadata.items() if k != KYO_LINKS_KEY
                    },
                }
            )
            try:
                db.create_concept(
                    stored.model_dump(mode="python"),
                    strip_links_block(body),
                    update_existing=True,
                    db_path=db_path,
                    merge_history=False,
                    file_stamp=_stamp(file, data),
                    file_links=_normalized_links(links),
                )
            except Exception as e:  # noqa: BLE001 - one file mustn't stop the rest
                report["errors"].append(
                    {"path": f"{path}.md", "message": f"could not be indexed: {e}"}
                )
                continue
            report["indexed"] += 1

        if parsed or report["removed"] or report["written"]:
            report["broken_links"] = _rebuild_links(db_path)
        report["pending_memory_deletions"] = len(db.list_pending_deletions(db_path))
        return report


# --- Writes: change a file, then re-index it ---------------------------------


def save_concept(
    concept_dict: Dict[str, Any],
    body: Optional[str] = None,
    create: bool = False,
    merge_history: bool = True,
    kyo_links: Optional[List[Dict[str, str]]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create or replace a concept by writing its file, then index it.

    With create=True an existing id raises ConceptExistsError. On a
    replace, verification history is merged (unless merge_history=False),
    the file keeps its path, and `body` / `kyo_links` default to the
    current ones. Returns the indexed concept.
    """
    with _lock:
        refresh(db_path)
        concept = OKFConcept.model_validate(concept_dict)
        if not concept.id:
            raise ValueError("Concept id is required")
        existing = db.get_concept_by_id(concept.id, db_path=db_path)
        if existing and create:
            raise db.ConceptExistsError(f"Concept {concept.id} already exists")

        if existing and merge_history:
            history = [
                {k: v for k, v in entry.items() if v is not None}
                for entry in existing.get("verified") or []
            ]
            for entry in concept.verified or []:
                dumped = entry.model_dump(exclude_none=True)
                if dumped not in history:
                    history.append(dumped)
            concept = OKFConcept.model_validate(
                {**concept.model_dump(mode="python"), "verified": history}
            )
        if body is None:
            body = (existing or {}).get("body_text") or ""
        if kyo_links is None:
            kyo_links = (existing or {}).get("file_links") or []

        rows = db.query_catalog(db_path=db_path)
        root = bundle_dir(db_path)
        path = (existing or {}).get("bundle_path")
        if not path:
            taken = {r["bundle_path"] for r in rows if r.get("bundle_path")}
            base = (
                concept.bundle_path
                if is_valid_bundle_path(concept.bundle_path)
                else suggested_path(concept.type, concept.title, concept.id)
            )
            path = _free_path(base, concept.id, taken, root)
        concept = concept.model_copy(update={"bundle_path": path})

        text = render_concept(concept, body, kyo_links, path, _titles(rows))
        write_file(root / f"{path}.md", text)
        report = refresh(db_path)
        for error in report["errors"]:
            if error["path"] == f"{path}.md":  # pragma: no cover - Kyo wrote it
                raise ValueError(f"{path}.md could not be indexed: {error['message']}")
        return db.get_concept_by_id(concept.id, db_path=db_path)


def update_concept(
    concept_id: str,
    changes: Dict[str, Any],
    body: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Apply a partial update. Returns the updated concept, or None if it
    doesn't exist. No changes and no body just marks it recently used."""
    with _lock:
        refresh(db_path)
        current = db.get_concept_by_id(concept_id, db_path=db_path)
        if current is None:
            return None
        if not changes and body is None:
            db.touch_concept(concept_id, db_path=db_path)
            return db.get_concept_by_id(concept_id, db_path=db_path)
        merged = {**current, **changes, "id": concept_id}
        return save_concept(merged, body, db_path=db_path)


def verify_concept(
    concept_id: str, event: Dict[str, Any], db_path: Optional[Path] = None
) -> bool:
    """Record a human verification event (§5.2). `by` may be given bare
    ("alice") or already prefixed ("human:alice")."""
    with _lock:
        refresh(db_path)
        current = db.get_concept_by_id(concept_id, db_path=db_path)
        if current is None:
            return False
        actor = str(event.get("by", "")).removeprefix("human:")
        entry = {**event, "by": f"human:{actor}"}
        save_concept({**current, "verified": [entry]}, db_path=db_path)
        return True


def delete_concept(
    concept_id: str, tombstone: bool = True, db_path: Optional[Path] = None
) -> bool:
    """Delete a concept's file, and the typed links other files hold to it.
    Inline links to it in other bodies are text and stay, as broken links
    (tolerated, §6.1). Returns False if it didn't exist.

    tombstone=True (the default) records that its synced memories, if any,
    still need removing (see database.delete_concept). Pass False when they
    were already removed, or are deliberately being kept.
    """
    with _lock:
        refresh(db_path)
        row = db.get_concept_by_id(concept_id, db_path=db_path)
        if row is None:
            return False
        path = row.get("bundle_path")
        for source in db.query_catalog(db_path=db_path):
            links = _normalized_links(source.get("file_links"))
            if source["id"] == concept_id or not source.get("bundle_path"):
                continue
            kept = [
                link
                for link in links
                if resolve_link(link["target"], source["bundle_path"]) != path
            ]
            if kept != links:
                save_concept(source, kyo_links=kept, db_path=db_path)
        if path:
            concept_file(path, db_path).unlink(missing_ok=True)
        # Remove the row here, with the caller's tombstone choice, so the
        # refresh below doesn't see a vanished file and tombstone it anyway.
        db.delete_concept(concept_id, db_path=db_path, tombstone=tombstone)
        refresh(db_path)
        return True


def add_link(
    source_id: str, target_id: str, relation: str, db_path: Optional[Path] = None
) -> None:
    """Add a typed link: a `kyo_links` entry in the source's file."""
    with _lock:
        refresh(db_path)
        source = db.get_concept_by_id(source_id, db_path=db_path)
        target = db.get_concept_by_id(target_id, db_path=db_path)
        missing = [i for i, c in ((source_id, source), (target_id, target)) if not c]
        if missing:
            raise KeyError(f"Concept(s) not found: {', '.join(missing)}")
        links = _normalized_links(source.get("file_links"))
        entry = {"rel": relation, "target": f"/{target['bundle_path']}.md"}
        if entry not in links:
            save_concept(source, kyo_links=links + [entry], db_path=db_path)


def remove_link(
    source_id: str,
    target_id: str,
    relation: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Tuple[int, bool]:
    """Remove typed links from source to target (only `relation` if given).
    Returns (how many were removed, whether the source's body still links
    to the target): a body link is text, so only an edit removes it."""
    with _lock:
        refresh(db_path)
        source = db.get_concept_by_id(source_id, db_path=db_path)
        target = db.get_concept_by_id(target_id, db_path=db_path)
        if not source or not target:
            return 0, False
        src, tgt = source["bundle_path"], target["bundle_path"]
        links = _normalized_links(source.get("file_links"))
        kept = [
            link
            for link in links
            if resolve_link(link["target"], src) != tgt
            or (relation is not None and link["rel"] != relation)
        ]
        if kept != links:
            save_concept(source, kyo_links=kept, db_path=db_path)
        inline = tgt in {
            resolve_link(h, src)
            for h in body_link_targets(source.get("body_text") or "")
        } and relation in (None, DEFAULT_RELATION)
        return len(links) - len(kept), inline


def read_concept_file(concept_id: str, db_path: Optional[Path] = None) -> Optional[str]:
    """The concept's file, exactly as stored, or None."""
    row = db.get_concept_by_id(concept_id, db_path=db_path)
    if not row or not row.get("bundle_path"):
        return None
    file = concept_file(row["bundle_path"], db_path)
    return file.read_text(encoding="utf-8") if file.exists() else None


__all__ = [
    "bundle_dir",
    "concept_file",
    "refresh",
    "save_concept",
    "update_concept",
    "verify_concept",
    "delete_concept",
    "add_link",
    "remove_link",
    "read_concept_file",
    "write_file",
    "render_concept",
]
