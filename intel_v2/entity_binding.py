"""Load the one canonical entity registry runtime for non-public consumers.

The CLI script remains the implementation and registry fixture remains the data
source. Importing this bridge does not create another alias table or approve any
candidate mapping.
"""

from __future__ import annotations

from functools import lru_cache
import importlib.util
from pathlib import Path
from types import ModuleType


@lru_cache(maxsize=1)
def registry_runtime() -> ModuleType:
    script = Path(__file__).resolve().parents[1] / "scripts" / "entity-registry.py"
    spec = importlib.util.spec_from_file_location("govintel_entity_registry_shared", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("canonical entity registry runtime is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
