import { useEffect, useState } from 'react'
import {
  Button,
  Input,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Select,
  SelectItem,
  Switch,
  Textarea,
} from '@heroui/react'
import {
  api,
  type Account,
  type AccountInput,
  type AccountSaved,
  type LoginMethod,
  type Mechanism,
  type SiteInfo,
} from './api'

interface AccountFormProps {
  account?: Account | null
  isOpen: boolean
  onClose: () => void
  /** The saved account plus what its pasted credential turned out to be worth. The verdict
   *  is reported by whoever owns the toasts, not here — the modal is already closing. */
  onSaved: (saved?: AccountSaved) => void
}

const METHOD_LABELS: Record<LoginMethod, string> = {
  password: '账号密码（纯 HTTP，最快）',
  access_token: '访问令牌 Access Token',
  session: '会话 Cookie（自己粘贴）',
  linuxdo: 'LinuxDO 授权登录（浏览器一次）',
  github: 'GitHub 授权登录（浏览器一次）',
}
const MECHANISM_LABELS: Record<Mechanism, string> = {
  auto: '自动检测（推荐）',
  visit: '登录后打开页面即签到',
}
const MECHANISM_HINTS: Record<Mechanism, string> = {
  auto: '有独立签到接口就 POST 它；没有的话靠重新登录发放。面板自己探测，不用你操心',
  visit: '站点在开放时间后自己跑签到脚本，只要页面在登录状态下加载过就算（anyrouter.top）。没有回执，面板只能报余额',
}

export const FIELD = {
  inputWrapper:
    'bg-default-100 shadow-none data-[hover=true]:bg-default-200 group-data-[focus=true]:bg-default-100',
} as const
const SELECT_FIELD = {
  trigger: 'bg-default-100 shadow-none data-[hover=true]:bg-default-200 data-[focus=true]:bg-default-100',
} as const

const MANUAL_METHODS: LoginMethod[] = ['password', 'access_token', 'session']
const OAUTH_METHODS: LoginMethod[] = ['linuxdo', 'github']

/** Methods this panel can actually drive, in ladder order.
 *
 * Before a probe answers we know nothing about the site, so hide nothing — offering
 * only the manual three made GitHub look unsupported. And never drop the method the
 * account already uses, or the Select renders blank.
 *
 * An **empty** `login_methods` is that same "we know nothing", not "password only": a WAF
 * that answers `/api/status` with an HTML challenge leaves the server nothing to read
 * (anyrouter.top, which does have LinuxDO login), and trusting the empty list hid the very
 * option that site needs. Only a list the site actually gave us may narrow this. */
function usable(site: SiteInfo | null, current: LoginMethod): LoginMethod[] {
  const said = site?.login_methods ?? []
  const oauth = said.length ? said.filter((m) => OAUTH_METHODS.includes(m)) : OAUTH_METHODS
  const all = [...MANUAL_METHODS, ...oauth]
  return all.includes(current) ? all : [...all, current]
}

/** Where a site's own cookie comes from, in one place because three branches say it.
 *
 * It is the longest instruction in this form and the one that was measured rather than
 * guessed: a fork's site session is `HttpOnly`, so `document.cookie` is empty and a
 * cookie extension standing on the wrong tab reports nothing at all. */
function SiteCookieHint() {
  return (
    <p className="text-xs text-default-500">
      在自己电脑上登录<b>这个站点</b>，这条 cookie 多半是 <b>HttpOnly</b> 的，页面 JS 读不到，
      用浏览器自带的 <b>DevTools → Application → Cookies → 该站点</b>，复制 Value 最稳。
      cookie 扩展也能读 HttpOnly，但得在这个站点上有权限、且停在<b>站点自己的页面</b>上
      —— 停在本面板上会显示「没有任何 cookie」，因为面板自己不发 cookie。
    </p>
  )
}

/** Can a pasted site credential do this site's check-in on its own?
 *
 * Only where the probe found a check-in route. `login_bonus` re-logs in to collect, which
 * needs a password (`newapi._check_in_login_bonus`), and `visit` collects inside a real
 * page (`service._browser_visit`, which never reads `account.session`) — offering the
 * paste there would be offering something that cannot work. */
function pasteIsEnough(site: SiteInfo | null, mechanism: Mechanism): boolean {
  if (mechanism === 'visit') return false
  return site ? site.mechanism === 'endpoint' : true
}

/** What the probe found that decides the login method, in the owner's words. */
function advice(site: SiteInfo): string[] {
  const notes = [site.mechanism === 'endpoint' ? `独立签到接口 ${site.checkin_path}` : '登录即签到（无签到按钮）']
  // The check-in route was still found — that comes from POSTing the candidates, not from
  // `/api/status` — so this is worth one line rather than an alarm.
  if (!site.login_methods.length)
    notes.push('站点没报登录方式（多半被 WAF 挡了），下面的登录方式全部列出，按站点实际有的选')
  // Worth saying out loud: it means the panel can ask the site "did today land" instead of
  // guessing from the balance, which is the difference between a receipt and an inference.
  if (site.status_path) notes.push('站点能查签到状态：结果由站点说，不靠余额推断')
  if (site.checkin_enabled === false && site.status_path)
    notes.push('注意：该站通用签到是关闭的，面板已选用它自己的签到接口')
  if (site.turnstile) notes.push('签到要 Turnstile：得用授权登录，面板在浏览器里造 token')
  if (site.refresh_path) notes.push('JWT 站点：会话栏要粘 new_api_refresh，且用一次就换')
  if (site.turnstile || site.refresh_path) notes.push('→ 建议选 GitHub / LinuxDO 授权登录')
  return notes
}

export default function AccountForm({ account, isOpen, onClose, onSaved }: AccountFormProps) {
  const [name, setName] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [method, setMethod] = useState<LoginMethod>('password')
  const [mechanism, setMechanism] = useState<Mechanism>('auto')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [accessToken, setAccessToken] = useState('')
  const [session, setSession] = useState('')
  const [apiUser, setApiUser] = useState('')
  const [checkinAfter, setCheckinAfter] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [site, setSite] = useState<SiteInfo | null>(null)
  const [probing, setProbing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!isOpen) return
    setName(account?.name ?? '')
    setBaseUrl(account?.base_url ?? '')
    setMethod(account?.login_method ?? 'password')
    setMechanism(account?.mechanism ?? 'auto')
    setUsername(account?.username ?? '')
    setPassword(account?.password ?? '')
    setAccessToken(account?.access_token ?? '')
    setSession(account?.session ?? '')
    setApiUser(account?.api_user ?? '')
    setCheckinAfter(account?.checkin_after ?? '')
    setEnabled(account?.enabled ?? true)
    setSite(null)
    setError(null)
    if (account?.base_url) probe(account.base_url) // editing: show what this site really supports
  }, [isOpen, account])

  /** Fill in the scheme a site address is normally typed without.
   *
   *  The server requires one and answers 422 without it, which used to be invisible here: the
   *  probe returned early on anything unschemed, so typing `anyrouter.top` auto-probed nothing
   *  and the 检测 button did nothing and said nothing. Completing it is right rather than
   *  merely convenient — `https` is what every one of these sites speaks, and a panel that
   *  demands the prefix is asking the owner to satisfy a validator.
   *
   *  Only the scheme is added. Anything else typed — a path, a port, a trailing slash — is the
   *  owner's, and guessing more is how an address turns into one the site does not answer on. */
  function withScheme(url: string): string {
    const text = url.trim()
    // The scheme is lowercased because the server's check is `startswith(('http://',
    // 'https://'))` — case-sensitive, so `HTTP://x` would 422 exactly as silently as the
    // bare domain did. Only the scheme; the host and path stay as typed.
    if (!text) return text
    const schemed = text.match(/^(https?:\/\/)(.*)$/i)
    if (schemed) return schemed[1].toLowerCase() + schemed[2]
    // A lone `//host` is what a copied protocol-relative URL looks like.
    return `https://${text.replace(/^\/+/, '')}`
  }

  async function probe(url = baseUrl) {
    const target = withScheme(url)
    if (!target || !/^https?:\/\/.+/.test(target)) return
    // Show what will actually be probed and saved, rather than probing one address while the
    // field claims another.
    if (target !== baseUrl) setBaseUrl(target)
    setProbing(true)
    setError(null)
    try {
      setSite(await api.probe(target))
    } catch (e) {
      setSite(null)
      setError(`检测失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setProbing(false)
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      const input: AccountInput = {
        name,
        // Saving normalises too: blurring the field is what usually adds the scheme, and
        // pressing Enter straight from the address skips the blur.
        base_url: withScheme(baseUrl),
        login_method: method,
        mechanism,
        username: username || undefined,
        password: password || undefined,
        access_token: accessToken || undefined,
        session: session || undefined,
        api_user: apiUser || undefined,
        checkin_after: checkinAfter || undefined,
        enabled,
      }
      const saved = account ? await api.updateAccount(account.id, input) : await api.createAccount(input)
      onSaved(saved)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal isOpen={isOpen} onClose={onClose} size="md" scrollBehavior="inside">
      <ModalContent className="max-h-[88vh]">
        {(onCloseModal) => (
          <form onSubmit={handleSubmit} className="flex min-h-0 flex-col">
            <ModalHeader className="pb-2">{account ? '编辑账号' : '添加账号'}</ModalHeader>
            <ModalBody className="gap-5 overflow-y-auto py-2">
              <Input
                label="名称"
                labelPlacement="outside"
                isRequired
                value={name}
                onValueChange={setName}
                placeholder="账号显示名称"
                classNames={FIELD}
              />
              <Input
                label="网站地址 (Base URL)"
                labelPlacement="outside"
                isRequired
                value={baseUrl}
                onValueChange={setBaseUrl}
                onBlur={() => probe()}
                placeholder="https://example.com"
                classNames={FIELD}
                description="任何 New API / One API 站点，签到方式自动检测"
                endContent={
                  <Button size="sm" variant="light" isLoading={probing} onPress={() => probe()}>
                    检测
                  </Button>
                }
              />
              {site && (
                <div className="text-xs text-default-500 space-y-0.5">
                  <p>
                    {advice(site)[0]}
                    {/* 支持 means the site said so. With nothing read, the list below is what
                        the panel is offering, not what the site confirmed — so say that. */}
                    {site.login_methods.length ? ' · 支持：' : ' · 可选：'}
                    {usable(site, method).map((m) => m.toUpperCase()).join(' / ')}
                  </p>
                  {advice(site)
                    .slice(1)
                    .map((note) => (
                      <p key={note}>{note}</p>
                    ))}
                </div>
              )}
              <Select
                label="签到方式"
                labelPlacement="outside"
                selectedKeys={[mechanism]}
                onChange={(e) => e.target.value && setMechanism(e.target.value as Mechanism)}
                description={MECHANISM_HINTS[mechanism]}
                classNames={SELECT_FIELD}
              >
                {(Object.keys(MECHANISM_LABELS) as Mechanism[]).map((m) => (
                  <SelectItem key={m}>{MECHANISM_LABELS[m]}</SelectItem>
                ))}
              </Select>
              <Select
                label="登录方式"
                labelPlacement="outside"
                selectedKeys={[method]}
                onChange={(e) => e.target.value && setMethod(e.target.value as LoginMethod)}
                description="密码最快；OAuth 账号点一次「浏览器登录」，之后每天自动；服务器上没桌面就选「会话 Cookie」粘一段"
                classNames={SELECT_FIELD}
              >
                {usable(site, method).map((m) => (
                  <SelectItem key={m}>{METHOD_LABELS[m]}</SelectItem>
                ))}
              </Select>
              {method === 'password' && (
                <>
                  <Input
                    label="用户名"
                    labelPlacement="outside"
                    value={username}
                    onValueChange={setUsername}
                    placeholder="站点用户名"
                    classNames={FIELD}
                  />
                  <Input
                    label="密码"
                    labelPlacement="outside"
                    type="password"
                    value={password}
                    onValueChange={setPassword}
                    placeholder="站点密码"
                    classNames={FIELD}
                  />
                  {site?.turnstile && (
                    <p className="text-xs text-warning">该站点登录要过 Turnstile，账号密码这条路会失败，改用授权登录。</p>
                  )}
                </>
              )}
              {method === 'access_token' && (
                <Input
                  label="Access Token"
                  labelPlacement="outside"
                  value={accessToken}
                  onValueChange={setAccessToken}
                  placeholder="站点后台生成的系统访问令牌"
                  description="站点用户页里「访问令牌 / Access Token」点生成，明文复制即可 —— 不用浏览器扩展，也不像 cookie 一个月就过期"
                  classNames={{ ...FIELD, input: 'font-mono text-xs' }}
                />
              )}
              {method === 'session' && (
                <>
                  <Textarea
                    label="Session Cookie"
                    labelPlacement="outside"
                    value={session}
                    onValueChange={setSession}
                    minRows={3}
                    maxRows={8}
                    placeholder='直接粘 session 值，或 cookie 扩展导出的整段 JSON：[{"name": "session", ...}]'
                    description="整段粘进来就行，面板自己挑出有用的那条。JWT 站点没有 session cookie，用 new_api_refresh（每次用完会轮换）"
                    classNames={{ ...FIELD, input: 'font-mono text-xs' }}
                  />
                  <p className="text-xs text-default-500">
                    先看这个站点有没有「访问令牌」—— 有的话用上面那个登录方式，明文复制，不会过期，省事得多。
                  </p>
                  <SiteCookieHint />
                  <p className="text-xs text-default-500">保存时会立刻拿这个凭据登录一次，验证结果直接告诉你。</p>
                  {(mechanism === 'visit' || site?.mechanism === 'login_bonus') && (
                    // The one thing a pasted site cookie cannot do. Worth saying here rather
                    // than letting it surface as a puzzling failure at 08:30 tomorrow.
                    <p className="text-xs text-warning">
                      {mechanism === 'visit'
                        ? '这个站点要在登录状态下真的加载页面才算签到，光有 session 不够 —— 得用授权登录，让面板能开浏览器。'
                        : '这个站点靠「重新登录」发放额度，而重新登录要账号密码或授权登录 —— 只有 session 的话，面板拿不到额度。'}
                    </p>
                  )}
                </>
              )}
              {(method === 'linuxdo' || method === 'github') && (
                <>
                  <p className="text-xs text-default-500">
                    保存后在列表里点「浏览器登录」授权一次。能设密码就转成纯 HTTP，不能就继续走浏览器，每天自动。
                    <br />
                    服务器上没有桌面？用列表里的「注入会话」：在自己电脑的浏览器里登录
                    {method === 'github' ? ' GitHub' : ' LinuxDO'}，停在
                    {method === 'github' ? ' github.com ' : ' linux.do '}
                    页面上用 cookie 扩展导出，整段粘进去，之后每天自动。
                  </p>
                  {/* The site's own credential, offered *beside* the IdP path rather than instead
                      of it. Keeping the method on OAuth is what preserves the fallback: the run
                      spends whatever is pasted here first (`newapi.check_in` dispatches on what
                      the account holds, not on `login_method`) and only reaches for a browser
                      when that fails (`service._attempt`). Choosing 「会话 Cookie」 to paste one
                      used to throw the fallback away. */}
                  {pasteIsEnough(site, mechanism) ? (
                    <>
                      <p className="text-xs text-default-500">
                        也可以<b>顺便</b>把这个站点自己的凭据粘在下面 —— 这个站签到只是 POST 一个路由，
                        所以有它就够，服务器上不用开浏览器。登录方式<b>不用改</b>：面板优先用粘进来的凭据，
                        它失效了才退回上面那条授权登录，两条路互为退路。两格都留空也没关系。
                      </p>
                      <Input
                        label="访问令牌（可选）"
                        labelPlacement="outside"
                        value={accessToken}
                        onValueChange={setAccessToken}
                        placeholder="站点后台生成的系统访问令牌"
                        description="首选这个：站点用户页里「访问令牌」点生成，明文复制，不会过期，也不跟你自己的浏览器抢登录态"
                        classNames={{ ...FIELD, input: 'font-mono text-xs' }}
                      />
                      <Textarea
                        label={site?.refresh_path ? '会话 Cookie（可选，本站要 new_api_refresh）' : '会话 Cookie（可选）'}
                        labelPlacement="outside"
                        value={session}
                        onValueChange={setSession}
                        minRows={2}
                        maxRows={6}
                        placeholder={
                          site?.refresh_path
                            ? '粘 new_api_refresh 的值，或 cookie 扩展导出的整段 JSON'
                            : '粘 session 的值，或 cookie 扩展导出的整段 JSON'
                        }
                        description={
                          site?.refresh_path
                            ? '这个站没有 session cookie，要的是 new_api_refresh。它每兑换一次就轮换，所以面板和你自己的浏览器不能共用它 —— 面板用了，浏览器这边下次刷新就要重新登录。想两边都用就选上面的访问令牌'
                            : '整段粘进来就行，面板自己挑出有用的那条'
                        }
                        classNames={{ ...FIELD, input: 'font-mono text-xs' }}
                      />
                      <SiteCookieHint />
                    </>
                  ) : (
                    <p className="text-xs text-warning">
                      {mechanism === 'visit'
                        ? '这个站点要在登录状态下真的加载页面才算签到，粘任何站点凭据都不够，只有「注入会话」这条路。'
                        : '这个站点靠「重新登录」发放额度，粘站点凭据换不来额度，只有「注入会话」这条路。'}
                    </p>
                  )}
                </>
              )}
              <Input
                label="API User（可选）"
                labelPlacement="outside"
                value={apiUser}
                onValueChange={setApiUser}
                placeholder="new-api-user 请求头的值"
                description="一般不用填：保存时验证凭据那一下会从站点响应里取回来自动填上。只有验证提示「站点还要账号的用户 id」时才手填（站点页面 localStorage 的 user.id）"
                classNames={FIELD}
              />
              <Input
                label={mechanism === 'visit' ? '每日开放时间' : '每日开放时间（可选）'}
                labelPlacement="outside"
                isRequired={mechanism === 'visit'}
                value={checkinAfter}
                onValueChange={setCheckinAfter}
                placeholder="08:30"
                classNames={FIELD}
                description={
                  mechanism === 'visit'
                    ? '这类站点必须填：早于这个点加载页面拿不到额度，面板也不会白试'
                    : '这个站点几点开始能签到；留空按 0 点算'
                }
              />
              <div className="flex items-center justify-between rounded-medium bg-default-100 px-3 py-2">
                <span className="text-small">启用自动签到</span>
                <Switch isSelected={enabled} onValueChange={setEnabled} size="sm" aria-label="启用自动签到" />
              </div>
              {error && <p className="text-danger text-sm">{error}</p>}
            </ModalBody>
            <ModalFooter className="border-t border-default-200">
              <Button variant="flat" onPress={onCloseModal}>
                取消
              </Button>
              <Button type="submit" color="primary" isLoading={saving}>
                保存
              </Button>
            </ModalFooter>
          </form>
        )}
      </ModalContent>
    </Modal>
  )
}
