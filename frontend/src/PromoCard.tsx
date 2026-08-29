import { useEffect, useState } from 'react'
import { api, type Promo } from './api'
import { hash } from './avatar'

/** The panel stays open for days, so a card that only arrived on mount would never arrive. Half an
 *  hour rather than the backend's 5-minute manifest TTL: a newly published card is worth seeing the
 *  same day, and a reload picks one up at once anyway. */
const POLL_MS = 30 * 60 * 1000

/** The pastel mesh from the reference. Geometry — where each blob sits, how far it reaches — was
 *  measured once and is shared by every palette; colour is the only thing a palette changes. It
 *  cannot be a Tailwind class (the JIT only sees literal strings, and one arbitrary value holding
 *  seven gradients is unreadable), so it stays an inline style.
 *
 *  Order matters: the first layer paints on top, and a pale wash listed first covers every colour
 *  under it — that is what turned the whole panel lavender on the first pass — so the saturated
 *  blobs go first and the wash sits fifth. */
const BLOBS: [string, number][] = [
  ['70% 62% at 97% 97%', 58],
  ['58% 52% at 92% 12%', 62],
  ['70% 76% at 0% 50%', 64],
  ['62% 55% at 26% 92%', 60],
  ['52% 42% at 20% 8%', 62],
  ['58% 52% at 48% 40%', 68],
]

/** Every layer fades to its own colour at alpha 0, never to `transparent`: `transparent` is
 *  transparent *black*, and interpolating to it greys the edge of each blob. */
function fade(hex: string): string {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},0)`
}

const mesh = (blobs: string[], base: string[]) =>
  [
    ...BLOBS.map(([at, stop], i) => `radial-gradient(${at}, ${blobs[i]} 0%, ${fade(blobs[i])} ${stop}%)`),
    `linear-gradient(145deg, ${base[0]} 0%, ${base[1]} 38%, ${base[2]} 72%, ${base[3]} 100%)`,
  ].join(', ')

/** One palette per 公益站, so no two sites wear the same hero. The manifest chooses by *name* and
 *  this table is the entire vocabulary — a remote string that reached CSS would be an injection
 *  point, so a name that is not a key here is discarded rather than used. */
const MESHES: Record<string, string> = {
  violet: mesh(['#6fd0fb', '#f5a2df', '#8f68f6', '#8fa4f7', '#dfe6ff', '#b899fa'], ['#c9b4fb', '#b39bf8', '#a9c6f9', '#86dcf8']),
  sky: mesh(['#7ef0e0', '#a5c8ff', '#3fa9f5', '#7fd4ff', '#e6f4ff', '#6fc3f7'], ['#bfe6fb', '#9ed6f8', '#8fc4f5', '#7ce0ef']),
  mint: mesh(['#9ff5c9', '#6fe3c0', '#35c48f', '#b6f0a8', '#e9fff4', '#7ae0b5'], ['#c7f6dd', '#a3ecc6', '#b6efb2', '#86e8cf']),
  lime: mesh(['#e6f77a', '#b9e84f', '#8bd12a', '#d7f58f', '#f6ffdd', '#c4ea6a'], ['#e8f8b8', '#d3f08d', '#bde86a', '#e7fbb4']),
  amber: mesh(['#ffd88a', '#ffb765', '#ff9a3d', '#ffe3a3', '#fff6e2', '#ffca7a'], ['#ffe6b8', '#ffd493', '#ffc27a', '#ffeecb']),
  rose: mesh(['#ffb3c7', '#ff8fb3', '#f4658f', '#ffc9d6', '#fff1f5', '#ff9fbd'], ['#ffd6e2', '#ffbdd0', '#ffa9c2', '#ffe1ea']),
  coral: mesh(['#ffc09f', '#ff9a7b', '#f76b52', '#ffd2b3', '#fff3ea', '#ffab8a'], ['#ffdcc9', '#ffc3a8', '#ffb094', '#ffe7d8']),
  indigo: mesh(['#a9b6ff', '#7c8cf8', '#4a5ae0', '#c3caff', '#eef0ff', '#8f9dfb'], ['#ccd2ff', '#b0b9fb', '#98a3f6', '#dfe4ff']),
}
const THEMES = Object.keys(MESHES)

/** An unnamed (or unknown) theme falls back to a hash of the card id, so a site published without
 *  one still looks different from its neighbours without an edit here. */
const meshFor = (card: Promo) =>
  MESHES[THEMES.includes(card.theme ?? '') ? (card.theme as string) : THEMES[hash(card.id) % THEMES.length]]

/** Two claims, and only two: a site this panel was only just offered is 全新, anything else is one
 *  the owner simply has not registered. `panel/promo.py` decides which; this is only how it looks. */
const STICKERS: Record<string, { label: string; skin: string }> = {
  new: { label: '全新', skin: 'bg-[#c9f24d] text-[#10131a]' },
  unregistered: { label: '未注册', skin: 'bg-[#12131a] text-white' },
}

function CloseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  )
}

export default function PromoCard() {
  const [card, setCard] = useState<Promo | null>(null)
  // Mount transparent and fade in: a fixed panel that appears mid-paint reads as a glitch.
  const [shown, setShown] = useState(false)

  useEffect(() => {
    let alive = true
    const load = () =>
      api
        .promo()
        .then((data) => {
          if (!alive) return
          setCard(data.card)
          if (data.card) setTimeout(() => alive && setShown(true), 60)
        })
        // A promo that cannot load is nothing the owner needs to hear about.
        .catch(() => {})
    load()
    const timer = setInterval(load, POLL_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  if (!card) return null

  const close = () => {
    // Optimistic and un-rollbackable on purpose: whatever the write does, something the user
    // closed stays closed. The backend is where the cooldown is remembered, and it doubles.
    setCard(null)
    setShown(false)
    api.dismissPromo(card.id).catch(() => {})
  }

  const hero = card.hero
  const sticker = STICKERS[card.sticker] ?? STICKERS.unregistered

  return (
    <>
      {/* Fixed means out of flow: at max scroll the card sits on the last rows' 操作 buttons and
          they cannot be clicked. This scrolls them clear, and disappears when the card does. */}
      <div aria-hidden="true" className="h-[480px]" />

      {/* One width everywhere: going full-bleed below sm made the card 63% of a 844px-tall
          phone screen (measured), and the 4:3 hero means width is the only lever on that.
          z-40 keeps it under the toasts (z-50) and under HeroUI's modal overlay. */}
      <aside
        aria-label={`推荐公益站：${card.title}`}
        className={`fixed bottom-3 right-3 z-40 w-[min(344px,calc(100vw-1.5rem))] sm:bottom-5 sm:right-5 transition-all duration-300 ease-out ${
          shown ? 'translate-y-0 opacity-100' : 'translate-y-3 opacity-0'
        }`}
      >
        <div className="relative rounded-[26px] border border-black/5 bg-white p-3.5 shadow-[0_20px_60px_-16px_rgba(16,18,32,0.45)]">
          {/* The sticker overhangs the corner, so it hangs off this box rather than living inside
              the hero's overflow-hidden. -right-2 still leaves a margin at the viewport edge. */}
          <span
            className={`absolute -right-2 -top-3 z-10 overflow-hidden rounded-[13px] border-[3px] border-white px-2.5 py-1 text-[13px] font-semibold leading-tight rotate-[9deg] shadow-[0_6px_16px_-4px_rgba(16,18,32,0.5)] ${sticker.skin}`}
          >
            {sticker.label}
            {/* 反光 — a band of light crossing the sticker. Decorative, and off under
                prefers-reduced-motion (index.css). */}
            <span
              aria-hidden="true"
              className="animate-shine pointer-events-none absolute inset-y-0 left-0 w-[45%] bg-gradient-to-r from-transparent via-white/70 to-transparent"
            />
          </span>

          <div className="relative aspect-[4/3] overflow-hidden rounded-[18px]" style={{ background: meshFor(card) }}>
            <button
              type="button"
              onClick={close}
              aria-label={`关闭卡片：${card.title}`}
              className="absolute left-2.5 top-2.5 flex h-7 w-7 items-center justify-center rounded-lg bg-white/45 text-[#131522]/70 backdrop-blur-sm transition hover:bg-white/75 focus-visible:ring-2 focus-visible:ring-[#131522]/50"
            >
              <CloseIcon />
            </button>
            {/* Dark ink on a pastel hero, lifted by a white glow rather than a shadow: it holds up
                on the mid-tone palettes (violet/indigo) without washing out the pale ones. */}
            <div className="flex h-full flex-col items-center justify-center gap-7 px-4">
              {(hero.title || hero.subtitle) && (
                <div className="text-center [text-shadow:0_1px_10px_rgba(255,255,255,0.5)]">
                  {hero.title && <p className="text-[27px] font-medium leading-none tracking-tight text-[#131522]">{hero.title}</p>}
                  {hero.subtitle && <p className="mt-2.5 text-[15px] text-[#131522]/75">{hero.subtitle}</p>}
                </div>
              )}
              {(hero.brand || hero.badge) && (
                <div className="flex items-center gap-1.5 text-[#131522]/80">
                  {hero.brand && <span className="text-[15px]">{hero.brand}</span>}
                  {hero.badge && <span className="rounded-md border border-[#131522]/35 px-1.5 py-px text-[11px]">{hero.badge}</span>}
                </div>
              )}
            </div>
          </div>

          <div className="px-1.5 pb-0.5 pt-4">
            <h2 className="text-[19px] font-semibold text-[#0b0c12]">{card.title}</h2>
            {card.body && <p className="mt-2 text-[13px] leading-relaxed text-black/50">{card.body}</p>}
            {/* Hand-rolled, so it needs its own focus ring: index.css kills the UA one.
                The href is remote input; panel/promo.py has already refused anything but https. */}
            <a
              href={card.cta.url}
              target="_blank"
              rel="noreferrer"
              className="mt-4 flex h-11 items-center justify-center rounded-xl bg-[#0b0c12] font-mono text-[13px] tracking-wide text-white transition hover:bg-[#0b0c12]/85 focus-visible:ring-2 focus-visible:ring-[#0b0c12]/60"
            >
              {card.cta.label}
            </a>
          </div>
        </div>
      </aside>
    </>
  )
}
