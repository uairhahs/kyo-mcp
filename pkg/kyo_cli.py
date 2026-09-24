#!/usr/bin/env python3
"""Compatibility shim: the CLI now lives in kyo_mcp.cli (installed as the
`kyo-cli` command). Kept so existing `python kyo_cli.py ...` calls work."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from kyo_mcp.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
