"""Bridge layer connecting OKF v0.2 to Mnemosyne and Hindsight.

This module provides functions to sync OKF concepts to external memory systems:
- Mnemosyne: Spaced repetition for long-term retention
- Hindsight: Fact extraction, reflection, and consolidation

Usage:
    from kyo_mcp.bridge import BridgeLayer

    bridge = BridgeLayer()
    await bridge.sync_concept_to_mnemosyne(concept)
    await bridge.sync_concept_to_hindsight(concept)
    await bridge.trigger_consolidation()
"""

import hashlib
import logging
import os
from typing import Any, Dict, List, Optional

from kyo_mcp.database import (
    get_sync_hash,
    query_catalog,
    set_sync_hash,
)
from kyo_mcp.okf_schema import (
    OKFConcept,
)

logger = logging.getLogger(__name__)


def _content_hash(content: str) -> str:
    """Hash of the exact text handed to a memory system, so a resync of an
    unchanged concept can be recognized and skipped (2026-09-09: neither
    sync method tracked this, so sync_all_concepts resynced every node on
    every call regardless of whether anything had actually changed)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# No call into Hindsight ever set a `requests` timeout, so a slow or
# overloaded backend (confirmed 2026-09-09: a CPU-only LLM backend under
# concurrent load left `requests.post()` waiting indefinitely rather than
# failing) hung every caller instead of returning a clear error. Every
# endpoint here touches Hindsight's LLM pipeline in some way (extraction,
# embedding/reranking, reflection, consolidation), so one generous timeout
# covers all of them. It is generous on purpose: CPU-only prompt processing
# on a large context can legitimately take well over a minute before
# generation even starts.
LLM_TIMEOUT = 180


class BridgeLayer:
    """Bridge layer connecting OKF v0.2 to external memory systems.

    This class provides methods to sync OKF concepts to:
    - Mnemosyne (spaced repetition)
    - Hindsight (fact extraction, reflection, consolidation)

    Attributes:
        db_path: Path to the SQLite database
        mnemosyne_config: Configuration for Mnemosyne integration
        hindsight_url: URL for Hindsight API (default: $HINDSIGHT_API_BASE_URL,
            falling back to http://localhost:8888)
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        mnemosyne_config: Optional[Dict[str, Any]] = None,
        hindsight_url: Optional[str] = None,
    ):
        """Initialize the bridge layer.

        Args:
            db_path: Path to the SQLite database. Defaults to None, which
                database.py's own get_connection() resolves to the real
                appdirs-based DB_PATH -- previously defaulted to the
                literal string "kyo.db" here, a different (and wrong)
                convention than the rest of the codebase uses. mcp_server.py
                constructs BridgeLayer() with no override in three places,
                so that mismatch silently pointed sync_all_concepts at an
                empty database at a relative "kyo.db" path every time
                (confirmed 2026-09-09: single-node sync tools never hit
                this, since they never touch self.db_path, but
                sync_all_concepts's query_catalog(db_path=self.db_path)
                call always did).
            mnemosyne_config: Configuration for Mnemosyne integration
            hindsight_url: URL for Hindsight API. Defaults to the
                HINDSIGHT_API_BASE_URL env var (matching
                tests/test_hindsight_integration.py), then
                http://localhost:8888.
        """
        self.db_path = db_path
        self.mnemosyne_config = mnemosyne_config or {}
        self.hindsight_url = hindsight_url or os.environ.get(
            "HINDSIGHT_API_BASE_URL", "http://localhost:8888"
        )

    async def sync_concept_to_mnemosyne(self, concept: OKFConcept) -> bool:
        """Sync an OKF concept to Mnemosyne for spaced repetition.

        Args:
            concept: The OKF concept to sync

        Returns:
            True if sync was successful, False otherwise
        """
        try:
            # Import mnemosyne here to avoid dependency if not needed
            from mnemosyne import remember

            # mnemosyne.remember()'s first argument is a plain string (it
            # calls content.encode() internally, confirmed via
            # AttributeError: 'dict' object has no attribute 'encode' when
            # this used to pass a whole structured dict as `content`
            # instead). Structured fields belong in the separate
            # `metadata` parameter.
            content = f"{concept.title}: {concept.description or ''}"

            # Skip if this exact content was already synced. Without this,
            # sync_all_concepts resynced every node on every call, and
            # since each resync re-runs a non-deterministic LLM extraction,
            # Hindsight's own dedup (matched on text similarity) often
            # didn't recognize repeats of an unchanged concept as the same
            # thing, so noise piled up (2026-09-09: 168 of 192 facts in the
            # kyo bank stuck at proof_count=1 after repeated test syncs).
            new_hash = _content_hash(content)
            if get_sync_hash(concept.id, "mnemosyne", db_path=self.db_path) == new_hash:
                logger.info(
                    f"Skipping {concept.id}: unchanged since last Mnemosyne sync"
                )
                return True

            # remember() is synchronous (returns str, not a coroutine,
            # confirmed via TypeError 2026-09-09), unlike this method's own
            # async signature -- no await here.
            remember(
                content,
                metadata={
                    "okf_id": concept.id,
                    "concept_type": concept.type,
                    "tags": concept.tags or [],
                    # OKFConcept has no created_at field (confirmed via
                    # AttributeError 2026-09-09); generated.at is the
                    # actual ISO 8601 timestamp field, already a string.
                    "created_at": concept.generated.at if concept.generated else None,
                },
            )
            set_sync_hash(concept.id, "mnemosyne", new_hash, db_path=self.db_path)
            logger.info(f"Synced concept {concept.id} to Mnemosyne")
            return True

        except Exception as e:
            logger.error(f"Failed to sync concept {concept.id} to Mnemosyne: {e}")
            return False

    async def sync_concept_to_hindsight(self, concept: OKFConcept) -> bool:
        """Sync an OKF concept to Hindsight for fact extraction.

        Args:
            concept: The OKF concept to sync

        Returns:
            True if sync was successful, False otherwise
        """
        try:
            import requests

            # Prepare content for Hindsight
            content = f"{concept.title}: {concept.description or ''}"
            if concept.tags:
                content += f"\nTags: {', '.join(concept.tags)}"

            # Skip if this exact content was already synced (see the
            # matching check in sync_concept_to_mnemosyne for why).
            new_hash = _content_hash(content)
            if get_sync_hash(concept.id, "hindsight", db_path=self.db_path) == new_hash:
                logger.info(
                    f"Skipping {concept.id}: unchanged since last Hindsight sync"
                )
                return True

            # Send to Hindsight API
            response = requests.post(
                f"{self.hindsight_url}/v1/default/banks/kyo/memories",
                json={
                    "items": [
                        {
                            "content": content,
                            "tags": concept.tags or [],
                            "importance": 5,
                        }
                    ]
                },
                timeout=LLM_TIMEOUT,
            )

            if response.status_code in [200, 201]:
                set_sync_hash(concept.id, "hindsight", new_hash, db_path=self.db_path)
                logger.info(f"Synced concept {concept.id} to Hindsight")
                return True
            else:
                logger.error(
                    f"Hindsight API error: {response.status_code} - {response.text}"
                )
                return False

        except Exception as e:
            logger.error(f"Failed to sync concept {concept.id} to Hindsight: {e}")
            return False

    async def recall_from_hindsight(
        self, query: str, top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """Recall memories from Hindsight using semantic search.

        Args:
            query: Search query
            top_k: Number of results to return

        Returns:
            List of matching memories
        """
        try:
            import requests

            response = requests.post(
                f"{self.hindsight_url}/v1/default/banks/kyo/memories/recall",
                json={"query": query, "top_k": top_k},
                timeout=LLM_TIMEOUT,
            )

            if response.status_code == 200:
                data = response.json()
                results = data.get("results", data.get("memories", []))
                # Defensive truncation. As of hindsight-api 0.9.x, this
                # endpoint's `top_k` is silently ignored server-side: it
                # returns every memory in the bank instead of the requested
                # count (confirmed 2026-09-09: a 5-result request against an
                # 81-fact bank returned all 81, correctly ranked by score but
                # never sliced). Ranking itself is fine, so slicing here is
                # sufficient; the real fix belongs upstream in hindsight-api.
                return results[:top_k]
            else:
                logger.error(
                    f"Hindsight API error: {response.status_code} - {response.text}"
                )
                return []

        except Exception as e:
            logger.error(f"Failed to recall from Hindsight: {e}")
            return []

    async def trigger_reflection(self, query: str) -> Dict[str, Any]:
        """Trigger reflection in Hindsight.

        Args:
            query: Reflection query

        Returns:
            Reflection results
        """
        try:
            import requests

            response = requests.post(
                f"{self.hindsight_url}/v1/default/banks/kyo/reflect",
                json={"query": query, "mode": "observations"},
                timeout=LLM_TIMEOUT,
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(
                    f"Hindsight API error: {response.status_code} - {response.text}"
                )
                return {}

        except Exception as e:
            logger.error(f"Failed to trigger reflection: {e}")
            return {}

    async def trigger_consolidation(self) -> bool:
        """Trigger consolidation in Hindsight.

        Returns:
            True if consolidation was triggered successfully
        """
        try:
            import requests

            response = requests.post(
                f"{self.hindsight_url}/v1/default/banks/kyo/consolidate",
                json={"mode": "full"},
                timeout=LLM_TIMEOUT,
            )

            if response.status_code in [200, 202]:
                logger.info("Consolidation triggered successfully")
                return True
            else:
                logger.error(
                    f"Hindsight API error: {response.status_code} - {response.text}"
                )
                return False

        except Exception as e:
            logger.error(f"Failed to trigger consolidation: {e}")
            return False

    async def sync_all_concepts(self) -> Dict[str, int]:
        """Sync all concepts from the database to external systems.

        Returns:
            Dictionary with counts of successful/failed syncs
        """
        try:
            # Query all concepts from the database. Must be a keyword
            # arg: query_catalog's first positional parameter is
            # type_filter, not db_path, so passing self.db_path
            # positionally (confirmed 2026-09-09) filtered on
            # type = '<the db file path string>' and silently matched
            # zero rows every time.
            concepts = query_catalog(db_path=self.db_path)

            stats = {"total": 0, "mnemosyne_success": 0, "hindsight_success": 0}

            for concept_data in concepts:
                concept = OKFConcept.model_validate(concept_data)
                stats["total"] += 1

                # Sync to Mnemosyne
                if await self.sync_concept_to_mnemosyne(concept):
                    stats["mnemosyne_success"] += 1

                # Sync to Hindsight
                if await self.sync_concept_to_hindsight(concept):
                    stats["hindsight_success"] += 1

            logger.info(
                f"Synced {stats['total']} concepts: {stats['mnemosyne_success']} to Mnemosyne, {stats['hindsight_success']} to Hindsight"
            )
            return stats

        except Exception as e:
            logger.error(f"Failed to sync all concepts: {e}")
            return {"total": 0, "mnemosyne_success": 0, "hindsight_success": 0}
