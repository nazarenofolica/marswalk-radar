"""Build the viewer package. Usage (from the repo root):

    python scripts/build_package.py configs/packages/gale_radar.yaml
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from marswalk.build import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv)
