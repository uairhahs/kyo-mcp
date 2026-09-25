"""Realistic Hindsight API response shapes for mocking.

Field names and required fields here are taken directly from Hindsight's
own OpenAPI schema (hindsight-api 0.9.x: RetainResponse, OperationResponse,
RecallResponse/RecallResult, ReflectResponse, ConsolidationResponse), not
guessed or copied from older test fixtures. That distinction matters: an
earlier ad-hoc mock shape (`{"observations": [...]}` for a reflect
response) let bridge.py and mcp_server.py's trigger_reflection code drift
from the real API -- the real ReflectResponse has only ever had a `text`
field -- without any test ever catching it, because the mock was never
checked against the schema it was standing in for. Every test that mocks
a Hindsight HTTP response should build it from here instead of writing an
inline dict, so that drift can't happen silently again.
"""

from typing import Any, Dict, List, Optional


def retain_response(
    operation_id: Optional[str] = "op-123",
    bank_id: str = "kyo",
    items_count: int = 1,
    is_async: bool = True,
) -> Dict[str, Any]:
    """RetainResponse: POST .../memories."""
    return {
        "success": True,
        "bank_id": bank_id,
        "items_count": items_count,
        "async": is_async,
        "operation_id": operation_id if is_async else None,
    }


def operation_status(
    status: str,
    operation_id: str = "op-123",
    error_message: Optional[str] = None,
    task_type: str = "batch_retain",
) -> Dict[str, Any]:
    """OperationResponse: GET .../operations/{operation_id}."""
    return {
        "id": operation_id,
        "task_type": task_type,
        "items_count": 1,
        "document_id": None,
        "filename": None,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:05Z",
        "status": status,
        "error_message": error_message,
        "retry_count": 0,
        "next_retry_at": None,
    }


def recall_result(
    id: str,
    text: str,
    okf_id: Optional[str] = None,
    document_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    context: Optional[str] = None,
) -> Dict[str, Any]:
    """One RecallResult entry, as found in RecallResponse.results."""
    result: Dict[str, Any] = {"id": id, "text": text}
    if okf_id is not None:
        result["metadata"] = {"okf_id": okf_id}
    if document_id is not None:
        result["document_id"] = document_id
    if tags is not None:
        result["tags"] = tags
    if context is not None:
        result["context"] = context
    return result


def recall_response(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """RecallResponse: POST .../memories/recall."""
    return {"results": results}


def reflect_response(text: str) -> Dict[str, Any]:
    """ReflectResponse: POST .../reflect. The only field bridge.py can
    rely on is `text` (markdown); there is no "observations" or
    "insights" field on the real response."""
    return {"text": text}


def consolidation_response(
    operation_id: str = "consolidate-op-123", deduplicated: bool = False
) -> Dict[str, Any]:
    """ConsolidationResponse: POST .../consolidate."""
    return {"operation_id": operation_id, "deduplicated": deduplicated}
