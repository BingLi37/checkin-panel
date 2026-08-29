import { Avatar, Popover, PopoverContent, PopoverTrigger } from '@heroui/react'
import {
  AVATAR_COLOR_LABELS,
  AVATAR_COLORS,
  AVATAR_ORB_GLOSS,
  AVATAR_SHAPES,
  AVATAR_SHAPE_LABELS,
  AVATAR_SKINS,
  initials,
  resolveColor,
  resolveShape,
  type AvatarColor,
  type AvatarShape,
} from './avatar'

interface AccountAvatarProps {
  name: string
  color: string | null
  shape: string | null
  /** Deterministic colour for an account nobody has picked one for yet. */
  fallbackColor: AvatarColor
  onPick: (color: AvatarColor, shape: AvatarShape) => void
}

/** Both shapes sit in the same 32px box, so switching one never moves the row. */
function Face({ name, color, shape }: { name: string; color: AvatarColor; shape: AvatarShape }) {
  const skin = AVATAR_SKINS[color]
  if (shape === 'dot') {
    return (
      <Avatar
        size="sm"
        radius="full"
        alt={name}
        // No `name`, so HeroUI renders the icon slot — which the theme already sizes w-full h-full,
        // giving the orb exactly the letter avatar's diameter.
        icon={
          <span className={`relative block h-full w-full rounded-full ${skin.dot}`}>
            <span className={`absolute inset-0 rounded-full ${AVATAR_ORB_GLOSS}`} />
          </span>
        }
        classNames={{ base: 'bg-transparent ring-1 ring-inset ring-black/5' }}
      />
    )
  }
  return (
    <Avatar
      size="sm"
      radius="full"
      name={name}
      // Default getInitials splits on separators and would turn 'AG' back into 'A'.
      getInitials={initials}
      // The 8-colour palette cannot go through `color`: that prop only has HeroUI's 6 semantic values.
      classNames={{ base: skin.letter, name: 'font-semibold' }}
    />
  )
}

export default function AccountAvatar({ name, color, shape, fallbackColor, onPick }: AccountAvatarProps) {
  const picked = resolveColor(color, fallbackColor)
  const pickedShape = resolveShape(shape)

  // The trigger sits inside a selectable table row, whose react-aria press handler would
  // otherwise tick the row's checkbox every time someone opens the picker. Stopping the
  // bubble here — outside PopoverTrigger, whose own props on the button must stay intact —
  // lets the button handle the press first and keeps the row out of it.
  const swallow = (e: { stopPropagation: () => void }) => e.stopPropagation()

  return (
    <span className="shrink-0" onPointerDown={swallow} onMouseDown={swallow} onClick={swallow}>
      <Popover placement="bottom-start" showArrow>
        <PopoverTrigger>
          <button
            type="button"
            aria-label={`设置「${name}」的头像`}
            className="rounded-full outline-none transition-transform hover:scale-105 focus-visible:ring-2 focus-visible:ring-focus"
          >
            <Face name={name} color={picked} shape={pickedShape} />
          </button>
        </PopoverTrigger>
        <PopoverContent className="px-3 py-3">
          <div className="w-[176px] space-y-3">
            <div className="space-y-1.5">
              <p className="text-tiny text-default-500">样式</p>
              <div className="flex gap-2">
                {AVATAR_SHAPES.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => onPick(picked, s)}
                    className={`flex flex-1 items-center gap-2 rounded-medium px-2 py-1 text-tiny outline-none transition-colors focus-visible:ring-2 focus-visible:ring-focus ${
                      s === pickedShape ? 'bg-default-200 font-medium' : 'hover:bg-default-100'
                    }`}
                  >
                    <Face name={name} color={picked} shape={s} />
                    {AVATAR_SHAPE_LABELS[s]}
                  </button>
                ))}
              </div>
            </div>
            <div className="space-y-1.5">
              <p className="text-tiny text-default-500">颜色</p>
              <div className="grid grid-cols-4 gap-2">
                {AVATAR_COLORS.map((c) => (
                  <button
                    key={c}
                    type="button"
                    aria-label={AVATAR_COLOR_LABELS[c]}
                    aria-pressed={c === picked}
                    onClick={() => onPick(c, pickedShape)}
                    className={`h-6 w-6 rounded-full outline-none transition-transform hover:scale-110 focus-visible:ring-2 focus-visible:ring-focus ${AVATAR_SKINS[c].swatch} ${
                      c === picked ? 'ring-2 ring-default-600 ring-offset-2 ring-offset-content1' : ''
                    }`}
                  />
                ))}
              </div>
            </div>
          </div>
        </PopoverContent>
      </Popover>
    </span>
  )
}
