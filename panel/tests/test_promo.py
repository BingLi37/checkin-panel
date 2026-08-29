"""Promo manifest tests — parsing untrusted JSON, local targeting, and what goes out.

Nothing here touches the network: every fetch is served by an httpx.MockTransport that
records what it was asked for, because "the panel uploads nothing" is a claim about the
requests themselves and not just about the answer.
"""
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from panel import promo

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
	monkeypatch.delenv('PANEL_PROMO', raising=False)
	monkeypatch.delenv('PANEL_PROMO_URL', raising=False)
	monkeypatch.delenv('CHECKIN_PROXY_URL', raising=False)
	promo.clear_cache()
	yield
	promo.clear_cache()


class Manifest:
	"""A recording stand-in for the CDN. Replies are callables so each request gets a fresh
	Response; the last one repeats."""

	def __init__(self, *replies):
		self.replies = list(replies)
		self.calls = []

	def handler(self, request: httpx.Request) -> httpx.Response:
		self.calls.append(request)
		return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)](request)

	@property
	def transport(self) -> httpx.MockTransport:
		return httpx.MockTransport(self.handler)


def serves(cards, *, etag=None):
	headers = {'etag': etag} if etag else {}
	return lambda request: httpx.Response(200, json={'version': 1, 'cards': cards}, headers=headers)


def breaks(status=500, text='nope'):
	return lambda request: httpx.Response(status, text=text)


def one(**raw) -> promo.Card:
	"""One card straight through the parser — the schema defaults belong to the manifest, so
	a targeting test that hand-built a Card would not notice them changing."""
	card = {'id': 'c', 'title': 'T', 'cta': {'url': 'https://site.test/r?aff=1'}}
	card.update(raw)
	cards = promo.parse_cards({'version': 1, 'cards': [card]})
	assert cards, 'the fixture itself has to be a valid card'
	return cards[0]


def picked(*cards, hosts=(), age=999.0, accounts=1, state=None, now=NOW, rng=None):
	"""pick() draws at random, so every test here pins the draw — a seeded Random makes the
	single-card assertions ('this card is/is not eligible') deterministic again."""
	return promo.pick(
		list(cards),
		hosts=set(hosts),
		panel_age_h=age,
		account_count=accounts,
		state=state or {},
		now=now,
		rng=rng or random.Random(0),
	)


def account(base_url='https://a.test', created_at='2026-08-01T00:00:00+00:00'):
	return SimpleNamespace(base_url=base_url, created_at=created_at)


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'http://site.test/r', 'data:text/html,x', '//site.test/r', ''])
def test_a_cta_that_is_not_https_drops_the_card(url):
	"""The manifest is remote input and cta.url lands in an href, so anything but https is
	dropped rather than repaired."""
	assert promo.parse_cards({'version': 1, 'cards': [{'id': 'c', 'title': 'T', 'cta': {'url': url}}]}) == []


@pytest.mark.parametrize(
	'manifest',
	[
		None,
		'not json at all',
		[{'id': 'c'}],
		{'cards': []},
		{'version': 2, 'cards': [{'id': 'c', 'title': 'T', 'cta': {'url': 'https://s.test/r'}}]},
		{'version': 1},
		{'version': 1, 'cards': 'nope'},
	],
)
def test_a_broken_manifest_yields_no_cards_and_no_exception(manifest):
	assert promo.parse_cards(manifest) == []


def test_one_bad_card_does_not_take_the_others_with_it():
	cards = promo.parse_cards(
		{
			'version': 1,
			'cards': [
				{'id': '', 'title': 'no id', 'cta': {'url': 'https://s.test/r'}},
				'not even a dict',
				{'id': 'good', 'title': 'T', 'cta': {'url': 'https://s.test/r'}},
				{'id': 'no-title', 'cta': {'url': 'https://s.test/r'}},
			],
		}
	)

	assert [c.id for c in cards] == ['good']


def test_defaults_fill_in_a_minimal_card():
	card = one()

	assert (card.body, card.priority, card.missing_hosts) == ('', 0, ())
	assert (card.cooldown_days, card.max_shows, card.min_accounts) == (1, 0, 0)
	assert card.theme is None, 'no palette named means the browser picks one from the id'
	assert card.cta_label, 'a card with no label still needs a clickable one'
	assert card.payload()['hero'] == {'title': None, 'subtitle': None, 'brand': None, 'badge': None}


def test_a_site_the_owner_already_has_hides_its_card():
	card = one(target={'missing_hosts': ['seekai.cc']})

	assert picked(card, hosts={'other.test'}) is card
	assert picked(card, hosts={'seekai.cc'}) is None, 'the one thing this mechanism exists to avoid'
	assert picked(card, hosts={'api.seekai.cc'}) is None, 'a subdomain counts as having the site'
	assert picked(card, hosts={'notseekai.cc'}) is card, 'a suffix that is not a subdomain is a different site'


def test_hosts_and_age_come_from_the_accounts_themselves():
	accounts = [account('https://WWW.Seekai.cc/'), account('https://api.x.test/console')]

	assert promo.hosts_of(accounts) == {'seekai.cc', 'api.x.test'}
	assert promo.panel_age_h([], now=NOW) == 0.0, 'nobody with no accounts has been using the panel'
	fresh = account(created_at=(NOW - timedelta(hours=3)).isoformat())
	assert promo.panel_age_h([fresh, accounts[0]], now=NOW) > 600, 'the oldest account sets the age'
	assert promo.panel_age_h([fresh], now=NOW) == pytest.approx(3.0)


def test_a_panel_too_new_or_too_empty_sees_nothing():
	card = one(target={'min_panel_age_h': 12, 'min_accounts': 1})

	assert picked(card, age=11.9) is None
	assert picked(card, age=12.0) is card
	assert picked(card, age=99.0, accounts=0) is None


def test_the_live_window_is_a_date_range():
	assert picked(one(show={'starts_at': '2026-08-28'})) is None
	assert picked(one(show={'starts_at': '2026-08-27'})) is not None, 'inclusive on the first day'
	assert picked(one(show={'expires_at': '2026-08-26'})) is None
	assert picked(one(show={'expires_at': '2026-08-27'})) is not None, 'inclusive on the last day'


def test_dismissal_cools_down_and_then_comes_back():
	card = one(show={'cooldown_days': 14})
	dismissed = {'c': {'dismissed_at': (NOW - timedelta(days=13)).isoformat(), 'shows': 3, 'dismissals': 1}}
	expired = {'c': {'dismissed_at': (NOW - timedelta(days=14, hours=1)).isoformat(), 'shows': 3, 'dismissals': 1}}

	assert picked(card, state=dismissed) is None
	assert picked(card, state=expired) is card
	assert picked(card, state={'c': {'dismissed_at': 'not a timestamp'}}) is card, 'junk state must not hide a card forever'


def test_the_cooldown_doubles_every_time_the_card_is_closed():
	"""Closing something twice is a stronger no than closing it once."""
	card = one(show={'cooldown_days': 1})

	assert [promo.cooldown_for(card, {'dismissals': n}) for n in (1, 2, 3, 4)] == [1, 2, 4, 8]
	assert promo.cooldown_for(card, {}) == 1, 'a card nobody closed still knows its base'
	assert promo.cooldown_for(card, {'dismissals': 99}) == 1024, 'capped, or the exponent runs away'

	def closed(days, times):
		return {'c': {'dismissed_at': (NOW - timedelta(days=days)).isoformat(), 'dismissals': times}}

	assert picked(card, state=closed(1, 1)) is card, 'a day after the first close it is back'
	assert picked(card, state=closed(1, 2)) is None, 'after the second close a day is not enough'
	assert picked(card, state=closed(2, 2)) is card


def test_closing_one_card_leaves_the_others_coming_up():
	"""The count and the timer are both per card: waving off one site says nothing about the
	other sites the owner has not registered."""
	a, b, c = one(id='a'), one(id='b'), one(id='c')
	state = {'a': {'dismissed_at': NOW.isoformat(), 'dismissals': 3}}
	rng = random.Random(3)  # one stream across the draws — a fresh seed each time draws the same card

	drawn = {picked(a, b, c, state=state, rng=rng).id for _ in range(200)}

	assert drawn == {'b', 'c'}


def test_a_card_reads_as_new_until_this_panel_has_had_it_a_few_days():
	assert promo.sticker_for(None) == 'new', 'never offered here before'
	assert promo.sticker_for({}) == 'new'
	assert promo.sticker_for({'first_seen_at': (NOW - timedelta(days=2)).isoformat()}, now=NOW) == 'new'
	assert promo.sticker_for({'first_seen_at': (NOW - timedelta(days=3)).isoformat()}, now=NOW) == 'unregistered'
	assert promo.sticker_for({'shows': 1, 'first_seen_at': 'junk'}) == 'unregistered', 'never claim more than we know'


def test_max_shows_stops_an_endless_card():
	card = one(show={'max_shows': 3})

	assert picked(card, state={'c': {'shows': 2}}) is card
	assert picked(card, state={'c': {'shows': 3}}) is None
	assert picked(one(), state={'c': {'shows': 999}}) is not None, 'max_shows 0 means no limit'


def test_every_eligible_card_comes_up_and_priority_is_only_the_weight():
	"""Ranked picking meant the second-best site was never offered until the first was closed."""
	a, b, rare = one(id='a', priority=10), one(id='b', priority=10), one(id='rare', priority=1)
	rng = random.Random(7)

	drawn = Counter(picked(a, b, rare, rng=rng).id for _ in range(600))

	assert set(drawn) == {'a', 'b', 'rare'}, 'every site the owner lacks has to come up'
	assert drawn['rare'] < drawn['a'], 'a bigger priority is offered more often, not first'
	assert 0.7 < drawn['a'] / drawn['b'] < 1.4, 'equal weights, comparable share'
	assert picked() is None


def test_impressions_are_counted_once_an_hour():
	assert promo.due_for_impression(None) is True
	assert promo.due_for_impression({'last_shown_at': datetime.now(timezone.utc).isoformat()}) is False
	stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
	assert promo.due_for_impression({'last_shown_at': stale}) is True


async def test_the_second_mirror_answers_when_the_first_is_down():
	site = Manifest(breaks(500), serves([{'id': 'c', 'title': 'T', 'cta': {'url': 'https://s.test/r'}}]))

	cards = await promo.cards(transport=site.transport)

	assert [c.id for c in cards] == ['c']
	assert [str(r.url) for r in site.calls] == list(promo.MIRRORS)


async def test_a_configured_proxy_is_a_retry_and_not_the_route(monkeypatch):
	"""`run.py` points CHECKIN_PROXY_URL at Clash on every install, so a manifest sent through
	it by default would never load on a machine that runs no Clash."""
	monkeypatch.setenv('CHECKIN_PROXY_URL', 'http://127.0.0.1:7897')
	site = Manifest(serves([{'id': 'c', 'title': 'T', 'cta': {'url': 'https://s.test/r'}}]))

	cards = await promo.cards(transport=site.transport)

	assert [c.id for c in cards] == ['c']
	assert len(site.calls) == 1, 'the direct attempt answered — nothing should go through the proxy'


async def test_a_dead_direct_route_retries_every_mirror_through_the_proxy(monkeypatch):
	monkeypatch.setenv('CHECKIN_PROXY_URL', 'http://127.0.0.1:7897')
	site = Manifest(breaks(500))

	assert await promo.cards(transport=site.transport) == []
	assert [str(r.url) for r in site.calls] == list(promo.MIRRORS) * 2


async def test_the_request_carries_nothing_but_the_url():
	"""AC6: the whole privacy story is that this GET says nothing about the owner."""
	site = Manifest(serves([]))

	await promo.cards(transport=site.transport)

	request = site.calls[0]
	assert request.method == 'GET'
	assert request.url.query == b'' and not request.content
	assert 'cookie' not in request.headers and 'authorization' not in request.headers
	assert request.headers['accept'] == 'application/json'
	assert request.headers['user-agent'] == promo.UA


async def test_a_manifest_that_is_not_json_leaves_the_panel_with_no_cards():
	site = Manifest(lambda request: httpx.Response(200, text='<html>rate limited</html>'))

	assert await promo.cards(transport=site.transport) == []
	assert len(site.calls) == 2, 'a garbage answer is a reason to try the other mirror'


async def test_disabled_makes_no_request_at_all(monkeypatch):
	monkeypatch.setenv('PANEL_PROMO', '0')
	site = Manifest(serves([{'id': 'c', 'title': 'T', 'cta': {'url': 'https://s.test/r'}}]))

	assert await promo.cards(transport=site.transport) == []
	assert site.calls == [], 'PANEL_PROMO=0 must short-circuit before any client exists'


async def test_an_override_url_replaces_the_mirrors(monkeypatch):
	monkeypatch.setenv('PANEL_PROMO_URL', 'https://mine.test/promos.json')
	site = Manifest(serves([]))

	await promo.cards(transport=site.transport)

	assert [str(r.url) for r in site.calls] == ['https://mine.test/promos.json']


async def test_the_cache_answers_the_second_call():
	site = Manifest(serves([{'id': 'c', 'title': 'T', 'cta': {'url': 'https://s.test/r'}}]))

	first = await promo.cards(transport=site.transport)
	second = await promo.cards(transport=site.transport)

	assert first == second and len(site.calls) == 1


async def test_a_304_keeps_the_cards_and_spends_the_etag():
	site = Manifest(serves([{'id': 'c', 'title': 'T', 'cta': {'url': 'https://s.test/r'}}], etag='"v1"'),
					lambda request: httpx.Response(304))

	await promo.cards(transport=site.transport)
	promo._CACHE['at'] = None  # the only way to age out a 6h TTL in a test
	cards = await promo.cards(transport=site.transport)

	assert [c.id for c in cards] == ['c']
	assert site.calls[1].headers['if-none-match'] == '"v1"'
