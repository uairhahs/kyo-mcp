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
    clear_hindsight_operation,
    get_hindsight_operation,
    get_sync_hash,
    query_catalog,
    set_hindsight_operation,
    set_sync_hash,
)
from kyo_mcp.okf_schema import (
    OKFConcept,
)

logger = logging.getLogger(__name__)


def _content_hash(content: str) -> str:
    """Hash of the exact text handed to a memory system, so a resync of an
    unchanged concept can be recognized and skipped instead of resyncing
    every node on every call regardless of whether anything actually
    changed."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# Every call into Hindsight sets this timeout: without one, a slow or
# overloaded backend leaves `requests.post()` waiting indefinitely rather
# than failing, and every endpoint here touches Hindsight's LLM pipeline in
# some way (extraction, embedding/reranking, reflection, consolidation), so
# one generous timeout covers all of them. It is generous on purpose:
# prompt processing on a large context, especially on a CPU-only backend,
# can legitimately take well over a minute before generation even starts.
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
        hindsight_api_key: Optional[str] = None,
    ):
        """Initialize the bridge layer.

        Args:
            db_path: Path to the SQLite database. Defaults to None, which
                database.py's own get_connection() resolves to the real
                appdirs-based DB_PATH. A literal string like "kyo.db" here
                would be a different (and wrong) convention than the rest
                of the codebase uses, and would silently point
                sync_all_concepts at an empty database at a relative path,
                since single-node sync tools never touch self.db_path but
                sync_all_concepts's query_catalog(db_path=self.db_path)
                call always does.
            mnemosyne_config: Configuration for Mnemosyne integration
            hindsight_url: URL for Hindsight API. Defaults to the
                HINDSIGHT_API_BASE_URL env var (matching
                tests/test_hindsight_integration.py), then
                http://localhost:8888.
            hindsight_api_key: Bearer token for a hosted Hindsight instance
                (e.g. Hindsight Cloud) that requires authentication.
                Defaults to the HINDSIGHT_API_KEY env var, then None. A
                self-hosted Hindsight has no auth of its own, so when this
                is None, no Authorization header is sent at all, matching
                every request this class made before this option existed.
        """
        self.db_path = db_path
        self.mnemosyne_config = mnemosyne_config or {}
        self.hindsight_url = hindsight_url or os.environ.get(
            "HINDSIGHT_API_BASE_URL", "http://localhost:8888"
        )
        self.hindsight_api_key = hindsight_api_key or os.environ.get(
            "HINDSIGHT_API_KEY"
        )

    def _hindsight_headers(self) -> Dict[str, str]:
        """Auth header for a hosted Hindsight instance. Hindsight Cloud
        (api.hindsight.vectorize.io) requires `Authorization: Bearer
        <key>`; self-hosted Hindsight declares no security scheme at all
        in its own OpenAPI spec, so when no key is configured this returns
        an empty dict and every call sends no Authorization header,
        unchanged from before this option existed."""
        if self.hindsight_api_key:
            return {"Authorization": f"Bearer {self.hindsight_api_key}"}
        return {}

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

            # mnemosyne.remember()'s first argument must be a plain string:
            # it calls content.encode() internally, so passing a
            # structured dict here raises an AttributeError. Structured
            # fields belong in the separate `metadata` parameter.
            content = f"{concept.title}: {concept.description or ''}"

            # Skip if this exact content was already synced. Without this,
            # sync_all_concepts resynced every node on every call, and
            # since each resync re-runs a non-deterministic LLM extraction,
            # Hindsight's own dedup (matched on text similarity) doesn't
            # always recognize repeats of an unchanged concept as the same
            # thing, letting near-duplicate facts pile up.
            new_hash = _content_hash(content)
            if get_sync_hash(concept.id, "mnemosyne", db_path=self.db_path) == new_hash:
                logger.info(
                    f"Skipping {concept.id}: unchanged since last Mnemosyne sync"
                )
                return True

            # remember() is synchronous (returns str, not a coroutine),
            # unlike this method's own async signature, so no await here.
            remember(
                content,
                metadata={
                    "okf_id": concept.id,
                    "concept_type": concept.type,
                    "tags": concept.tags or [],
                    # OKFConcept has no created_at field; generated.at is
                    # the actual ISO 8601 timestamp field, already a string.
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
        """Submit an OKF concept to Hindsight for fact extraction.

        This used to POST with Hindsight's default async=false, blocking on
        the full extraction pipeline synchronously. On a slow or
        CPU-bound backend, a single retain call can legitimately take far
        longer than this method's own LLM_TIMEOUT, so the `requests.post()`
        reliably raised a client-side timeout and every such call was
        reported as "failed" regardless of whether Hindsight would have
        eventually finished it. Hindsight's own /memories endpoint already
        supports `async=true` + a client-supplied
        `operation_id` (idempotent: resubmitting the same id against
        unchanged content returns the existing operation rather than
        enqueuing a duplicate) plus a GET .../operations/{operation_id} to
        poll status; see check_hindsight_operation below. Submission with
        async=true returns as soon as Hindsight has enqueued the work, so
        this call is now fast regardless of how long extraction itself
        takes; the underlying LLM call is no longer this method's problem.

        Args:
            concept: The OKF concept to sync

        Returns:
            True if the submission was accepted (queued or already
            in-flight/complete), False if the submission itself failed.
            This does NOT mean extraction has finished; call
            check_hindsight_operation to find out when it has.
        """
        try:
            import uuid

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

            # Skip if there's already an in-flight submission for this exact
            # content, otherwise every retry before the first one resolves
            # would submit a fresh operation_id (since it's derived from
            # new_hash below, a *stale* one wouldn't collide, just duplicate
            # the work).
            existing = get_hindsight_operation(concept.id, db_path=self.db_path)
            if existing and existing[1] == new_hash:
                logger.info(
                    f"Skipping {concept.id}: operation {existing[0]} already "
                    "in flight for this content; use check_hindsight_operation"
                )
                return True

            # Deterministic, not random: resubmitting the same content for
            # the same node after a lost/ambiguous response (e.g. this
            # process died after Hindsight accepted the request but before
            # it recorded the operation_id locally) reuses the same id.
            # Hindsight treats that as "return the existing operation," not
            # a duplicate: reusing an id against genuinely *different*
            # content would instead get HTTP 409, which is exactly why this
            # is derived from new_hash rather than concept.id alone.
            operation_id = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"kyo-hindsight:{concept.id}:{new_hash}")
            )

            response = requests.post(
                f"{self.hindsight_url}/v1/default/banks/kyo/memories",
                json={
                    "items": [
                        {
                            "content": content,
                            "tags": concept.tags or [],
                            "importance": 5,
                        }
                    ],
                    "async": True,
                    "operation_id": operation_id,
                },
                headers=self._hindsight_headers(),
                timeout=LLM_TIMEOUT,
            )

            if response.status_code in [200, 201]:
                set_hindsight_operation(
                    concept.id, operation_id, new_hash, db_path=self.db_path
                )
                logger.info(
                    f"Queued concept {concept.id} to Hindsight as operation {operation_id}"
                )
                return True
            else:
                logger.error(
                    f"Hindsight API error: {response.status_code} - {response.text}"
                )
                return False

        except Exception as e:
            logger.error(f"Failed to submit concept {concept.id} to Hindsight: {e}")
            return False

    async def check_hindsight_operation(self, node_id: str) -> Dict[str, Any]:
        """Poll the status of a node's in-flight Hindsight sync operation.

        Returns a dict with at least a "state" key:
          - "no_operation": nothing pending (never submitted, or the last
            submission already completed/failed and was cleared).
          - "pending" / "processing": still queued or running in Hindsight.
          - "completed": extraction finished; hindsight_synced_hash has
            been promoted so a future sync_concept_to_hindsight call with
            the same content will skip as already-synced.
          - "failed" / "cancelled": extraction did not succeed; the pending
            operation has been cleared so a fresh sync_concept_to_hindsight
            call will submit a new attempt. "error" holds Hindsight's own
            error_message when present.
          - "not_found": Hindsight has no record of this operation_id (e.g.
            it was deleted server-side); cleared locally for the same
            reason as failed/cancelled.
          - "error": the status check itself failed (network error, bad
            response); the pending operation is left untouched so a later
            check can retry.
        """
        pending = get_hindsight_operation(node_id, db_path=self.db_path)
        if not pending:
            return {"state": "no_operation"}
        operation_id, pending_hash = pending

        try:
            import requests

            response = requests.get(
                f"{self.hindsight_url}/v1/default/banks/kyo/operations/{operation_id}",
                headers=self._hindsight_headers(),
                timeout=30,
            )
            if response.status_code != 200:
                return {
                    "state": "error",
                    "error": f"HTTP {response.status_code}: {response.text}",
                }
            body = response.json()
            status = body.get("status")

            if status == "completed":
                set_sync_hash(node_id, "hindsight", pending_hash, db_path=self.db_path)
                clear_hindsight_operation(node_id, db_path=self.db_path)
                logger.info(
                    f"Hindsight operation {operation_id} for {node_id} completed"
                )
                return {"state": "completed", "operation_id": operation_id}

            if status in ("failed", "cancelled", "not_found"):
                clear_hindsight_operation(node_id, db_path=self.db_path)
                logger.warning(
                    f"Hindsight operation {operation_id} for {node_id}: {status}"
                )
                return {
                    "state": status,
                    "operation_id": operation_id,
                    "error": body.get("error_message"),
                }

            # pending / processing: leave tracking state as-is
            return {"state": status, "operation_id": operation_id}

        except Exception as e:
            logger.error(f"Failed to check Hindsight operation {operation_id}: {e}")
            return {"state": "error", "error": str(e)}

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
                headers=self._hindsight_headers(),
                timeout=LLM_TIMEOUT,
            )

            if response.status_code == 200:
                data = response.json()
                results = data.get("results", data.get("memories", []))
                # Defensive truncation. As of hindsight-api 0.9.x, this
                # endpoint's `top_k` is silently ignored server-side: it
                # returns every memory in the bank instead of the requested
                # count, correctly ranked by score but never sliced.
                # Ranking itself is fine, so slicing here is sufficient;
                # the real fix belongs upstream in hindsight-api.
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
                headers=self._hindsight_headers(),
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
                headers=self._hindsight_headers(),
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
            # positionally would filter on
            # type = '<the db file path string>' and silently match zero
            # rows every time.
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
