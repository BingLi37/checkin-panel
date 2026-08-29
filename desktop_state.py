"""The desktop shell's decisions and its one remembered preference — no GUI, no Win32.

Split out of `desktop.py` so both are reachable: importing `desktop.py` starts a sandbox
and pulls in webview, pystray and uvicorn, and `desktop_dialog.py` cannot even be imported
off Windows (`ctypes.wintypes` raises there). Everything here is stdlib, so the rule that
actually matters — what a click on X does — is testable.

Why the preference is a JSON file and not a column in `data/panel.db`:

- It is a shell preference, not account data. Neither `run.py` nor the container reads it.
- A column means a schema migration and the migration test the guidelines require, which
  is a lot to pay for one checkbox.
- `data/panel.db` is the file ADR-0003 asks the owner to back up because it holds
  credentials in the clear. Mixing window state into it muddies what that file is for.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

PREFS_NAME = 'desktop.json'
HIDE_SILENTLY = 'hide_without_asking'


class Answer(Protocol):
	"""What `desktop_dialog.ask()` returns. A Protocol so this module imports nothing."""

	hide: bool
	remember: bool


@dataclass(frozen=True)
class CloseDecision:
	allow_close: bool  # let the window really go
	hide: bool  # hide it to the tray instead
	remember: bool  # and never ask again


def decide_close(*, quitting: bool, hide_without_asking: bool, ask: Callable[[], Answer]) -> CloseDecision:
	"""What clicking X should do. `ask` is only called when it is really needed.

	Three ways in, and the order matters: the tray's 退出 sets `quitting` and then closes
	the window, so that has to win before any dialog appears — otherwise quitting would
	pop the "keep running in the background" box on the way out.
	"""
	if quitting:
		return CloseDecision(allow_close=True, hide=False, remember=False)
	if hide_without_asking:
		return CloseDecision(allow_close=False, hide=True, remember=False)
	answer = ask()
	# Cancel means "I did not mean to close it": stay exactly as we are, do not hide.
	return CloseDecision(allow_close=False, hide=answer.hide, remember=answer.remember)


def load(path: Path) -> dict:
	"""Read the preferences. Anything unreadable counts as "nothing remembered".

	Failing open costs one extra dialog; failing closed would hide the window with no
	explanation on a corrupt file, which is much harder to understand.
	"""
	try:
		data = json.loads(path.read_text(encoding='utf-8'))
	except (OSError, ValueError):
		return {}
	return data if isinstance(data, dict) else {}


def hide_without_asking(prefs: dict) -> bool:
	"""Strictly the stored `true` — a truthy string or a number is a corrupt file, not consent."""
	return prefs.get(HIDE_SILENTLY) is True


def remember_hide(path: Path, prefs: dict) -> dict:
	"""Persist "don't ask again". Returns the new preferences even if the write failed,
	so the choice at least holds for this session."""
	updated = {**prefs, HIDE_SILENTLY: True}
	try:
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(json.dumps(updated, indent=2), encoding='utf-8')
	except OSError as e:
		print(f'[DESK] could not save the preference ({e}); it holds until the app exits')
	return updated
