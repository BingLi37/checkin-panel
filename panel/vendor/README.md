# Vendored third-party code

## `utils/` — cloakbrowser helpers from `anyrouter-check-in`

**Upstream:** https://github.com/Milly/anyrouter-check-in
**Licence:** BSD 2-Clause, see `LICENSE` beside this file. Copyright (c) 2025, Milly.

Five files, 1061 lines, copied rather than depended on. `panel/browser_login.py` imports six
names from them:

```python
from .vendor.utils.browser import (
	launch_login_context, load_browser_login_settings,
	login_with_email_form, prepare_browser_page, wait_for_waf_ready,
)
from .vendor.utils.popups import dismiss_popups
```

`browser.py` pulls in `debug.py`, `popups.py` and `proxy.py`; nothing else. The upstream project's
own `config.py` and `notify.py` are **not** here — no code imports them, and `notify.py` would drag
in `smtplib` for a mail-notification feature this panel does not have.

### What was changed

One thing only: `browser.py`'s three internal imports went from `from utils.X import` to
`from .X import`, because these files are a package inside `panel/` now rather than a directory on
`sys.path`. Every other line is upstream's, so `diff` against upstream stays readable — keep it
that way, and fix bugs upstream-shaped rather than reformatting to this project's style.

### Why vendored at all

The browser login needs a real browser to survive a WAF and an OAuth hop, and these helpers carry
fixes that were measured against live sites (see `CONTEXT.md` and the ADRs). Upstream is a
GitHub-Actions script, not a library — it publishes no package, so there is nothing to pin.

Nothing on the HTTP check-in path touches any of this. A panel that only does HTTP check-ins never
imports it.

### The licence obligation

BSD-2 clause 2 asks a binary redistribution to reproduce the notice, and both distributions are
one: the desktop exe bundles these files, and so does the container image. `LICENSE` therefore
ships next to them in both — `desktop.spec` and `Dockerfile` each copy it, and `THIRD-PARTY.md`
at the repo root records where it lands. Do not drop it from either.
