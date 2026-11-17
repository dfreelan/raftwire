"""Allow ``python -m raftwire``."""

from __future__ import annotations

import sys

from raftwire.cli import main

if __name__ == "__main__":
    sys.exit(main())
