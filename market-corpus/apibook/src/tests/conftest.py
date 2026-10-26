"""Load the independent plugin using the N.E.K.O SDK on PYTHONPATH."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("apibook", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
module = importlib.util.module_from_spec(spec)
sys.modules["apibook"] = module
spec.loader.exec_module(module)
