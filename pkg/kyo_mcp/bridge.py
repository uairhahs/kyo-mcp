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

import logging
import os
from typing import Any, Dict, List, Optional

from kyo_mcp.database import (
    query_catalog,
)
from kyo_mcp.okf_schema import (
    OKFConcept,
)

logger = logging.getLogger(__name__)


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
        db_path: str = "kyo.db",
        mnemosyne_config: Optional[Dict[str, Any]] = None,
        hindsight_url: Optional[str] = None,
    ):
        """Initialize the bridge layer.

        Args:
            db_path: Path to the SQLite database
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

            # Convert OKF concept to Mnemosyne format
            mnemosyne_card = {
                "question": concept.title,
                "answer": concept.description or "",
                "tags": concept.tags or [],
                "metadata": {
                    "okf_id": concept.id,
                    "concept_type": concept.type,
                    "created_at": (
                        concept.created_at.isoformat() if concept.created_at else None
                    ),
                },
            }

            # Store in Mnemosyne
            await remember(mnemosyne_card)
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
            )

            if response.status_code in [200, 201]:
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
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("results", data.get("memories", []))
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
            # Query all concepts from the database
            concepts = query_catalog(self.db_path)

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
