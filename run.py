#!/usr/bin/env python3
"""Start Rota Planner. Double-click the launcher for your system, or run:

    python3 run.py            your data, in ./data
    python3 run.py --demo     demonstration data with invented names
"""

import sys

if sys.version_info < (3, 9):
    sys.exit("Rota Planner needs Python 3.9 or newer.")

from rotaplanner.__main__ import main  # noqa: E402  (version check must come first)

sys.exit(main())
