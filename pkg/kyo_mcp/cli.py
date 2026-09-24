"""
Kyo CLI - Command-line interface for Kyo knowledge graph operations.

Usage:
    kyo-cli <command> [args]

Commands:
    search_concepts <query> [--limit N] [--verified-only]
    query_catalog <query> [--limit N] [--verified-only]   (alias of search_concepts)
    get_concept <concept_id>
    create_concept <label> <summary> [--content TEXT] [--type TYPE]
    create_link <source_id> <target_id> <link_type>
    sync_to_mnemosyne <concept_id>
    sync_to_hindsight <concept_id>
    check_hindsight_operation <concept_id>
    recall_from_hindsight <query> [--limit N]
    sync_all
    trigger_consolidation
    trigger_reflection [query] [--concept-id ID]

Every command prints JSON to stdout; failures print a JSON error to stderr
and exit with status 1.
"""

import argparse
import asyncio
import inspect
import json
import sys
from typing import Any

from kyo_mcp.bridge import BridgeLayer
from kyo_mcp.database import (
    create_concept,
    create_link,
    get_concept_by_id,
    query_catalog,
)
from kyo_mcp.mcp_server import GENERATOR, new_node_id, now_iso
from kyo_mcp.okf_schema import OKFConcept


class CommandError(Exception):
    """A user-facing failure, reported as {"error": ...} with exit status 1."""


def _load_concept(concept_id: str) -> OKFConcept:
    concept_data = get_concept_by_id(concept_id)
    if not concept_data:
        raise CommandError(f"Concept {concept_id} not found")
    return OKFConcept.model_validate(concept_data)


def cmd_search_concepts(args: argparse.Namespace) -> Any:
    """Full-text search over concepts."""
    results = query_catalog(search_term=" ".join(args.query))
    if args.verified_only:
        results = [r for r in results if r.get("verified")]
    if args.limit and args.limit > 0:
        results = results[: args.limit]
    return results


def cmd_get_concept(args: argparse.Namespace) -> Any:
    """Get concept by ID."""
    concept = get_concept_by_id(args.concept_id)
    if not concept:
        raise CommandError(f"Concept {args.concept_id} not found")
    return concept


def cmd_create_concept(args: argparse.Namespace) -> Any:
    """Create a new concept."""
    concept_dict = {
        "id": new_node_id(),
        "type": args.type,
        "title": args.label,
        "description": args.summary,
        "status": "stable",
        "generated": {"by": GENERATOR, "at": now_iso()},
        "sources": [{"id": "kyo-cli", "resource": "kyo-cli"}],
    }
    body = args.content or f"# {args.label}\n\n{args.summary}\n"
    create_concept(concept_dict, markdown_body=body, update_existing=False)
    return {"created": True, "concept_id": concept_dict["id"]}


def cmd_create_link(args: argparse.Namespace) -> Any:
    """Create a link between concepts."""
    missing = [i for i in (args.source_id, args.target_id) if not get_concept_by_id(i)]
    if missing:
        raise CommandError(f"Concept(s) not found: {', '.join(missing)}")
    create_link(args.source_id, args.target_id, args.link_type)
    return {
        "created": True,
        "source_id": args.source_id,
        "target_id": args.target_id,
        "link_type": args.link_type,
    }


async def cmd_sync_to_mnemosyne(args: argparse.Namespace) -> Any:
    """Sync concept to Mnemosyne."""
    bridge = BridgeLayer()
    success = await bridge.sync_concept_to_mnemosyne(_load_concept(args.concept_id))
    return {"success": success, "error": bridge.last_error}


async def cmd_sync_to_hindsight(args: argparse.Namespace) -> Any:
    """Queue concept for Hindsight fact extraction."""
    bridge = BridgeLayer()
    success = await bridge.sync_concept_to_hindsight(_load_concept(args.concept_id))
    return {"success": success, "error": bridge.last_error}


async def cmd_check_hindsight_operation(args: argparse.Namespace) -> Any:
    """Check the status of a concept's in-flight Hindsight sync operation."""
    return await BridgeLayer().check_hindsight_operation(args.concept_id)


async def cmd_recall_from_hindsight(args: argparse.Namespace) -> Any:
    """Recall from Hindsight."""
    bridge = BridgeLayer()
    results = await bridge.recall_from_hindsight(
        " ".join(args.query), top_k=args.limit or 10
    )
    if bridge.last_error:
        raise CommandError(bridge.last_error)
    return results


async def cmd_sync_all(args: argparse.Namespace) -> Any:
    """Sync all concepts."""
    return await BridgeLayer().sync_all_concepts()


async def cmd_trigger_consolidation(args: argparse.Namespace) -> Any:
    """Trigger Hindsight consolidation."""
    bridge = BridgeLayer()
    success = await bridge.trigger_consolidation()
    return {"success": success, "error": bridge.last_error}


async def cmd_trigger_reflection(args: argparse.Namespace) -> Any:
    """Ask Hindsight to reflect on a free-text query, or on a concept (its
    title and description become the query)."""
    query = " ".join(args.query)
    if args.concept_id:
        concept = _load_concept(args.concept_id)
        query = f"{concept.title}: {concept.description or ''}".strip()
    if not query:
        raise CommandError("Pass a query or --concept-id")
    bridge = BridgeLayer()
    result = await bridge.trigger_reflection(query)
    if bridge.last_error:
        raise CommandError(bridge.last_error)
    return {"query": query, "result": result}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kyo CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("search_concepts", "query_catalog"):
        p = subparsers.add_parser(name)
        p.add_argument(
            "query", nargs="*", help="Search query (words joined with spaces)"
        )
        p.add_argument("--limit", type=int, default=10)
        p.add_argument("--verified-only", action="store_true")
        p.set_defaults(func=cmd_search_concepts)

    p = subparsers.add_parser("get_concept")
    p.add_argument("concept_id")
    p.set_defaults(func=cmd_get_concept)

    p = subparsers.add_parser("create_concept")
    p.add_argument("label")
    p.add_argument("summary")
    p.add_argument("--content", default=None, help="Markdown body")
    p.add_argument("--type", default="concept")
    p.set_defaults(func=cmd_create_concept)

    p = subparsers.add_parser("create_link")
    p.add_argument("source_id")
    p.add_argument("target_id")
    p.add_argument("link_type")
    p.set_defaults(func=cmd_create_link)

    for name, func in (
        ("sync_to_mnemosyne", cmd_sync_to_mnemosyne),
        ("sync_to_hindsight", cmd_sync_to_hindsight),
        ("check_hindsight_operation", cmd_check_hindsight_operation),
    ):
        p = subparsers.add_parser(name)
        p.add_argument("concept_id")
        p.set_defaults(func=func)

    p = subparsers.add_parser("recall_from_hindsight")
    p.add_argument("query", nargs="*", help="Search query (words joined with spaces)")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_recall_from_hindsight)

    subparsers.add_parser("sync_all").set_defaults(func=cmd_sync_all)
    subparsers.add_parser("trigger_consolidation").set_defaults(
        func=cmd_trigger_consolidation
    )

    p = subparsers.add_parser("trigger_reflection")
    p.add_argument(
        "query", nargs="*", help="Reflection query (words joined with spaces)"
    )
    p.add_argument("--concept-id", default=None, help="Reflect on this concept instead")
    p.set_defaults(func=cmd_trigger_reflection)
    return parser


async def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
        if inspect.isawaitable(result):
            result = await result
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    print(json.dumps(result, default=str))
    return 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
