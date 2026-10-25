"""Run from a downloaded checkout without installing this package."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from kujiequ_research.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

