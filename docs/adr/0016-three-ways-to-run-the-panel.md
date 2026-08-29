# Three ways to run the panel

`start.bat` asks the owner to keep a console window open for the scheduler to keep running. That is the whole reason this exists: the panel is a daily background job with a web UI, and a console window is a poor container for one. Closing it by accident stops every check-in, and nothing says so.

So there are now three entry points, and **one** startup sequence underneath them:

| | `run.py` | `desktop.py` | the image |
|---|---|---|---|
| how it starts | `start.bat` | double-click | `docker compose up -d` |
| the UI | a browser tab | its own window | a browser tab |
| where it lives | the checkout | `dist/签到面板/` | a container |
| the browser fallback | the folder's `.local/` | beside the exe | a volume |

`panel/sandbox.py:prepare()` is the shared sequence, and it is shared because its order is load-bearing rather than stylistic: stdout line buffering, then `loopback.install()` **before anything builds an event loop** (ADR-0014), then `sys.path`, then the sandbox paths as env defaults (ADR-0006). Two copies of that would have meant two copies of the loopback fix, and one of them rotting. A fourth entry point has to call it too.

## The window that does not close

Clicking X hides the panel and says so once, with a "don't show again" box; the tray icon's left click brings it back and its 退出 really quits. This is the one place the shell overrides what a window manager normally means, so it is worth being explicit: the panel is only useful while it is running, and a user who closes the window has almost certainly not decided to stop checking in. Asking once, and never again after they say so, costs one dialog.

The dialog is a Win32 `TaskDialogIndirect` in `desktop_dialog.py` rather than anything from the GUI stack, because pywebview asks its `closing` handler **synchronously** and takes the answer literally — there is nowhere to await a web modal. It also means the shell needs Common Controls **v6**: `C:\Windows\System32\comctl32.dll` is the v5 copy and does not export that symbol, so its absence shows up as an `AttributeError` at attribute lookup, not an error code. `python.exe`'s own manifest asks for v6, which is why development worked; the packaged exe needs the dependency declared in `desktop.spec`, and the acceptance check that would catch its loss is "did the X button produce a dialog".

Everything decidable without a desktop is in `desktop_state.py` (`decide_close`, and the rule that only a stored `true` counts as consent), which is why 13 of the 201 tests can cover it. The window and the tray were verified by driving the built exe with the messages Windows itself posts — 15 checks, including a real mouse click on the tray menu item where `GetMenuItemRect` says it is.

## The container's deliberate deviation from ADR-0006

ADR-0006 says every dependency lives inside the project folder. In the image, "the project folder" is `/app`, and three things that ADR-0006 keeps as directories become **volumes**: `data/` because it is the only copy of the accounts and it is plaintext (ADR-0003), `.browser_profiles/` because it holds live sessions, and `.local/cloakbrowser/` because it is a ~700MB download (measured: `chromium-146.0.7680.177.5`) that must survive `docker compose down`. The spirit is intact — nothing reaches outside the deployment for state — but a reader comparing the two documents should see the exception named rather than infer it.

The browser binary is not in the image on purpose. `cloakbrowser.launch_async` calls `ensure_binary()` itself, so the container fetches it on first *use*, not at startup, and the image stays at 929MB instead of 1.6GB. Its system libraries come from `playwright install-deps chromium`, which is playwright's own list maintained per Debian release, not a list written here; a browser really launches in the container and loads a page (verified).

`PANEL_HOST=0.0.0.0` inside the container is **not** a decision about exposure and cannot be — a container reaches its own published port through the bridge, so binding loopback inside would make the panel unreachable. The trust boundary is compose's `127.0.0.1:8000:8000`. These two are easy to confuse and the consequence of confusing them is publishing plaintext credentials to a LAN, so both files say it in place.

## Consequences

- The scheduler no longer depends on a console window nobody may close. The desktop shell refuses to start beside `start.bat` (a named mutex for a second desktop instance, a port probe for the rest) because two panels would lock one database and put two browsers on one profile.
- That port probe cannot be a `bind()`: `SO_REUSEADDR` on Windows means "bind anyway, even though someone holds this", so the probe reported a busy port as free and uvicorn went on to fight the owner's panel for it (measured). It dials instead — an answered connection proves a listener, and a socket that is bound but not listening refuses, which is the case uvicorn can still take.
- The frozen build has **two** roots. `sys._MEIPASS` is a temp directory PyInstaller deletes on exit, so resolving `data/panel.db` against it would put the accounts somewhere new on every run and then throw them away; `sandbox.roots()` returns the exe's folder for what is written and `_MEIPASS` for what was bundled.
- `panel/` stays OS-neutral, and that is now checked rather than asserted: all 188 of its tests pass inside the Linux image. The Win32 code (`desktop_dialog.py`) and the shell's own tests (`tests/`) live at the repo root for the same reason — a test for a root module sitting under `panel/tests/` is what stopped the container collecting the suite at all.
- Rejected: onefile PyInstaller. It unpacks ~40MB per launch, moves `_MEIPASS` every run, and cannot hold the writable folders that have to sit beside the exe.
- Rejected: a command queue between the tray thread and the window. Its only justification was a deadlock that was inferred and then measured not to exist — calling pywebview from pystray's own thread returns normally. Quitting still posts `WM_CLOSE` rather than calling `destroy()`, not for deadlock reasons but because a posted message cannot block by construction and quitting is the one path that must never wedge.
- Rejected: shipping the browser in the image, and shipping CJK fonts. The first doubles it for something a volume holds better; the second is ~100MB for screenshots this code path does not take (`panel/browser_login.py` saves none).
- `npm ci` needs `--legacy-peer-deps` in the image because it needs it on the host too: `@heroui/theme@2.4.26` declares `peerDependencies: tailwindcss >=4.0.0` against the project's `^3.4.17`. The flag reproduces the tree `node_modules/` was actually installed with rather than papering over a difference between host and image. The real fix is a frontend decision and is not packaging's to make.
- The container runs as uid 10001, not root: the process holding plaintext credentials should not own the filesystem it runs on. A *bind* mount keeps the host's ownership, so it has to be chowned or the panel cannot write its database.
- **ADR-0015 is an empty number.** `AGENTS.md` and `CONTEXT.md` both cite it for the evidence string on a check-in (`Outcome.evidence` / `accounts.last_evidence`); that decision is real and both descriptions of it are accurate, but no file was ever written. Noted here so the next reader stops looking, and so the number is not reused for something else.
