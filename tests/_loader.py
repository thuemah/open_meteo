"""Import the integration's hass-free modules without executing the package.

`custom_components/open_meteo/__init__.py` imports Home Assistant, which is not
available to these tests and is not needed by them. The modules under test use
package-relative imports, so they are loaded into a synthetic package whose
__init__ is never run.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

PACKAGE = "open_meteo_under_test"
SOURCE = Path(__file__).resolve().parents[1] / "custom_components" / "open_meteo"


def load(name: str) -> types.ModuleType:
    """Load `name` from the integration as a submodule of a stub package."""
    if PACKAGE not in sys.modules:
        stub = types.ModuleType(PACKAGE)
        stub.__path__ = [str(SOURCE)]
        sys.modules[PACKAGE] = stub

    qualified = f"{PACKAGE}.{name}"
    if qualified in sys.modules:
        return sys.modules[qualified]

    spec = importlib.util.spec_from_file_location(qualified, SOURCE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module
