#!/usr/bin/env python3
"""Startup script for the tmux MCP agent that handles PYTHONPATH automatically."""

import sys
import os
from pathlib import Path

# Add src directory to Python path
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))

# Import and run the agent
from tmux_mcp.agent import main

if __name__ == "__main__":
    sys.exit(main())





