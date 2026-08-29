"""Remote promo cards: a read-only manifest, fetched over HTTPS, matched entirely locally.

The panel has no server side (ADR-0008), so "remotely configurable" here means a static
JSON published to a public repo and read through a CDN — the client only ever reads it.
Every decision about whether a card is shown (which sites the owner already has, how long
the panel has been in use, what was dismissed) is computed here from the local database, so
the outbound request is a bare GET that carries nothing about the user.

Two rules an audit of this file turns on, both load-bearing:
	- the only outbound traffic is GET <manifest url> — no query, no body, no cookies;
	- the manifest is untrusted input. A card whose CTA is not https is dropped, and no
	  field from it ever becomes CSS, HTML, a path, or SQL.

This is the one module outside `store` and `newapi` that opens the network, which
docs/guidelines/backend/quality-guidelines.md otherwise forbids: the manifest is not a New
API site, nothing here touches a credential, and folding it into `newapi` would put a
promotion inside the protocol engine.
"""
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

import httpx

# The manifest lives in the author's public 福利文章 repo rather than this one: the code
# repository keeps no promotional content in its history, and publishing a card is then a
# commit over there, not a release over here. On its own branch, not on main: main is what
# that repo shows its readers, and a targeting config is not something they came for.
PROMO_REPO = 'BingLi37/Welfare-Express'
PROMO_REF = 'promos'
PROMO_FILE = 'promos.json'

# The raw host first, jsDelivr as the fallback — the order is about freshness, and it is
# measured, not assumed: raw answers with max-age=300, while cdn.jsdelivr.net kept serving
# a 12h-old body (s-maxage=43200) for over 8 minutes after purge.jsdelivr.net answered 200
# for that exact path — the same commit was already live on fastly./gcore.jsdelivr.net, so
# it is one edge holding the stale copy and a purge cannot be trusted to clear it. jsDelivr
# stays in the list because it is the mirror that answers on a network which blocks GitHub;
# a user served by it sees a new card up to 12h late.
MIRRORS = (
	f'https://raw.githubusercontent.com/{PROMO_REPO}/{PROMO_REF}/{PROMO_FILE}',
	f'https://cdn.jsdelivr.net/gh/{PROMO_REPO}@{PROMO_REF}/{PROMO_FILE}',
)

# Five minutes because that is raw.githubusercontent.com's own max-age: polling faster cannot
# see anything newer, and polling slower is the only reason an edited manifest would not be live
# almost at once. A re-fetch costs a 304 (If-None-Match), and the SPA asks at most twice an hour.
TTL_S = 300
IMPRESSION_GAP_S = 3600  # a page reload is not a new impression
NEW_FOR_D = 3  # how long a card this panel has only just met still reads as brand new
# Short on purpose, and deliberately unrelated to newapi's timeouts: a promo must never be
# the reason a request feels slow.
TIMEOUT = httpx.Timeout(8.0, connect=4.0)
UA = 'auto-checkin-panel'  # not newapi.UA — a packet capture should name the caller

_CACHE: dict = {'at': None, 'etag': None, 'cards': []}


def enabled() -> bool:
	"""`PANEL_PROMO=0` turns the whole path off, checked before any client is constructed."""
	return os.getenv('PANEL_PROMO', '1') != '0'


def urls() -> tuple:
	"""`PANEL_PROMO_URL` replaces the mirrors outright: mirrors rot, and an auditor should
	be able to point the panel at a manifest of their own."""
	override = (os.getenv('PANEL_PROMO_URL') or '').strip()
	return (override,) if override else MIRRORS


@dataclass(frozen=True)
class Card:
	"""One manifest entry, after every field has been checked. Flat, because the targeting
	rules read better without three levels of dict; the nested wire shape is rebuilt by
	payload()."""

	id: str
	title: str
	body: str
	cta_label: str
	cta_url: str
	hero_title: Optional[str] = None
	hero_subtitle: Optional[str] = None
	hero_brand: Optional[str] = None
	hero_badge: Optional[str] = None
	priority: int = 0  # a weight, not a rank — see pick()
	theme: Optional[str] = None  # a palette *name*; the browser matches it against its own table
	missing_hosts: tuple = ()
	min_panel_age_h: float = 0.0
	min_accounts: int = 0
	cooldown_days: int = 1  # the *first* cooldown; it doubles per dismissal (cooldown_for)
	max_shows: int = 0  # 0 = no limit
	starts_at: Optional[str] = None  # 'YYYY-MM-DD'
	expires_at: Optional[str] = None

	def payload(self, *, sticker: str = 'unregistered') -> dict:
		"""What the SPA gets: text, plus two names it looks up in tables of its own. The
		targeting fields stay on this side — the browser has no use for them."""
		return {
			'id': self.id,
			'title': self.title,
			'body': self.body,
			'sticker': sticker,
			'theme': self.theme,
			'hero': {
				'title': self.hero_title,
				'subtitle': self.hero_subtitle,
				'brand': self.hero_brand,
				'badge': self.hero_badge,
			},
			'cta': {'label': self.cta_label, 'url': self.cta_url},
		}


def _text(value: Any, default: Optional[str] = None) -> Optional[str]:
	return (value.strip() or default) if isinstance(value, str) else default


def _num(value: Any, default: float = 0.0) -> float:
	return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _host(value: Any) -> str:
	"""A hostname out of whatever the manifest author typed: 'x.com', 'https://x.com/api',
	'WWW.X.com'. Also how a stored base_url becomes comparable."""
	text = str(value or '').strip().lower()
	text = (urlparse(text).hostname or '') if '//' in text else text.split('/')[0]
	return text[4:] if text.startswith('www.') else text


def _when(value: Any) -> Optional[datetime]:
	"""A stored ISO timestamp as an aware datetime, or None if it is not one."""
	try:
		stamp = datetime.fromisoformat(str(value))
	except (TypeError, ValueError):
		return None
	return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def parse_cards(manifest: Any) -> list:
	"""Manifest -> cards, dropping anything malformed. One bad card must cost only itself:
	the point of a remote list is that a typo in it cannot break the panel."""
	if not isinstance(manifest, dict) or _num(manifest.get('version')) != 1:
		return []
	raw_cards = manifest.get('cards')
	if not isinstance(raw_cards, list):
		return []
	cards = []
	for raw in raw_cards:
		card = _card(raw) if isinstance(raw, dict) else None
		if card is not None:
			cards.append(card)
	return cards


def _card(raw: dict) -> Optional[Card]:
	cta = raw.get('cta') if isinstance(raw.get('cta'), dict) else {}
	hero = raw.get('hero') if isinstance(raw.get('hero'), dict) else {}
	target = raw.get('target') if isinstance(raw.get('target'), dict) else {}
	show = raw.get('show') if isinstance(raw.get('show'), dict) else {}
	card_id, title, url = _text(raw.get('id')), _text(raw.get('title')), _text(cta.get('url'))
	# `url` lands in an href. Anything that is not https — javascript:, data:, a bare host —
	# is dropped rather than repaired: this is remote input, and guessing what the author
	# meant is how an injection point gets built.
	if not card_id or not title or not url or not url.startswith('https://'):
		return None
	hosts = target.get('missing_hosts') if isinstance(target.get('missing_hosts'), list) else []
	return Card(
		id=card_id,
		title=title,
		body=_text(raw.get('body'), '') or '',
		cta_label=_text(cta.get('label'), '了解一下 →') or '',
		cta_url=url,
		hero_title=_text(hero.get('title')),
		hero_subtitle=_text(hero.get('subtitle')),
		hero_brand=_text(hero.get('brand')),
		hero_badge=_text(hero.get('badge')),
		priority=int(_num(raw.get('priority'))),
		theme=_text(raw.get('theme')),
		missing_hosts=tuple(h for h in (_host(x) for x in hosts) if h),
		min_panel_age_h=_num(target.get('min_panel_age_h')),
		min_accounts=int(_num(target.get('min_accounts'))),
		cooldown_days=int(_num(show.get('cooldown_days'), 1.0)),
		max_shows=int(_num(show.get('max_shows'))),
		starts_at=_text(show.get('starts_at')),
		expires_at=_text(show.get('expires_at')),
	)


def hosts_of(accounts: Iterable[Any]) -> set:
	"""Which sites this panel already checks in to. Never leaves the process."""
	return {h for h in (_host(getattr(a, 'base_url', '')) for a in accounts) if h}


def panel_age_h(accounts: Iterable[Any], *, now: Optional[datetime] = None) -> float:
	"""How long the panel has been in use, from its oldest account. There is no
	installed-at record and none is needed: somebody who has never added an account has not
	started using the panel, and gets 0 — which no card with a minimum age can satisfy."""
	now = now or datetime.now(timezone.utc)
	stamps = [s for s in (_when(getattr(a, 'created_at', None)) for a in accounts) if s]
	return (now - min(stamps)).total_seconds() / 3600 if stamps else 0.0


def pick(
	cards: Iterable[Card],
	*,
	hosts: set,
	panel_age_h: float,
	account_count: int,
	state: dict,
	now: Optional[datetime] = None,
	rng: Any = random,
) -> Optional[Card]:
	"""One eligible card at random, or None.

	Random rather than ranked: every site the owner does not have is worth offering, and taking
	the top one on every load means the others are never seen until it is closed or registered.
	`priority` is the weight — a bigger number comes up more often, not first. `rng` is injected
	so a test can pin the draw; everything else arrives as an argument, which is what makes the
	targeting matrix testable without a database or a network."""
	now = now or datetime.now(timezone.utc)
	ready = [
		c
		for c in cards
		if _eligible(
			c,
			hosts=hosts,
			panel_age_h=panel_age_h,
			account_count=account_count,
			seen=state.get(c.id) or {},
			now=now,
		)
	]
	# max(_, 1) so a card that never set a priority still has a chance of being drawn.
	return rng.choices(ready, weights=[max(c.priority, 1) for c in ready])[0] if ready else None


def _eligible(card: Card, *, hosts: set, panel_age_h: float, account_count: int, seen: dict, now: datetime) -> bool:
	today = now.date().isoformat()
	if (card.starts_at and today < card.starts_at) or (card.expires_at and today > card.expires_at):
		return False
	if panel_age_h < card.min_panel_age_h or account_count < card.min_accounts:
		return False
	# The card advertises a site, and showing it to somebody who already has that site is
	# the one thing this whole mechanism exists to avoid. A subdomain counts as having it.
	if any(_has_host(hosts, target) for target in card.missing_hosts):
		return False
	if card.max_shows and _num(seen.get('shows')) >= card.max_shows:
		return False
	dismissed = _when(seen.get('dismissed_at'))
	return not (dismissed and (now - dismissed).days < cooldown_for(card, seen))


def cooldown_for(card: Card, seen: dict) -> int:
	"""How long this card stays away after being closed: the manifest's number, doubled once per
	dismissal (1 → 2 → 4 → 8 days). Closing something twice is a stronger no than closing it
	once, so a card the owner keeps waving off walks itself out of the way without ever being
	banned outright — and the sites they never closed keep coming up in the meantime, because
	both the count and the timer are per card. Capped: the exponent must not run away on a
	long-lived install."""
	return card.cooldown_days * 2 ** min(max(int(_num(seen.get('dismissals'))) - 1, 0), 10)


def sticker_for(seen: Optional[dict], *, now: Optional[datetime] = None) -> str:
	"""Which sticker the card wears — a site this panel was only just offered is new, anything
	else is simply not registered yet. Derived from local state rather than declared in the
	manifest: whether a site is new is a fact about this install, and a flag over there would
	have to be remembered and unset."""
	if not seen:
		return 'new'  # no row at all: this panel is being offered the site for the first time
	first = _when(seen.get('first_seen_at'))
	now = now or datetime.now(timezone.utc)
	# A row whose timestamp will not parse gets the weaker of the two claims: 'not registered'
	# is true of every card here, 'new' would be a statement we cannot support.
	return 'new' if first is not None and (now - first).days < NEW_FOR_D else 'unregistered'


def _has_host(hosts: Iterable[str], target: str) -> bool:
	return any(h == target or h.endswith('.' + target) for h in hosts)


def due_for_impression(seen: Optional[dict]) -> bool:
	"""One impression an hour per card. `max_shows` has to mean sessions rather than page
	reloads — the SPA asks for a card on every mount."""
	shown = _when((seen or {}).get('last_shown_at'))
	return shown is None or (datetime.now(timezone.utc) - shown).total_seconds() >= IMPRESSION_GAP_S


def clear_cache() -> None:
	_CACHE.update(at=None, etag=None, cards=[])


async def cards(*, transport: Any = None) -> list:
	"""The manifest's cards, from a short in-process cache (TTL_S). Never raises, never returns None.

	A failed fetch still moves the cache timestamp, so an offline panel retries on the next
	cycle instead of on every page load."""
	if not enabled():
		return []
	now = time.monotonic()
	if _CACHE['at'] is not None and now - _CACHE['at'] < TTL_S:
		return _CACHE['cards']
	manifest, etag = await _fetch(transport=transport, etag=_CACHE['etag'])
	_CACHE['at'] = now
	if manifest is None:  # 304, unreachable, or garbage — keep whatever we had
		return _CACHE['cards']
	_CACHE['etag'] = etag
	_CACHE['cards'] = parse_cards(manifest)
	return _CACHE['cards']


async def _fetch(*, transport: Any = None, etag: Optional[str] = None) -> tuple:
	"""GET the manifest from the first mirror that answers with JSON.

	The only outbound request on this path, and it carries nothing: no query, no body, no
	cookies, no account data. `trust_env=False` for the same reason as `newapi._client` — a
	Clash-style ALL_PROXY makes httpx raise at construction — so the proxy is explicit.

	Direct first, the proxy only as a retry. `panel.sandbox` sets `CHECKIN_PROXY_URL` to a
	Clash address on *every* install, so sending the manifest through it by default means a
	machine with no Clash running never sees a card and is never told why. The proxy matters —
	it is what reaches GitHub from a network that blocks it — but it is the fallback.
	"""
	headers = {'Accept': 'application/json', 'User-Agent': UA}
	if etag:
		headers['If-None-Match'] = etag
	proxy = os.getenv('CHECKIN_PROXY_URL') or None
	for route in (None, proxy) if proxy else (None,):
		async with httpx.AsyncClient(
			timeout=TIMEOUT,
			headers=headers,
			trust_env=False,
			# An injected transport replaces the network, proxy included: httpx mounts its own
			# transport for a proxy and quietly ignores this one, which sent a test straight out
			# to the real CDN on the retry pass.
			proxy=None if transport is not None else route,
			follow_redirects=True,
			transport=transport,
		) as client:
			for url in urls():
				try:
					response = await client.get(url)
					if response.status_code == 304:
						return None, etag
					response.raise_for_status()
					return response.json(), response.headers.get('etag')
				except Exception as e:
					# The URL is ours and the exception type is httpx's; neither says anything
					# about the owner's accounts. Which route failed is the one thing worth
					# knowing here, and the proxy address came from the owner.
					where = '经代理 ' if route else ''
					print(f'[PROMO] 清单拉取失败 {where}{url}: {type(e).__name__}')
	return None, etag
