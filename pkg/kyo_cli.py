#!/usr/bin/env python3
"""
Kyo CLI - Command-line interface for Kyo knowledge graph operations.

Usage:
    python kyo_cli.py <command> [args]

Commands:
    search_concepts <query> [--limit N]
    get_concept <concept_id>
    create_concept <label> <summary> [--content TEXT]
    create_link <source_id> <target_id> <link_type> [--weight N]
    query_catalog <query> [--limit N] [--verified-only]
    sync_to_mnemosyne <concept_id>
    sync_to_hindsight <concept_id>
    recall_from_hindsight <query> [--limit N]
    sync_all
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

# Add pkg directory to path
sys.path.insert(0, str(Path(__file__).parent))

from kyo_mcp.bridge import BridgeLayer
from kyo_mcp.database import (
    create_concept,
    create_link,
    get_concept_by_id,
    get_connection,
    query_catalog,
)


def cmd_search_concepts(args: argparse.Namespace) -> str:
    """Search concepts by query."""
    db_path = Path("/path/to/kyo/pkg/kyo_catalog.db")
    results = query_catalog(search_term=args.query, db_path=db_path)
    # Apply limit if specified
    if args.limit and args.limit > 0:
        results = results[: args.limit]
    return json.dumps(results, default=str)


def cmd_get_concept(args: argparse.Namespace) -> str:
    """Get concept by ID."""
    db_path = Path("/path/to/kyo/pkg/kyo_catalog.db")
    concept = get_concept_by_id(args.concept_id, db_path=db_path)
    if concept:
        return json.dumps(concept, default=str)
    else:
        return json.dumps({"error": f"Concept {args.concept_id} not found"})


def cmd_create_concept(args: argparse.Namespace) -> str:
    """Create a new concept."""
    db_path = Path("/path/to/kyo/pkg/kyo_catalog.db")
    import time

    concept_dict = {
        "id": f"concept-{int(time.time())}",
        "type": "Concept",
        "title": args.label,
        "description": args.summary,
        "status": "stable",
        "sources": [
            {
                "id": "kyo-cli",
                "resource": "kyo_cli.py",
            }
        ],
    }
    result = create_concept(
        concept_dict, markdown_body=args.content or "", db_path=db_path
    )
    return json.dumps({"created": True, "concept_id": concept_dict["id"]})


def cmd_create_link(args: argparse.Namespace) -> str:
    """Create a link between concepts."""
    db_path = Path("/path/to/kyo/pkg/kyo_catalog.db")
    conn = get_connection(db_path)
    result = create_link(
        conn,
        source_id=args.source_id,
        target_id=args.target_id,
        link_type=args.link_type,
        weight=args.weight,
    )
    return json.dumps({"created": True, "link_id": result.id})


def cmd_query_catalog(args: argparse.Namespace) -> str:
    """Query the catalog."""
    db_path = Path("/path/to/kyo/pkg/kyo_catalog.db")
    conn = get_connection(db_path)
    results = query_catalog(
        search_term=args.query,
        db_path=db_path,
    )
    # Apply filters
    if args.verified_only:
        results = [r for r in results if r.get("verified")]
    if args.limit and args.limit > 0:
        results = results[: args.limit]
    return json.dumps(results, default=str)


async def cmd_sync_to_mnemosyne(args: argparse.Namespace) -> str:
    """Sync concept to Mnemosyne."""
    try:
        from kyo_mcp.database import get_concept_by_id

        db_path = Path("/path/to/kyo/pkg/kyo_catalog.db")
        concept_data = get_concept_by_id(args.concept_id, db_path=db_path)
        if not concept_data:
            return json.dumps({"error": f"Concept {args.concept_id} not found"})
        from kyo_mcp.okf_schema import OKFConcept

        concept = OKFConcept.model_validate(concept_data)
        bridge = BridgeLayer(db_path=str(db_path))
        result = await bridge.sync_concept_to_mnemosyne(concept)
        return json.dumps({"success": result})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def cmd_sync_to_hindsight(args: argparse.Namespace) -> str:
    """Sync concept to Hindsight."""
    try:
        from kyo_mcp.database import get_concept_by_id

        db_path = Path("/path/to/kyo/pkg/kyo_catalog.db")
        concept_data = get_concept_by_id(args.concept_id, db_path=db_path)
        if not concept_data:
            return json.dumps({"error": f"Concept {args.concept_id} not found"})
        # Parse metadata if it's a string
        if isinstance(concept_data.get("metadata"), str):
            try:
                concept_data["metadata"] = json.loads(concept_data["metadata"])
            except Exception:
                concept_data["metadata"] = {}
        from kyo_mcp.okf_schema import OKFConcept

        concept = OKFConcept.model_validate(concept_data)
        bridge = BridgeLayer(db_path=str(db_path))
        result = await bridge.sync_concept_to_hindsight(concept)
        return json.dumps({"success": result})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def cmd_recall_from_hindsight(args: argparse.Namespace) -> str:
    """Recall from Hindsight."""
    try:
        bridge = BridgeLayer()
        result = await bridge.recall_from_hindsight(args.query, top_k=args.limit or 10)
        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


async def cmd_sync_all(args: argparse.Namespace) -> str:
    """Sync all concepts."""
    try:
        db_path = Path("/path/to/kyo/pkg/kyo_catalog.db")
        bridge = BridgeLayer(db_path=str(db_path))
        result = await bridge.sync_all_concepts()
        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


async def main():
    parser = argparse.ArgumentParser(description="Kyo CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # search_concepts
    p_search = subparsers.add_parser("search_concepts")
    p_search.add_argument("query", type=str)
    p_search.add_argument("--limit", type=int, default=10)

    # get_concept
    p_get = subparsers.add_parser("get_concept")
    p_get.add_argument("concept_id", type=str)

    # create_concept
    p_create = subparsers.add_parser("create_concept")
    p_create.add_argument("label", type=str)
    p_create.add_argument("summary", type=str)
    p_create.add_argument("--content", type=str, default=None)

    # create_link
    p_link = subparsers.add_parser("create_link")
    p_link.add_argument("source_id", type=str)
    p_link.add_argument("target_id", type=str)
    p_link.add_argument("link_type", type=str)
    p_link.add_argument("--weight", type=float, default=None)

    # query_catalog
    p_query = subparsers.add_parser("query_catalog")
    p_query.add_argument("query", type=str)
    p_query.add_argument("--limit", type=int, default=10)
    p_query.add_argument("--verified-only", action="store_true")

    # sync_to_mnemosyne
    p_sync_mnem = subparsers.add_parser("sync_to_mnemosyne")
    p_sync_mnem.add_argument("concept_id", type=str)

    # sync_to_hindsight
    p_sync_hind = subparsers.add_parser("sync_to_hindsight")
    p_sync_hind.add_argument("concept_id", type=str)

    # recall_from_hindsight
    p_recall = subparsers.add_parser("recall_from_hindsight")
    p_recall.add_argument("query", type=str)
    p_recall.add_argument("--limit", type=int, default=10)

    # sync_all
    subparsers.add_parser("sync_all")

    args = parser.parse_args()

    # Map commands to functions
    commands = {
        "search_concepts": cmd_search_concepts,  # sync
        "get_concept": cmd_get_concept,  # sync
        "create_concept": cmd_create_concept,  # sync
        "create_link": cmd_create_link,  # sync
        "query_catalog": cmd_query_catalog,  # sync
        "sync_to_mnemosyne": cmd_sync_to_mnemosyne,  # async
        "sync_to_hindsight": cmd_sync_to_hindsight,  # async
        "recall_from_hindsight": cmd_recall_from_hindsight,  # async
        "sync_all": cmd_sync_all,  # async
    }

    try:
        cmd_func = commands[args.command]
        # Check if the function is a coroutine function
        import inspect

        if inspect.iscoroutinefunction(cmd_func):
            result = await cmd_func(args)
        else:
            result = cmd_func(args)
        print(result)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
