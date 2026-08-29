# All dependencies sandboxed inside the project folder

Every dependency — Python packages, browser binary, browser profiles, Node modules, SQLite database — must live inside the project folder (`D:\web-project\any-AutomaticCheckIn`), never in global user paths (`~/.cloakbrowser`, global `site-packages`, system Node globals, etc.). This was chosen to avoid polluting the host machine and keep the project fully self-contained and removable by deleting the folder.

## Mechanism

- **Python packages**: use `python -m venv` + `pip` (no `uv`). A local `.venv/` holds all packages. `uv` is not installed.
- **cloakbrowser browser binary**: set `CLOAKBROWSER_BINARY_PATH` to `./.local/cloakbrowser/chrome.exe` (or platform equivalent) before running `cloakbrowser install`, so the binary downloads into the project folder instead of `~/.cloakbrowser/`.
- **Browser profiles**: `CHECKIN_BROWSER_PROFILE_DIR` defaults to `.browser_profiles/` inside the project — kept as-is.
- **Node frontend**: `node_modules/` and `frontend/dist/` live in the project (Node itself is already isolated via fnm).
- **SQLite**: `./data/panel.db` inside the project.
- **uv**: not used. `python -m venv` + `pip` substitutes, since the host Python (3.14) satisfies the project's `>=3.11` requirement.

## Consequence

Running the panel requires activating the local venv or invoking `./.venv/Scripts/python.exe` directly. There is no global `uv` or project Python. Deployment is a single folder copy.
