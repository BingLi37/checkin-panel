"""Pytest config for the tests of the repo-root modules (the desktop shell, ADR-0016).

They live here rather than in `panel/tests/` because their targets live at the repo root, and
that is not an aesthetic split: `panel/` has to stay importable in the Linux container, which
has no `desktop_state.py` in it at all. A test for it under `panel/tests/` made the container
unable to collect the suite — the failure that moved this file.

Only the path setup is here. `desktop_state` is pure stdlib and needs no loopback fix, no
event loop and no Windows: the Win32 parts sit in `desktop_dialog.py`, which nothing here
imports.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))
