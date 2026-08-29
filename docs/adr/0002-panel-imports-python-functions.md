# Panel backend imports Python check-in functions directly

> **Superseded by ADR-0007.** The check-in is done over HTTP by `panel/newapi.py` now; nothing
> imports `check_in_account`, and `checkin.py` is no longer in this repository. What survived is
> the browser-login half, five files vendored in `panel/vendor/utils/` — the "779 lines" this
> record weighed is `browser.py`, and declining to rewrite it is the one part of the decision
> that still holds. Kept for the reasoning, not as a description of the code.

The panel backend is a Python FastAPI app that imports `check_in_account` and `AccountConfig` directly from the existing `checkin.py` / `utils.config`, rather than spawning `checkin.py` as a subprocess or rewriting the check-in logic. This was chosen because `check_in_account` already accepts a single `AccountConfig` and returns structured `(success, user_info_before, user_info_after)`, so per-account testing needs no refactoring. Subprocess (option 1) was rejected because `checkin.py` is batch-oriented (loads all accounts, loops, calls `sys.exit`) and parsing stdout strings is fragile. A fresh rewrite (option 3) would duplicate 779 lines of WAF-bypass and browser-login logic in `utils/browser.py`. The cost is that the backend must be Python, constraining language choice.
