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

import asyncio
import hashlib
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

import httpx
from kyo_mcp.database import (
    clear_hindsight_operation,
    fail_hindsight_operation,
    get_failed_hindsight_operation,
    get_hindsight_operation,
    get_sync_hash,
    list_pending_hindsight_operations,
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
# overloaded backend leaves a request waiting indefinitely rather than
# failing, and every endpoint here touches Hindsight's LLM pipeline in some
# way (extraction, embedding/reranking, reflection, consolidation), so one
# generous timeout covers all of them. It is generous on purpose: prompt
# processing on a large context, especially on a CPU-only backend, can
# legitimately take well over a minute before generation even starts.
LLM_TIMEOUT = 180

# Status polls don't touch the LLM pipeline, so they can fail fast.
STATUS_TIMEOUT = 30

# Cap on concurrent Hindsight requests during a bulk sync. Retains are
# submitted with async=true, so each request only enqueues work; the cap
# just keeps a large catalogue from opening hundreds of sockets at once.
HINDSIGHT_CONCURRENCY = 8


class BridgeLayer:
    """Bridge layer connecting OKF v0.2 to external memory systems.

    This class provides methods to sync OKF concepts to:
    - Mnemosyne (spaced repetition)
    - Hindsight (fact extraction, reflection, consolidation)

    Every Hindsight call is made with httpx.AsyncClient, and Mnemosyne's
    synchronous remember() runs in a worker thread, so a slow memory
    system never blocks the MCP server's event loop (over streamable-http,
    a blocking call here would stall every connected client).

    Attributes:
        db_path: Path to the SQLite database
        mnemosyne_config: Configuration for Mnemosyne integration
        hindsight_url: URL for Hindsight API (default: $HINDSIGHT_API_BASE_URL,
            falling back to http://localhost:8888)
        hindsight_namespace / hindsight_bank: where memories are stored
            (default: $HINDSIGHT_NAMESPACE or "default", $HINDSIGHT_BANK or "kyo")
        last_error: description of the most recent failure, for callers
            that need to report why a method returned False/empty
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        mnemosyne_config: Optional[Dict[str, Any]] = None,
        hindsight_url: Optional[str] = None,
        hindsight_api_key: Optional[str] = None,
        hindsight_namespace: Optional[str] = None,
        hindsight_bank: Optional[str] = None,
    ):
        """Initialize the bridge layer.

        Args:
            db_path: Path to the SQLite database. Defaults to None, which
                database.py resolves to its configured default. Pass it by
                keyword to every database call: query_catalog's first
                positional parameter is type_filter, not db_path.
            mnemosyne_config: Configuration for Mnemosyne integration
            hindsight_url: URL for Hindsight API. Defaults to the
                HINDSIGHT_API_BASE_URL env var, then http://localhost:8888.
            hindsight_api_key: Bearer token for a hosted Hindsight instance
                (e.g. Hindsight Cloud) that requires authentication.
                Defaults to the HINDSIGHT_API_KEY env var, then None. A
                self-hosted Hindsight has no auth of its own, so when this
                is None, no Authorization header is sent at all.
            hindsight_namespace: Hindsight namespace. Defaults to the
                HINDSIGHT_NAMESPACE env var, then "default".
            hindsight_bank: Hindsight memory bank. Defaults to the
                HINDSIGHT_BANK env var, then "kyo".
        """
        self.db_path = db_path
        self.mnemosyne_config = mnemosyne_config or {}
        self.hindsight_url = (
            hindsight_url
            or os.environ.get("HINDSIGHT_API_BASE_URL", "http://localhost:8888")
        ).rstrip("/")
        self.hindsight_api_key = hindsight_api_key or os.environ.get(
            "HINDSIGHT_API_KEY"
        )
        self.hindsight_namespace = hindsight_namespace or os.environ.get(
            "HINDSIGHT_NAMESPACE", "default"
        )
        self.hindsight_bank = hindsight_bank or os.environ.get("HINDSIGHT_BANK", "kyo")
        self.last_error: Optional[str] = None
        # Shared client for bulk operations (see sync_all_concepts), so
        # they reuse one connection instead of opening one per request.
        self._client: Optional[httpx.AsyncClient] = None

    def _hindsight_headers(self) -> Dict[str, str]:
        """Auth header for a hosted Hindsight instance. Hindsight Cloud
        requires `Authorization: Bearer <key>`; self-hosted Hindsight
        declares no security scheme at all, so when no key is configured
        this returns an empty dict and no Authorization header is sent."""
        if self.hindsight_api_key:
            return {"Authorization": f"Bearer {self.hindsight_api_key}"}
        return {}

    def _bank_url(self, path: str) -> str:
        return (
            f"{self.hindsight_url}/v1/{self.hindsight_namespace}"
            f"/banks/{self.hindsight_bank}/{path}"
        )

    async def _hindsight_request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        timeout: float = LLM_TIMEOUT,
    ) -> httpx.Response:
        kwargs = {
            "json": json,
            "headers": self._hindsight_headers(),
            "timeout": timeout,
        }
        if self._client is not None:
            return await self._client.request(method, self._bank_url(path), **kwargs)
        async with httpx.AsyncClient() as client:
            return await client.request(method, self._bank_url(path), **kwargs)

    async def _gather_limited(self, coros: List[Any]) -> List[Any]:
        """Run coroutines concurrently, at most HINDSIGHT_CONCURRENCY at once."""
        semaphore = asyncio.Semaphore(HINDSIGHT_CONCURRENCY)

        async def limited(coro: Any) -> Any:
            async with semaphore:
                return await coro

        return await asyncio.gather(*(limited(c) for c in coros))

    def _fail(self, message: str) -> None:
        self.last_error = message
        logger.error(message)

    async def sync_concept_to_mnemosyne(self, concept: OKFConcept) -> bool:
        """Sync an OKF concept to Mnemosyne for spaced repetition.

        Args:
            concept: The OKF concept to sync

        Returns:
            True if sync was successful, False otherwise
        """
        try:
            # mnemosyne.remember()'s first argument must be a plain string:
            # it calls content.encode() internally, so passing a
            # structured dict here raises an AttributeError. Structured
            # fields belong in the separate `metadata` parameter.
            content = f"{concept.title}: {concept.description or ''}"

            # Skip if this exact content was already synced, rather than
            # storing a duplicate memory on every sync_all call.
            new_hash = _content_hash(content)
            if get_sync_hash(concept.id, "mnemosyne", db_path=self.db_path) == new_hash:
                logger.info(
                    f"Skipping {concept.id}: unchanged since last Mnemosyne sync"
                )
                return True

            # Imported only once there is something to sync: the import
            # alone takes ~0.6s, which an unchanged concept shouldn't pay.
            from mnemosyne import remember

            # remember() is synchronous and may do disk or model work, so
            # it runs in a worker thread to keep the event loop free.
            await asyncio.to_thread(
                remember,
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
            self._fail(f"Failed to sync concept {concept.id} to Mnemosyne: {e}")
            return False

    async def sync_concept_to_hindsight(self, concept: OKFConcept) -> bool:
        """Submit an OKF concept to Hindsight for fact extraction.

        Submits with Hindsight's `async=true` retain mode, which returns as
        soon as the work is enqueued. A synchronous retain blocks on the
        full extraction pipeline, which on a slow or CPU-bound backend
        takes far longer than LLM_TIMEOUT, so it was reliably reported as
        failed regardless of whether Hindsight eventually finished it.
        The client-supplied `operation_id` makes resubmission idempotent,
        and check_hindsight_operation polls it for completion.

        Args:
            concept: The OKF concept to sync

        Returns:
            True if the submission was accepted (queued or already
            in-flight/complete), False if the submission itself failed.
            This does NOT mean extraction has finished; call
            check_hindsight_operation to find out when it has.
        """
        try:
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
            # content, so retries before the first one resolves don't
            # duplicate the work.
            existing = get_hindsight_operation(concept.id, db_path=self.db_path)
            if existing and existing[1] == new_hash:
                logger.info(
                    f"Skipping {concept.id}: operation {existing[0]} already "
                    "in flight for this content; use check_hindsight_operation"
                )
                return True

            # Deterministic, not random: resubmitting the same content for
            # the same node after a lost/ambiguous response reuses the same
            # id, which Hindsight treats as "return the existing
            # operation". Reusing an id against different content gets
            # HTTP 409 instead, which is why this is derived from new_hash
            # rather than concept.id alone. Hindsight replays a known id
            # even when that operation failed, so after a failure the id
            # is also derived from the failed one to get a real retry.
            seed = f"kyo-hindsight:{concept.id}:{new_hash}"
            failed = get_failed_hindsight_operation(concept.id, db_path=self.db_path)
            if failed:
                seed += f":after:{failed}"
            operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, seed))

            response = await self._hindsight_request(
                "POST",
                "memories",
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
            )

            if response.status_code in [200, 201]:
                set_hindsight_operation(
                    concept.id, operation_id, new_hash, db_path=self.db_path
                )
                logger.info(
                    f"Queued concept {concept.id} to Hindsight as operation {operation_id}"
                )
                return True
            self._fail(f"Hindsight API error: {response.status_code} - {response.text}")
            return False

        except Exception as e:
            self._fail(f"Failed to submit concept {concept.id} to Hindsight: {e}")
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
            operation has been cleared and recorded as failed, so a fresh
            sync_concept_to_hindsight call will submit a new attempt under
            a new operation_id. "error" holds Hindsight's own
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
            response = await self._hindsight_request(
                "GET", f"operations/{operation_id}", timeout=STATUS_TIMEOUT
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

            if status in ("failed", "cancelled"):
                fail_hindsight_operation(node_id, operation_id, db_path=self.db_path)
            elif status == "not_found":
                clear_hindsight_operation(node_id, db_path=self.db_path)
            if status in ("failed", "cancelled", "not_found"):
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
            response = await self._hindsight_request(
                "POST", "memories/recall", json={"query": query, "top_k": top_k}
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
            self._fail(f"Hindsight API error: {response.status_code} - {response.text}")
            return []

        except Exception as e:
            self._fail(f"Failed to recall from Hindsight: {e}")
            return []

    async def trigger_reflection(self, query: str) -> Dict[str, Any]:
        """Trigger reflection in Hindsight.

        Args:
            query: Reflection query

        Returns:
            Reflection results
        """
        try:
            # Hindsight's ReflectRequest has no "mode" field (confirmed
            # against its OpenAPI schema); a stray one used to be sent
            # here, harmless only because FastAPI silently drops unknown
            # request fields rather than rejecting them.
            response = await self._hindsight_request(
                "POST", "reflect", json={"query": query}
            )

            if response.status_code == 200:
                return response.json()
            self._fail(f"Hindsight API error: {response.status_code} - {response.text}")
            return {}

        except Exception as e:
            self._fail(f"Failed to trigger reflection: {e}")
            return {}

    async def trigger_consolidation(self) -> bool:
        """Trigger consolidation in Hindsight.

        Returns:
            True if consolidation was triggered successfully
        """
        try:
            response = await self._hindsight_request(
                "POST", "consolidate", json={"mode": "full"}
            )

            if response.status_code in [200, 202]:
                logger.info("Consolidation triggered successfully")
                return True
            self._fail(f"Hindsight API error: {response.status_code} - {response.text}")
            return False

        except Exception as e:
            self._fail(f"Failed to trigger consolidation: {e}")
            return False

    async def sync_all_concepts(self) -> Dict[str, int]:
        """Sync all concepts from the database to external systems.

        In-flight Hindsight operations are polled first, so any that have
        completed since the last run are recorded as synced and not
        resubmitted.

        Returns:
            Dictionary of counts: total, mnemosyne_success,
            hindsight_success (queued or already current), and
            hindsight_completed (in-flight operations found finished)
        """
        stats = {
            "total": 0,
            "mnemosyne_success": 0,
            "hindsight_success": 0,
            "hindsight_completed": 0,
        }
        try:
            concepts = [
                OKFConcept.model_validate(c)
                for c in query_catalog(db_path=self.db_path)
            ]
            stats["total"] = len(concepts)

            async with httpx.AsyncClient() as client:
                self._client = client
                try:
                    pending = list_pending_hindsight_operations(db_path=self.db_path)
                    checks = await self._gather_limited(
                        [self.check_hindsight_operation(n) for n in pending]
                    )
                    stats["hindsight_completed"] = sum(
                        r["state"] == "completed" for r in checks
                    )

                    # Hindsight submissions are independent network calls,
                    # so they run concurrently. Mnemosyne's remember() runs
                    # in a worker thread and isn't known to be thread-safe,
                    # so those calls go one at a time alongside.
                    async def mnemosyne_all() -> int:
                        ok = 0
                        for concept in concepts:
                            ok += await self.sync_concept_to_mnemosyne(concept)
                        return ok

                    mnemosyne_ok, hindsight_results = await asyncio.gather(
                        mnemosyne_all(),
                        self._gather_limited(
                            [self.sync_concept_to_hindsight(c) for c in concepts]
                        ),
                    )
                    stats["mnemosyne_success"] = mnemosyne_ok
                    stats["hindsight_success"] = sum(hindsight_results)
                finally:
                    self._client = None

            logger.info(
                f"Synced {stats['total']} concepts: {stats['mnemosyne_success']} to "
                f"Mnemosyne, {stats['hindsight_success']} to Hindsight"
            )
            return stats

        except Exception as e:
            self._fail(f"Failed to sync all concepts: {e}")
            return stats
