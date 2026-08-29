"""The desktop shell — a window plus a tray icon (ADR-0016).

Deliberately empty. `state.py` is pure stdlib and its tests import it on any OS, so an
import here of anything Windows-only would make `import desktop.state` fail in the very
places that need it — the Linux container never imports this package at all, but the
suite does. Keep the Win32 code inside the modules that need it.
"""
