"""orbyn-design-status: runs the unit tests that live next to design_status.py."""
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orbyn-design-status" / "scripts"


def load_tests(loader, tests, pattern):
    sys.path.insert(0, str(SCRIPTS))
    return loader.discover(str(SCRIPTS), pattern="test_design_status.py", top_level_dir=str(SCRIPTS))


if __name__ == "__main__":
    unittest.main()
