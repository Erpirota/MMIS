"""Executable entrypoint for python -m mmis."""

import sys
from mmis.cli import main

if __name__ == "__main__":
    sys.exit(main())
