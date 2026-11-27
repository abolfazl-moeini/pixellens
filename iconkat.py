#!/usr/bin/env python3
"""Backward-compatible iconkat CLI entry point."""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if not sys.path or sys.path[0] != _SRC:
    sys.path.insert(0, _SRC)

from pixellens.cli import *  # noqa: F401,F403,E402
from pixellens.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
