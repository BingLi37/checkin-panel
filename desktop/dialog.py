"""The native "closing does not quit" dialog, with its own "don't ask again" box.

Windows-only, and deliberately at the repo root rather than in `panel/`: the container
image imports `panel` on Linux, so nothing under it may reach for comctl32 (ADR-0016).

Why a native dialog rather than a modal in the page: pywebview's `closing` handler is
**synchronous** — whatever it returns decides then and there whether the window dies. A
modal drawn inside the SPA cannot answer in place; it would have to veto the close first
and then re-close later, which is a second code path and a visible flicker.
`TaskDialogIndirect`'s verification checkbox is also exactly the control this needs, so
the "don't ask again" state comes back from the same call that asks the question.

Measured on Windows 11 / Python 3.14.3, and load-bearing:

- The struct is **160 bytes** on x64 with `_pack_ = 1`. Getting the layout wrong does not
  raise; it silently reads the wrong fields.
- `TaskDialogIndirect` lives only in **comctl32 v6**. `C:\\Windows\\System32\\comctl32.dll`
  is the v5 copy and does not export it — `hasattr` is False there. It resolves for us
  because `python.exe` carries a manifest requesting v6, so `LoadLibrary` picks the WinSxS
  copy. A host without that manifest therefore fails at **attribute lookup**, not at call
  time, which is why the fallback hangs off `AttributeError` and why the PyInstaller spec
  must keep a v6 manifest.
"""

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

TDCBF_CANCEL_BUTTON = 0x0008
TDF_ALLOW_DIALOG_CANCELLATION = 0x0008
TDF_USE_COMMAND_LINKS = 0x0010
TDF_POSITION_RELATIVE_TO_WINDOW = 0x1000

MB_OKCANCEL = 0x00000001
MB_ICONINFORMATION = 0x00000040
IDOK = 1

ID_TRAY = 101  # any value that is not one of the IDOK/IDCANCEL commons

TITLE = '签到面板'
INSTRUCTION = '关闭窗口后面板继续运行'
BODY = (
	'面板会收进任务栏通知区域，到点照常自动签到。\n\n'
	'左键点托盘图标可以重新打开这个窗口，右键选「退出」才真正结束。'
)
TRAY_BUTTON = '收进托盘\n面板继续在后台运行'
CHECKBOX = '不再提示'


@dataclass(frozen=True)
class Answer:
	"""What the owner said. `remember` is only ever True when the box was really ticked."""

	hide: bool  # True = hide to tray, False = stay open (they cancelled)
	remember: bool  # True = never ask again


class TASKDIALOG_BUTTON(ctypes.Structure):
	_pack_ = 1
	_fields_ = [('nButtonID', ctypes.c_int), ('pszButtonText', wintypes.LPCWSTR)]


TaskDialogCallback = ctypes.WINFUNCTYPE(
	ctypes.HRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, ctypes.c_void_p
)


class TASKDIALOGCONFIG(ctypes.Structure):
	_pack_ = 1
	_fields_ = [
		('cbSize', wintypes.UINT),
		('hwndParent', wintypes.HWND),
		('hInstance', wintypes.HINSTANCE),
		('dwFlags', wintypes.UINT),
		('dwCommonButtons', wintypes.UINT),
		('pszWindowTitle', wintypes.LPCWSTR),
		('pszMainIcon', wintypes.LPCWSTR),
		('pszMainInstruction', wintypes.LPCWSTR),
		('pszContent', wintypes.LPCWSTR),
		('cButtons', wintypes.UINT),
		('pButtons', ctypes.POINTER(TASKDIALOG_BUTTON)),
		('nDefaultButton', ctypes.c_int),
		('cRadioButtons', wintypes.UINT),
		('pRadioButtons', ctypes.POINTER(TASKDIALOG_BUTTON)),
		('nDefaultRadioButton', ctypes.c_int),
		('pszVerificationText', wintypes.LPCWSTR),
		('pszExpandedInformation', wintypes.LPCWSTR),
		('pszExpandedControlText', wintypes.LPCWSTR),
		('pszCollapsedControlText', wintypes.LPCWSTR),
		('pszFooterIcon', wintypes.LPCWSTR),
		('pszFooter', wintypes.LPCWSTR),
		('pfCallback', TaskDialogCallback),
		('lpCallbackData', ctypes.c_void_p),
		('cxWidth', wintypes.UINT),
	]


def available() -> bool:
	"""Whether the native dialog can be used at all. See the module docstring."""
	if sys.platform != 'win32':
		return False
	try:
		ctypes.windll.comctl32.TaskDialogIndirect
	except (AttributeError, OSError):
		return False
	return True


def ask(hwnd: int = 0) -> Answer:
	"""Ask whether to hide to the tray. Blocks until answered.

	Falls back to a plain message box when comctl32 v6 is not in the activation context;
	that box has no checkbox, so `remember` comes back False and the owner simply gets
	asked again next time. Never raises — a dialog that fails must not take the window
	with it, and "they did not agree to hide" is the safe reading of a failure.
	"""
	if not available():
		return _fallback(hwnd)
	try:
		return _task_dialog(hwnd)
	except OSError as e:
		print(f'[DESK] TaskDialog failed ({e}); using a plain message box')
		return _fallback(hwnd)


def _task_dialog(hwnd: int) -> Answer:
	fn = ctypes.windll.comctl32.TaskDialogIndirect
	fn.argtypes = [
		ctypes.POINTER(TASKDIALOGCONFIG),
		ctypes.POINTER(ctypes.c_int),
		ctypes.POINTER(ctypes.c_int),
		ctypes.POINTER(wintypes.BOOL),
	]
	fn.restype = ctypes.HRESULT

	buttons = (TASKDIALOG_BUTTON * 1)(TASKDIALOG_BUTTON(ID_TRAY, TRAY_BUTTON))

	cfg = TASKDIALOGCONFIG()
	cfg.cbSize = ctypes.sizeof(TASKDIALOGCONFIG)
	cfg.hwndParent = hwnd or None
	cfg.dwFlags = TDF_ALLOW_DIALOG_CANCELLATION | TDF_USE_COMMAND_LINKS
	if hwnd:
		cfg.dwFlags |= TDF_POSITION_RELATIVE_TO_WINDOW
	cfg.dwCommonButtons = TDCBF_CANCEL_BUTTON
	cfg.pszWindowTitle = TITLE
	cfg.pszMainInstruction = INSTRUCTION
	cfg.pszContent = BODY
	cfg.cButtons = 1
	cfg.pButtons = buttons
	cfg.nDefaultButton = ID_TRAY
	cfg.pszVerificationText = CHECKBOX

	pressed = ctypes.c_int(0)
	radio = ctypes.c_int(0)
	checked = wintypes.BOOL(0)
	fn(ctypes.byref(cfg), ctypes.byref(pressed), ctypes.byref(radio), ctypes.byref(checked))

	hide = pressed.value == ID_TRAY
	# Only trust the box when they actually chose to hide: a ticked box plus Cancel is
	# not consent to stop asking about something they just declined.
	return Answer(hide=hide, remember=hide and bool(checked.value))


def _fallback(hwnd: int) -> Answer:
	if sys.platform != 'win32':
		return Answer(hide=True, remember=False)
	text = f'{INSTRUCTION}\n\n{BODY}'
	answer = ctypes.windll.user32.MessageBoxW(
		hwnd or None, text, TITLE, MB_OKCANCEL | MB_ICONINFORMATION
	)
	return Answer(hide=answer == IDOK, remember=False)


def warn(instruction: str, body: str) -> None:
	"""Tell the owner why nothing opened. Prints instead when there is no Windows to ask.

	A desktop app that exits silently looks broken, and the two reasons it can refuse to
	start — a busy port, another instance — are both things only the owner can fix.
	"""
	if sys.platform != 'win32':
		print(f'[DESK] {instruction}: {body}')
		return
	MB_OK = 0x00000000
	MB_ICONWARNING = 0x00000030
	ctypes.windll.user32.MessageBoxW(None, f'{instruction}\n\n{body}', TITLE, MB_OK | MB_ICONWARNING)


def window_handle(title: str) -> Optional[int]:
	"""The top-level window with this exact title, for parenting the dialog."""
	if sys.platform != 'win32':
		return None
	ctypes.windll.user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
	ctypes.windll.user32.FindWindowW.restype = wintypes.HWND
	return ctypes.windll.user32.FindWindowW(None, title) or None
