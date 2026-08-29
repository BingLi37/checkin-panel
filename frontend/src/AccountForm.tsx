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
} from '@heroui/react'
import { api, type Account, type AccountInput, type LoginMethod, type Mechanism, type SiteInfo } from './api'

interface AccountFormProps {
  account?: Account | null
  isOpen: boolean
  onClose: () => void
  onSaved: () => void
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
 * account already uses, or the Select renders blank. */
function usable(site: SiteInfo | null, current: LoginMethod): LoginMethod[] {
  const oauth = site ? site.login_methods.filter((m) => OAUTH_METHODS.includes(m)) : OAUTH_METHODS
  const all = [...MANUAL_METHODS, ...oauth]
  return all.includes(current) ? all : [...all, current]
}

/** What the probe found that decides the login method, in the owner's words. */
function advice(site: SiteInfo): string[] {
  const notes = [site.mechanism === 'endpoint' ? `独立签到接口 ${site.checkin_path}` : '登录即签到（无签到按钮）']
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

  async function probe(url = baseUrl) {
    if (!/^https?:\/\/.+/.test(url)) return
    setProbing(true)
    setError(null)
    try {
      setSite(await api.probe(url))
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
        base_url: baseUrl,
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
      if (account) await api.updateAccount(account.id, input)
      else await api.createAccount(input)
      onSaved()
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
                    {' · 支持：'}
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
                description="密码最快；OAuth 账号点一次「浏览器登录」，之后每天自动"
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
                  classNames={{ ...FIELD, input: 'font-mono text-xs' }}
                />
              )}
              {method === 'session' && (
                <Input
                  label="Session Cookie"
                  labelPlacement="outside"
                  value={session}
                  onValueChange={setSession}
                  placeholder="直接粘 session 值 / session=xxx / 整段 JSON"
                  description="JWT 站点没有 session cookie，粘 new_api_refresh 的值（每次用完会轮换）"
                  classNames={{ ...FIELD, input: 'font-mono text-xs' }}
                />
              )}
              {(method === 'linuxdo' || method === 'github') && (
                <p className="text-xs text-default-500">
                  保存后在列表里点「浏览器登录」授权一次。能设密码就转成纯 HTTP，不能就继续走浏览器，每天自动。
                </p>
              )}
              <Input
                label="API User（可选）"
                labelPlacement="outside"
                value={apiUser}
                onValueChange={setApiUser}
                placeholder="new-api-user 请求头的值"
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
