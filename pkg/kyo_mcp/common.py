"""Small helpers shared by the MCP server and the CLI.

Kept free of heavy imports: the CLI is started once per pi tool call, and
importing the MCP SDK alone (via mcp_server) used to cost it ~0.65s.
"""

import datetime
import uuid
from importlib.metadata import PackageNotFoundError, version
from typing import Optional

try:
    _VERSION = version("kyo-mcp")
except PackageNotFoundError:  # running from a source tree without install
    _VERSION = "0.0.0"

# The actor stamped into `generated.by`. OKF §7: agents and tools use
# `<producer>/<version>`; `process:<id>` is reserved for automated processes.
GENERATOR = f"kyo-mcp/{_VERSION}"


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
