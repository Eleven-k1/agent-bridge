"""Import shim: only needed while backend/pyproject.toml is absent.

When the shared pytest configuration provides ``pythonpath = ["src"]`` this file
becomes a no-op because ``agent_bridge`` already imports.
"""

import sys
from importlib.util import find_spec
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"

if find_spec("agent_bridge") is None and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
