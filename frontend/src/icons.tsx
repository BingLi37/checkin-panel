import type { FC, SVGProps } from 'react'
import type { LoginMethod } from './api'

type IconProps = SVGProps<SVGSVGElement>

/** Inline SVG rather than an icon package: six glyphs is not worth a dependency, and
 *  `currentColor` lets each call site keep its own text colour. */
const base = (props: IconProps) => ({
  viewBox: '0 0 24 24',
  'aria-hidden': true,
  focusable: false,
  ...props,
  className: `w-4 h-4 ${props.className ?? ''}`.trim(),
})

const GitHubIcon: FC<IconProps> = (props) => (
  <svg {...base(props)} fill="currentColor">
    <path d="M12 2C6.48 2 2 6.58 2 12.23c0 4.52 2.87 8.35 6.84 9.7.5.1.68-.22.68-.49v-1.7c-2.78.62-3.37-1.37-3.37-1.37-.45-1.18-1.11-1.5-1.11-1.5-.91-.64.07-.63.07-.63 1 .07 1.53 1.06 1.53 1.06.9 1.57 2.34 1.12 2.91.85.09-.66.35-1.12.63-1.38-2.22-.26-4.56-1.14-4.56-5.06 0-1.12.39-2.03 1.03-2.75-.1-.26-.45-1.3.1-2.7 0 0 .83-.27 2.75 1.05a9.35 9.35 0 0 1 2.5-.34c.85 0 1.71.12 2.5.34 1.91-1.32 2.75-1.05 2.75-1.05.55 1.4.2 2.44.1 2.7.64.72 1.03 1.63 1.03 2.75 0 3.93-2.35 4.8-4.57 5.06.36.32.68.94.68 1.9v2.81c0 .27.18.6.69.49A10.06 10.06 0 0 0 22 12.23C22 6.58 17.52 2 12 2Z" />
  </svg>
)

const KeyIcon: FC<IconProps> = (props) => (
  <svg {...base(props)} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <circle cx="8" cy="8" r="4" />
    <path d="M11 11l9 9M17 17l2-2M14.5 14.5l2-2" />
  </svg>
)

/** LinuxDO has no official mark, so it gets a letter badge. */
const LinuxDoIcon: FC<IconProps> = (props) => (
  <svg {...base(props)} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="3" width="18" height="18" rx="5" />
    <path d="M9.5 7.5v9h5" />
  </svg>
)

const TokenIcon: FC<IconProps> = (props) => (
  <svg {...base(props)} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <path d="M13.5 3.5 3.5 13.5l7 7 10-10v-7z" />
    <circle cx="16.5" cy="7.5" r="1.6" />
  </svg>
)

const SessionIcon: FC<IconProps> = (props) => (
  <svg {...base(props)} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <rect x="2.5" y="5" width="19" height="14" rx="3" />
    <circle cx="8.5" cy="11.5" r="2" />
    <path d="M5.5 16c.7-1.3 1.8-2 3-2s2.3.7 3 2M14.5 10h4M14.5 13.5h4" />
  </svg>
)

export const SearchIcon: FC<IconProps> = (props) => (
  <svg {...base(props)} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
    <circle cx="10.5" cy="10.5" r="6.5" />
    <path d="M15.5 15.5 21 21" />
  </svg>
)

/** All five methods must be here — `Record` type-errors on a missing key, which is the point. */
const LOGIN_METHOD_META: Record<LoginMethod, { label: string; Icon: FC<IconProps> }> = {
  password: { label: '密码', Icon: KeyIcon },
  access_token: { label: '访问令牌', Icon: TokenIcon },
  session: { label: '会话 Cookie', Icon: SessionIcon },
  linuxdo: { label: 'LinuxDO', Icon: LinuxDoIcon },
  github: { label: 'GitHub', Icon: GitHubIcon },
}

/** A method the panel has not heard of is still an account worth showing: fall back to its raw
 *  value instead of throwing on an undefined lookup, which would blank the whole table. Same
 *  tolerance the avatar palette has for an unknown colour slug. */
export const loginMethodMeta = (method: string) =>
  LOGIN_METHOD_META[method as LoginMethod] ?? { label: method, Icon: KeyIcon }
