# A site's day starts when its bonus does, not at midnight

`scheduler.due` compared local calendar days, which is wrong for a site that opens the daily bonus at a fixed hour: `anyrouter.top` grants it from 08:30, so a run at 08:00 belongs to the *previous* window, and an account that collected yesterday at 09:00 looked due from midnight onwards — a browser launch every 30 minutes all night, each one failing because the site had nothing to give yet. Accounts therefore carry `checkin_after` ('HH:MM', empty = midnight) and `service.window_start(account)` answers "when did the current window open"; `scheduler.due` and `service._reconcile` both measure against that instead of a calendar date. Rejected: a global offset (each site differs), and cron expressions per account (the poll loop already retries every 30 minutes, so a window boundary is all that is missing).

**Consequences**:
- The owner has to know the hour and type it into 每日开放时间 when adding the account. Nothing discovers it: the sites do not publish it, and guessing it wrong is worse than asking.
- A run that succeeds inside the window ends the day's work. A failure is retried with a widening gap (`scheduler.backoff_s`: 30min, 1h, 2h, 4h, counted in `accounts.failures`), because the common causes — a dead credential, a WAF, a site with nothing to give — do not clear in half an hour, and each retry costs a browser launch.
- The reconciliation ledger check uses the same boundary, so an entry from yesterday morning no longer counts as "today's bonus" on an 08:30 site.
- `window_start` tolerates a malformed value by falling back to midnight — a typo must not stop an account from ever running.
