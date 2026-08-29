export type LoginMethod = 'password' | 'access_token' | 'session' | 'linuxdo' | 'github'
export type Mechanism = 'auto' | 'visit'

export interface Account {
  id: number
  name: string
  base_url: string
  login_method: LoginMethod
  mechanism: Mechanism | null
  username: string | null
  password: string | null
  access_token: string | null
  session: string | null
  api_user: string | null
  checkin_after: string | null
  enabled: boolean
  /** Palette slug, or null when nobody picked one — the frontend then hashes a default. */
  avatar_color: string | null
  avatar_shape: string | null
  last_run_at: string | null
  last_success: boolean | null
  last_checked_in: boolean | null
  last_quota: number | null
  last_error: string | null
  created_at: string | null
  updated_at: string | null
}

export interface AccountInput {
  name: string
  base_url: string
  login_method?: LoginMethod
  mechanism?: Mechanism
  username?: string
  password?: string
  access_token?: string
  session?: string
  api_user?: string
  checkin_after?: string
  enabled?: boolean
  avatar_color?: string
  avatar_shape?: string
}

export interface Outcome {
  success: boolean
  checked_in: boolean | null
  before_quota: number | null
  after_quota: number | null
  delta: number | null
  /** What the site itself said it granted, when it reports it at all. */
  awarded: number | null
  /** What today's check-in was worth: `awarded` when the site named a figure,
   *  otherwise the balance movement. */
  gain: number | null
  error: string | null
  username: string | null
}

export interface SiteInfo {
  base_url: string
  login_methods: LoginMethod[]
  quota_per_unit: number
  turnstile: boolean
  turnstile_key: string | null
  checkin_path: string | null
  refresh_path: string | null
  /** Same path, GET: a status read that performs nothing. Where a fork has one it is
   *  the only unambiguous answer to "did today's bonus land". */
  status_path: string | null
  /** The generic check-in toggle. False on a fork that registered the route and turned
   *  it off, null where the site does not report it. */
  checkin_enabled: boolean | null
  mechanism: 'endpoint' | 'login_bonus'
}

/** A promo card, as `panel/promo.py` decided to show it. Text only, by design: the
 *  manifest is remote input, so nothing in here becomes markup, CSS or a class name. */
export interface Promo {
  id: string
  /** Which sticker the card wears. Derived from this install's display state, never declared in
   *  the manifest: whether a site is new is a fact about how long we have been offering it. */
  sticker: 'new' | 'unregistered'
  /** A palette *name*, matched against PromoCard's own table. The manifest never sends CSS. */
  theme: string | null
  hero: { title: string | null; subtitle: string | null; brand: string | null; badge: string | null }
  title: string
  body: string
  cta: { label: string; url: string }
}

const API_BASE = '/api'

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) {
    const text = await resp.text()
    let detail = text
    try {
      detail = JSON.parse(text).detail ?? text
    } catch {
      /* not JSON — show the raw body */
    }
    throw new Error(detail || `HTTP ${resp.status}`)
  }
  if (resp.status === 204) return null as T
  return resp.json()
}

const post = <T>(url: string, body?: unknown) =>
  request<T>(url, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  listAccounts: () => request<Account[]>('/accounts'),
  createAccount: (input: AccountInput) => post<Account>('/accounts', input),
  updateAccount: (id: number, fields: Partial<AccountInput>) =>
    request<Account>(`/accounts/${id}`, { method: 'PUT', body: JSON.stringify(fields) }),
  deleteAccount: (id: number) => request<void>(`/accounts/${id}`, { method: 'DELETE' }),

  checkIn: (id: number) => post<Outcome>(`/accounts/${id}/check-in`),
  checkInMany: (accountIds: number[]) => post<Record<string, Outcome>>('/check-in', { account_ids: accountIds }),
  probe: (baseUrl: string) => post<SiteInfo>('/probe', { base_url: baseUrl }),
  bootstrap: (id: number) => post<{ username: string }>(`/accounts/${id}/bootstrap`),
  browserLogin: (id: number, headless = false) =>
    post<{ session_stored: boolean; username: string | null; warning?: string }>(
      `/accounts/${id}/browser-login`,
      { headless, set_password: true },
    ),

  promo: () => request<{ card: Promo | null }>('/promos'),
  dismissPromo: (id: string) => post<void>(`/promos/${encodeURIComponent(id)}/dismiss`),
}
