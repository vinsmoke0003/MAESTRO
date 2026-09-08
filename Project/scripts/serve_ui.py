"""Start the MAESTRO web workspace from anywhere.

    python scripts/serve_ui.py [--port 8765] [--no-browser]

`python -m maestro.cli ui` needs the project root as the working directory;
this wrapper does not, which is what editor launch configs and shortcuts want.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from maestro.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["ui", *sys.argv[1:]]))
