"""Daily scheduler — while the panel is running it checks in by itself.

One successful check-in per account per window (ADR-0011), and nothing more: an
account that already collected is skipped until its next window opens. A failure is
retried, but with a widening gap — 30min, 1h, 2h, 4h — because a site that has
nothing to give at 09:00 rarely has something at 09:30, and every retry costs a
browser launch.
"""
import asyncio
import time
from datetime import datetime

from panel.service import CheckInService, window_start
from panel.store import Account, AccountStore

# ponytail: fixed poll interval, no cron expressions. A per-site window lives on the
# account (`checkin_after`, ADR-0011); this is only how often the loop looks.
TICK_S = 30 * 60
MAX_BACKOFF_S = 4 * 60 * 60  # a site that has failed all morning is not fixed by asking faster


def backoff_s(failures: int) -> float:
	"""How long to leave a failing account alone: 30min, 1h, 2h, then 4h.

	An account that cannot check in today usually cannot check in for hours (a dead
	credential, a WAF, a site with nothing to give), and each retry costs a browser
	launch. Success resets the count, and a new window ignores the backoff entirely.
	"""
	return min(TICK_S * 2 ** max(0, failures - 1), MAX_BACKOFF_S)


def due(account: Account) -> bool:
	"""Enabled, has something to authenticate with, and has not succeeded in the window.

	The window is the account's own day (ADR-0011): it opens at `checkin_after`, or at
	midnight when that is empty. Anything that succeeded inside the current window is
	done; everything else is due, and a failure is retried on the next tick.
	"""
	if not (account.enabled and account.base_url):
		return False
	if not (account.has_password or account.access_token or account.session):
		return False
	if not account.last_run_at:
		return True
	try:
		ran_at = datetime.fromisoformat(account.last_run_at).timestamp()
	except ValueError:
		return True
	if ran_at < window_start(account):  # a fresh window: always worth one attempt
		return True
	if account.last_success:
		return False
	return time.time() - ran_at >= backoff_s(account.failures)


async def run_once(store: AccountStore, service: CheckInService) -> dict:
	account_ids = [a.id for a in store.list_enabled() if due(a)]
	if not account_ids:
		return {}
	results = await service.check_in_many(account_ids)
	for account_id, outcome in results.items():
		state = 'OK' if outcome.success else 'FAIL'
		print(
			f'[SCHED] #{account_id} {state} checked_in={outcome.checked_in} '
			f'quota={outcome.after_quota} {outcome.error or ""}'.rstrip()
		)
	return results


async def loop(store: AccountStore, service: CheckInService, interval_s: int = TICK_S) -> None:
	while True:
		try:
			await run_once(store, service)
		except Exception as e:  # a scheduler that dies silently is worse than a noisy one
			print(f'[SCHED] tick failed: {type(e).__name__}: {e}')
		await asyncio.sleep(interval_s)
