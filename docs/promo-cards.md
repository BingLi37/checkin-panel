# Promo cards

The panel can show one card in the bottom-right corner: a 公益站 the owner does not have yet,
with a registration link that is the author's affiliate link — clicking it is how the author is
paid for the panel. The card wears a sticker in its top-right corner saying either 全新 (a site
this panel has only just been offered) or 未注册. This page is the full disclosure of what the
card costs you.

## What is fetched

One `GET` per manifest check, for a static JSON manifest, from the first of these that
answers:

1. `https://raw.githubusercontent.com/BingLi37/Welfare-Express/promos/promos.json`
2. `https://cdn.jsdelivr.net/gh/BingLi37/Welfare-Express@promos/promos.json`

The raw host is first because it is the fresher of the two (`max-age=300`, against
jsDelivr's 12-hour edge cache); jsDelivr stays in the list because it is the mirror that
answers on a network which blocks GitHub. Whichever one answers, the request is the same.

The manifest is cached in the panel for 5 minutes — the raw host's own `max-age`, so asking
more often cannot see anything newer — and the page asks twice an hour, so one conditional
`GET` every half hour is the whole of it while the panel is open. A repeat answer is a 304 with
no body.

The request has no query string, no body, no cookies, and no authentication. It sends
`Accept`, `User-Agent: auto-checkin-panel`, and `If-None-Match` when a previous answer had an
ETag. That is the entire outbound surface of this feature — `panel/promo.py` is the only code
that makes it, and `panel/tests/test_promo.py` asserts the shape of the request.

## What is not sent

Nothing about you. Which sites you have, how many accounts, your balances, your usernames,
whether you ever clicked anything: none of it leaves the machine. Every targeting rule
("show this only to someone who does not have seekai.cc yet", "only after the panel has been
in use for 12 hours") is evaluated locally, against `data/panel.db`, after the manifest has
already been downloaded in full.

The affiliate link is a plain `https://` URL. The site on the other end sees you arriving
from a click, the same as any other referral link; the panel adds nothing to it.

## Turning it off

```bat
set PANEL_PROMO=0
```

No fetch happens at all — the check is before any HTTP client is constructed. To audit the
mechanism with your own manifest instead:

```bat
set PANEL_PROMO_URL=http://127.0.0.1:8899/promos.json
```

An override replaces both mirrors. Closing a card is remembered in `data/panel.db`
(`promo_state`), not in the browser, so it stays closed after a cache clear.

Closing is per card, and it escalates: the card you closed stays away for its `cooldown_days`,
then twice that if you close it again, then four times (1 → 2 → 4 → 8 days, capped). The sites
you did not close keep coming up in the meantime — both the timer and the count are stored
against one `promo_id`. Nothing you do to a card is sent anywhere; it is a row in your own
database.

## Publishing (author only)

The manifest lives in the author's public
[Welfare-Express](https://github.com/BingLi37/Welfare-Express) repo, not in this one: the
code repository keeps no promotional content in its history, and a card can be published
without cutting a release here. It sits on that repo's `promos` branch rather than `main`,
which is the branch its readers see. Edit `promos.json` there and commit: the raw mirror is
live within its 5-minute cache, so a running panel picks the change up within about ten minutes,
and a restarted one at once.
jsDelivr needs a purge, and the purge cannot be trusted — measured 2026-08-27:
`purge.jsdelivr.net` answered 200 for this exact path while one Cloudflare edge kept
serving the previous body for another 8 minutes, the new one already being live on
`fastly.`/`gcore.jsdelivr.net`. Purge anyway, and expect a panel served by that edge to be
up to 12 hours behind:

```bash
curl https://purge.jsdelivr.net/gh/BingLi37/Welfare-Express@promos/promos.json
```

Shape (`version` must be `1`; a card with a malformed field is dropped on its own, and a
`cta.url` that is not `https://` is refused):

```json
{
  "version": 1,
  "cards": [
    {
      "id": "seekai-2026-08",
      "priority": 10,
      "theme": "violet",
      "hero": {"title": "每天 $20 额度", "subtitle": "注册即用", "brand": "SeekAI", "badge": "公益站"},
      "title": "新公益站：SeekAI",
      "body": "支持 Claude / GPT，签到每天 $20，面板可直接托管每日签到。",
      "cta": {"label": "去注册 →", "url": "https://seekai.cc/register?aff=xxxx"},
      "target": {"missing_hosts": ["seekai.cc"], "min_panel_age_h": 12, "min_accounts": 1},
      "show": {"cooldown_days": 1, "max_shows": 0, "starts_at": null, "expires_at": "2026-12-31"}
    }
  ]
}
```

`missing_hosts` matches subdomains (`api.seekai.cc` counts as having `seekai.cc`), so an owner
who already added the site never sees its card.

When several cards match, one is drawn **at random** on every load and `priority` is only the
weight — a card with 30 comes up ten times as often as one with 3, but every matching card comes
up. (Ranking them meant the second site was never offered until the first was closed or
registered.) `cooldown_days` is the *first* cooldown after a close and doubles on each further
close; `max_shows: 0` means no display cap, and any other number retires the card after that many
hours in which it was seen.

`theme` names one of the hero palettes the panel ships — `violet`, `sky`, `mint`, `lime`, `amber`,
`rose`, `coral`, `indigo` — so each site is recognisable by colour. It is a name and nothing else:
the panel looks it up in its own table and ignores anything that is not on that list, falling back
to a colour derived from the card `id`. A manifest can never send CSS, markup, or a class name.
