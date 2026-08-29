export const AVATAR_COLORS = ['slate', 'blue', 'violet', 'emerald', 'amber', 'rose', 'cyan', 'fuchsia'] as const
export const AVATAR_SHAPES = ['letter', 'dot'] as const

export type AvatarColor = (typeof AVATAR_COLORS)[number]
export type AvatarShape = (typeof AVATAR_SHAPES)[number]

/** Every class is spelled out on purpose: Tailwind's JIT scans source text, so a class
 *  built as `bg-${color}-500` never gets any CSS emitted for it. */
export const AVATAR_SKINS: Record<AvatarColor, { letter: string; dot: string; swatch: string }> = {
  slate: {
    letter: 'bg-slate-500 text-white',
    dot: 'bg-gradient-to-br from-slate-200 via-slate-400 to-slate-700',
    swatch: 'bg-slate-500',
  },
  blue: {
    letter: 'bg-blue-500 text-white',
    dot: 'bg-gradient-to-br from-blue-200 via-blue-400 to-blue-700',
    swatch: 'bg-blue-500',
  },
  violet: {
    letter: 'bg-violet-500 text-white',
    dot: 'bg-gradient-to-br from-violet-200 via-violet-400 to-violet-700',
    swatch: 'bg-violet-500',
  },
  emerald: {
    letter: 'bg-emerald-500 text-white',
    dot: 'bg-gradient-to-br from-emerald-200 via-emerald-400 to-emerald-700',
    swatch: 'bg-emerald-500',
  },
  amber: {
    letter: 'bg-amber-500 text-white',
    dot: 'bg-gradient-to-br from-amber-200 via-amber-400 to-amber-700',
    swatch: 'bg-amber-500',
  },
  rose: {
    letter: 'bg-rose-500 text-white',
    dot: 'bg-gradient-to-br from-rose-200 via-rose-400 to-rose-700',
    swatch: 'bg-rose-500',
  },
  cyan: {
    letter: 'bg-cyan-500 text-white',
    dot: 'bg-gradient-to-br from-cyan-200 via-cyan-400 to-cyan-700',
    swatch: 'bg-cyan-500',
  },
  fuchsia: {
    letter: 'bg-fuchsia-500 text-white',
    dot: 'bg-gradient-to-br from-fuchsia-200 via-fuchsia-400 to-fuchsia-700',
    swatch: 'bg-fuchsia-500',
  },
}

export const AVATAR_SHAPE_LABELS: Record<AvatarShape, string> = { letter: '字母', dot: '小球' }

/** The gloss on the 小球 shape: a white highlight up and to the left plus a soft shade
 *  opposite it, which is what makes a flat gradient read as a sphere. Hue-independent, so it
 *  is one shared class rather than a fourth string per palette colour. */
export const AVATAR_ORB_GLOSS =
  'bg-[radial-gradient(circle_at_30%_24%,rgba(255,255,255,0.95)_0%,rgba(255,255,255,0.45)_26%,rgba(255,255,255,0)_58%),radial-gradient(circle_at_76%_80%,rgba(0,0,0,0.16)_0%,rgba(0,0,0,0)_48%)]'

export const AVATAR_COLOR_LABELS: Record<AvatarColor, string> = {
  slate: '石板灰',
  blue: '蓝',
  violet: '紫',
  emerald: '翠绿',
  amber: '琥珀',
  rose: '玫红',
  cyan: '青',
  fuchsia: '洋红',
}

/** The name's literal first two characters (D1), not per-segment initials. */
export const initials = (name: string) => name.slice(0, 2).toUpperCase()

/** Exported for PromoCard, which needs the same "pick a palette from a string" behaviour. */
export function hash(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (Math.imul(h, 31) + s.charCodeAt(i)) | 0
  return h >>> 0 // >>> not Math.abs: abs(-2**31) is still negative
}

/** Default colour per account, guaranteed distinct inside a same-initials group.
 *
 * The literal first two characters collide for 11 of the 14 live accounts (AG×2, AN×2,
 * TA×3, GO×3), so colour is the only thing that tells those rows apart — a plain
 * `hash % 8` would let two of them land on the same one. Group order is by ascending id
 * so a new account joins at the tail and never shifts the colours already on screen. */
export function defaultSkins(accounts: { id: number; name: string }[]): Map<number, AvatarColor> {
  const groups = new Map<string, { id: number; name: string }[]>()
  for (const acc of accounts) {
    const key = initials(acc.name)
    const group = groups.get(key)
    if (group) group.push(acc)
    else groups.set(key, [acc])
  }

  const skins = new Map<number, AvatarColor>()
  for (const group of groups.values()) {
    const taken = new Set<number>()
    for (const acc of [...group].sort((a, b) => a.id - b.id)) {
      const preferred = hash(acc.name) % AVATAR_COLORS.length
      let slot = preferred
      // Linear probe to a free slot. Bounded by the palette size, so a group of more than
      // 8 falls back to the preferred slot instead of spinning forever.
      for (let step = 0; step < AVATAR_COLORS.length && taken.has(slot); step++) {
        slot = (slot + 1) % AVATAR_COLORS.length
      }
      taken.add(slot)
      skins.set(acc.id, AVATAR_COLORS[slot])
    }
  }
  return skins
}

/** Stored slugs are validated for shape only, not membership (the palette lives here and
 *  nowhere else), so an unknown value has to degrade instead of rendering broken. */
export const resolveColor = (slug: string | null, fallback: AvatarColor): AvatarColor =>
  (AVATAR_COLORS as readonly string[]).includes(slug ?? '') ? (slug as AvatarColor) : fallback

export const resolveShape = (slug: string | null): AvatarShape =>
  (AVATAR_SHAPES as readonly string[]).includes(slug ?? '') ? (slug as AvatarShape) : 'letter'
