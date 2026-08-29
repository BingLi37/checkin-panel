# Panel backend executes real check-ins locally

The panel backend is a Python service with `cloakbrowser` + a browser binary (+ `xvfb` on Linux) installed, so the "test check-in" button performs the real check-in path end-to-end (WAF bypass via browser, form login, cookie extraction, sign-in API call). This was chosen over delegating tests to GitHub Actions (`workflow_dispatch`) because the user wants immediate, accurate feedback in the UI — a delegated Actions run takes minutes to queue and parse, and the panel would need GH API plumbing anyway. The cost is a heavy backend: the server must maintain a browser runtime matching the GitHub Actions environment.

**Sandboxing note (amended by ADR-0006)**: all of this — Python packages, the browser binary, browser profiles — lives inside the project folder, not in global user paths. The browser binary is pointed at `.local/cloakbrowser/` via `CLOAKBROWSER_BINARY_PATH`.
