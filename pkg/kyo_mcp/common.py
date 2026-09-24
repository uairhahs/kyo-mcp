"""Small helpers shared by the MCP server and the CLI.

Kept free of heavy imports: the CLI is started once per pi tool call, and
importing the MCP SDK alone (via mcp_server) used to cost it ~0.65s.
"""

import datetime
import uuid
from typing import Optional

GENERATOR = "process:kyo-mcp"


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_node_id() -> str:
    """Random, not sequential: ids derived from a node count collide when
    two processes create nodes against the same database."""
    return f"kyo-{uuid.uuid4().hex[:12]}"


def is_stale(stale_after: Optional[str]) -> bool:
    """True once the OKF `stale_after` date/time has passed. Unparseable
    values are treated as not stale rather than raising."""
    if not stale_after:
        return False
    try:
        cutoff = datetime.datetime.fromisoformat(stale_after)
    except ValueError:
        return False
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc) >= cutoff
