"""``python -m aira_cloud`` is ``python -m monosage_cloud`` (the old name, kept for existing commands)."""

import sys

from monosage_cloud.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
