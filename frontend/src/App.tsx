import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Button,
  Card,
  CardBody,
  Checkbox,
  Chip,
  Input,
  Spinner,
  Table,
  TableBody,
  TableCell,
  TableColumn,
  TableHeader,
  TableRow,
  Tooltip,
  useDisclosure,
} from '@heroui/react'
import { api, type Account, type Outcome } from './api'
import AccountForm, { FIELD } from './AccountForm'
import IdpCookiesModal from './IdpCookiesModal'
import DeleteAccountModal from './DeleteAccountModal'
import ProfileCleanupModal from './ProfileCleanupModal'
import AccountAvatar from './AccountAvatar'
import PromoCard from './PromoCard'
import { defaultSkins, type AvatarColor, type AvatarShape } from './avatar'
import { loginMethodMeta, SearchIcon } from './icons'
import { useStuck } from './useStuck'
import type { AccountSaved, IdpInjection } from './api'

/** One container to append into: every toast used to be pinned at the same top-6 right-6,
 *  so two in a row landed on top of each other. */
function toastStack(): HTMLElement {
  const existing = document.getElementById('toast-stack')
  if (existing) return existing
  const stack = document.createElement('div')
  stack.id = 'toast-stack'
  stack.className =
    'fixed top-3 right-3 left-3 sm:left-auto sm:top-6 sm:right-6 z-50 flex flex-col items-end gap-2'
  document.body.appendChild(stack)
  return stack
}

function toast(text: string, ok: boolean) {
  const el = document.createElement('div')
  el.textContent = text
  el.className = `max-w-full sm:max-w-sm px-4 py-2 rounded-lg text-white shadow-lg text-sm ${ok ? 'bg-success' : 'bg-danger'}`
  toastStack().appendChild(el)
  setTimeout(() => el.remove(), ok ? 4000 : 9000)
}

function describe(outcome: Outcome): string {
  if (!outcome.success) return outcome.error ?? '未知错误'
  const balance = outcome.after_quota != null ? `，余额 $${outcome.after_quota}` : ''
  // `gain` prefers the site's own figure: a fork whose reward is per-weekday config
  // (sotamodel.net) is the only thing that can price it, and a balance that moved by a
  // different amount because usage landed mid-run would misreport it.
  const gained = outcome.gain ? `（+$${outcome.gain}）` : ''
  if (outcome.checked_in == null) return `已重新登录${balance}` // balance unknown before, so the bonus cannot be confirmed
  return outcome.checked_in ? `签到成功${gained}${balance}` : `今日已签到${balance}`
}

type ChipState = {
  text: string
  color: 'default' | 'danger' | 'success' | 'primary' | 'warning'
  /** Why the state is what it is, when the state alone would overstate it. */
  hint: string | null
}

function statusChip(acc: Account): ChipState {
  // A disabled account still deserves its last result: showing only 已停用 made a
  // successful manual 签到 look like nothing had happened.
  if (acc.last_success == null) return { text: acc.enabled ? '未运行' : '已停用', color: 'default', hint: null }
  const state: ChipState = !acc.last_success
    ? { text: '失败', color: 'danger', hint: null }
    : acc.last_checked_in === true
      ? { text: '签到成功', color: 'success', hint: null }
      : acc.last_checked_in === false
        ? { text: '今日已签', color: 'primary', hint: null }
        : // null is nobody's word: rendering it as 今日已签 showed no evidence as evidence.
          {
            text: '已登录·未确认',
            color: 'warning',
            hint: '本次没能确认今天的签到是否落地：站点没给回执，余额也没动过。',
          }
  return acc.enabled ? state : { ...state, text: `${state.text}·已停用`, color: 'default' }
}

/** OAuth-only account with no password yet — it needs the one-time browser hop. */
const needsBrowser = (acc: Account) =>
  (acc.login_method === 'linuxdo' || acc.login_method === 'github') && !(acc.username && acc.password)

type RowAction = { key: string; label: string; color: 'default' | 'secondary' | 'danger'; run: () => void }

/** The actions that only some rows have. Both layouts reserve a fixed-width slot for each,
 *  so 签到 and everything after it line up down the list whether or not a row has them. The card
 *  widths are smaller because its buttons are compact enough to keep all five on one line.
 *
 *  Adding an action means adding its slot here, or the two layouts drift apart. */
const ACTION_SLOTS = [
  { key: 'browser', table: 'w-[92px]', card: 'w-[76px]' },
  { key: 'inject', table: 'w-[104px]', card: 'w-[84px]' },
  { key: 'bootstrap', table: 'w-[76px]', card: 'w-[64px]' },
] as const

const host = (url: string) => url.replace(/^https?:\/\//, '')

const runAt = (iso: string | null) =>
  iso
    ? new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    : '—'

const money = (quota: number | null) => (quota != null ? `$${quota.toFixed(2)}` : '—')

export default function App() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [batching, setBatching] = useState(false)
  const [editing, setEditing] = useState<Account | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [search, setSearch] = useState('')
  const [injecting, setInjecting] = useState<Account | null>(null)
  const [deleting, setDeleting] = useState<Account | null>(null)
  const { isOpen, onOpen, onClose } = useDisclosure()
  const { isOpen: injectOpen, onOpen: onInjectOpen, onClose: onInjectClose } = useDisclosure()
  const { isOpen: deleteOpen, onOpen: onDeleteOpen, onClose: onDeleteClose } = useDisclosure()
  const { isOpen: cleanupOpen, onOpen: onCleanupOpen, onClose: onCleanupClose } = useDisclosure()
  const { sentinel, stuck } = useStuck()

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setAccounts(await api.listAccounts())
      // Deliberately does not clear `error`: this runs inside run()'s finally, and
      // clearing here wiped every failure before it could render.
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  async function run<T>(id: number | null, action: () => Promise<T>, onDone: (result: T) => void) {
    setBusyId(id)
    setError(null)
    try {
      onDone(await action())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
      await refresh()
    }
  }

  /** Reload the list, and report what the pasted credential turned out to be worth.
   *
   *  The account is saved either way — a site behind a WAF can refuse a perfectly good
   *  cookie (ADR-0010), so a failed check is a warning, never a lost account. Silence here
   *  would mean discovering it at 08:30 tomorrow instead. */
  async function afterSaved(saved?: AccountSaved) {
    await refresh()
    const check = saved?.credential
    if (!check) return
    if (check.ok) {
      const who = check.username ? `${check.username}` : '已登录'
      const balance = check.quota != null ? `，余额 $${check.quota}` : ''
      toast(`${saved.account.name}: 凭据可用（${who}${balance}）`, true)
    } else {
      toast(`${saved.account.name}: 账号已保存，但凭据没通过 —— ${check.warning ?? '站点没有说明原因'}`, false)
    }
  }

  const checkIn = (acc: Account) => {
    if (needsBrowser(acc)) toast(`${acc.name}: 正在签到，要走一次浏览器登录，可能要 1~2 分钟`, true)
    return run(acc.id, () => api.checkIn(acc.id), (outcome) => toast(`${acc.name}: ${describe(outcome)}`, outcome.success))
  }

  const browserLogin = (acc: Account) => {
    toast(`${acc.name}: 正在打开浏览器窗口，请在窗口里登录并授权（最多等 5 分钟）`, true)
    return run(acc.id, () => api.browserLogin(acc.id), (result) =>
      toast(
        result.warning
          ? `${acc.name}: 会话已保存，之后仍用浏览器登录签到（${result.warning}）`
          : `${acc.name}: 已登录${result.username ? `，已设置密码（${result.username}）` : ''}`,
        true,
      ),
    )
  }

  const bootstrap = (acc: Account) =>
    run(acc.id, () => api.bootstrap(acc.id), (result) => toast(`${acc.name}: 已设置密码（${result.username}）`, true))

  /** Deliberately not run(): that sets busyId, which would put the row's 签到 button into a
   *  loading spinner just because someone picked a colour. */
  async function saveAvatar(acc: Account, patch: { avatar_color: AvatarColor; avatar_shape: AvatarShape }) {
    setAccounts((prev) => prev.map((a) => (a.id === acc.id ? { ...a, ...patch } : a)))
    try {
      await api.updateAccount(acc.id, patch)
    } catch (e) {
      setAccounts((prev) =>
        prev.map((a) =>
          a.id === acc.id ? { ...a, avatar_color: acc.avatar_color, avatar_shape: acc.avatar_shape } : a,
        ),
      )
      toast(`${acc.name}: 头像没保存成功 —— ${e instanceof Error ? e.message : String(e)}`, false)
    }
  }

  // A modal rather than `window.confirm`, because the profile is a choice and confirm cannot
  // hold one. What that choice is about: the profile holds the IdP session, so the old
  // "profile 保留" left a live github.com login on disk under a name nothing showed.
  function askToDelete(acc: Account) {
    setDeleting(acc)
    onDeleteOpen()
  }

  function remove(acc: Account, forgetProfile: boolean) {
    return run(
      acc.id,
      () => api.deleteAccount(acc.id, forgetProfile),
      (result) =>
        toast(`${acc.name}: 已删除${result.profile_removed ? '，浏览器 profile 一起删了' : ''}`, true),
    )
  }

  async function checkInMany(ids: number[]) {
    if (ids.length === 0) return
    setBatching(true)
    setError(null)
    try {
      const results = await api.checkInMany(ids)
      const failed = Object.entries(results).filter(([, o]) => !o.success)
      toast(`签到完成：${ids.length - failed.length}/${ids.length} 成功`, failed.length === 0)
      if (failed.length) {
        setError(
          failed
            .map(([id, o]) => `${accounts.find((a) => a.id === Number(id))?.name ?? id}: ${o.error}`)
            .join(' · '),
        )
      }
      setSelected(new Set())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBatching(false)
      await refresh()
    }
  }

  function openForm(acc: Account | null) {
    setEditing(acc)
    onOpen()
  }

  function openInject(acc: Account) {
    setInjecting(acc)
    onInjectOpen()
  }

  /** Report what an injection proved, in the three shapes it can come back as.
   *
   *  `verified` is three-valued and the difference matters: true is the only evidence that
   *  tomorrow's unattended run will work, false means these cookies did not get through,
   *  and null means nobody asked — so it must not be reported as success. */
  async function afterInjected(acc: Account, result: IdpInjection) {
    await refresh()
    if (result.warning) toast(`${acc.name}: ${result.warning}`, false)
    if (result.verified === true) {
      toast(`${acc.name}: 已注入 ${result.injected} 条 cookie，无头登录验证通过，之后每天自动`, true)
    } else if (result.verified === false) {
      toast(`${acc.name}: 已注入 ${result.injected} 条，但登录没通过 —— ${result.reason ?? '站点没有说明原因'}`, false)
    } else {
      toast(`${acc.name}: 已注入 ${result.injected} 条 cookie，未验证，成不成要等下次签到`, true)
    }
  }

  /** The card layout has no table row to press, so its checkbox drives the same Set directly. */
  function toggleOne(id: number, on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (on) next.add(String(id))
      else next.delete(String(id))
      return next
    })
  }

  const query = search.trim().toLowerCase()
  const visible = query
    ? accounts.filter((a) => a.name.toLowerCase().includes(query) || a.base_url.toLowerCase().includes(query))
    : accounts
  const enabledVisibleIds = visible.filter((a) => a.enabled).map((a) => a.id)
  const selectedIds = Array.from(selected, Number)
  const batchTargets = selectedIds.length ? selectedIds : enabledVisibleIds
  // Over the full list, never `visible`: colours would jump around while typing a query.
  const skins = useMemo(() => defaultSkins(accounts), [accounts])

  // Derived once, rendered twice: the table on a wide screen, cards on a phone. Keeping the
  // derivation here is what stops the two layouts from drifting apart.
  const rendered = visible.map((acc) => {
    const chip = statusChip(acc)
    return {
      acc,
      chip,
      method: loginMethodMeta(acc.login_method),
      /** The site's own error, else why a status is less certain than it looks. */
      note: acc.last_error ?? chip.hint,
      busy: busyId === acc.id || (batching && (selected.size ? selected.has(String(acc.id)) : acc.enabled)),
      actions: [
        ...(needsBrowser(acc)
          ? [{ key: 'browser', label: '浏览器登录', color: 'secondary' as const, run: () => void browserLogin(acc) }]
          : []),
        // Offered on the same condition as 浏览器登录, and beside it: that one needs a desktop
        // to open a window on, this one is how the same account gets set up on a server.
        ...(needsBrowser(acc)
          ? [{ key: 'inject', label: '注入会话', color: 'secondary' as const, run: () => openInject(acc) }]
          : []),
        ...(acc.session && !(acc.username && acc.password)
          ? [{ key: 'bootstrap', label: '设置密码', color: 'default' as const, run: () => void bootstrap(acc) }]
          : []),
        { key: 'edit', label: '编辑', color: 'default', run: () => openForm(acc) },
        { key: 'remove', label: '删除', color: 'danger', run: () => askToDelete(acc) },
      ] as RowAction[],
      avatar: (
        <AccountAvatar
          name={acc.name}
          color={acc.avatar_color}
          shape={acc.avatar_shape}
          fallbackColor={skins.get(acc.id) ?? 'slate'}
          onPick={(color, shape) => saveAvatar(acc, { avatar_color: color, avatar_shape: shape })}
        />
      ),
    }
  })


  /** 签到 plus whichever of the optional actions this row has, in fixed slots so every row's
   *  buttons sit at the same offsets. The two variants differ only in how much room they have. */
  const actionRow = (row: (typeof rendered)[number], variant: 'table' | 'card') => {
    const compact = variant === 'card' ? 'min-w-0 shrink-0 px-2 text-tiny' : ''
    const rest = row.actions.filter((a) => !ACTION_SLOTS.some((s) => s.key === a.key))
    return (
      <div className={`flex flex-nowrap items-center gap-1 ${variant === 'table' ? 'justify-end' : ''}`}>
        <Button
          size="sm"
          variant="light"
          color="primary"
          className={compact}
          isLoading={row.busy}
          onPress={() => checkIn(row.acc)}
        >
          签到
        </Button>
        {ACTION_SLOTS.map((slot) => {
          const slotted = row.actions.find((a) => a.key === slot.key)
          return (
            <span key={slot.key} className={`inline-flex shrink-0 justify-center ${slot[variant]}`}>
              {slotted && (
                <Button
                  size="sm"
                  variant="light"
                  color={slotted.color}
                  className={compact}
                  isLoading={row.busy}
                  onPress={slotted.run}
                >
                  {slotted.label}
                </Button>
              )}
            </span>
          )
        })}
        {rest.map((a) => (
          <Button key={a.key} size="sm" variant="light" color={a.color} className={compact} onPress={a.run}>
            {a.label}
          </Button>
        ))}
      </div>
    )
  }
  return (
    <div className="min-h-screen bg-background p-3 sm:p-6">
      <div className="max-w-[1400px] mx-auto space-y-4 sm:space-y-6">
        {/* Sticky sentinel. The negative margin cancels the container's space-y so the header stays put. */}
        <div ref={sentinel} className="h-0 -mb-4 sm:-mb-6" />
        <header
          className={`sticky top-2 sm:top-4 z-30 flex flex-col border transition-all duration-300 ease-out sm:flex-row sm:items-center sm:justify-between sm:gap-4 ${
            stuck
              ? // gap-0 below sm: the title block has folded to nothing there, and a gap to it
                // would leave a dead band above the controls that the padding does not explain.
                'gap-0 sm:gap-4 rounded-large bg-background/70 backdrop-blur-xl shadow-medium border-default-200/50 p-2'
              : 'gap-3 rounded-none bg-transparent shadow-none border-transparent p-0'
          }`}
        >
          <div
            className={`grid min-w-0 transition-all duration-300 ease-out sm:grid-rows-[1fr] sm:opacity-100 ${
              stuck ? 'grid-rows-[0fr] opacity-0' : 'grid-rows-[1fr] opacity-100'
            }`}
          >
            {/* On a phone the whole title block folds away when stuck — the bar is 98px otherwise,
                and the controls are the only part worth that much of a 844px screen. */}
            <div className="overflow-hidden sm:overflow-visible">
              <h1 className="text-xl sm:text-2xl font-bold font-serif">自动签到管理面板</h1>
              <div
                className={`grid transition-all duration-300 ease-out ${stuck ? 'grid-rows-[0fr] opacity-0' : 'grid-rows-[1fr] opacity-100'}`}
              >
                <p className="overflow-hidden text-default-500 text-xs sm:text-sm">
                  面板开着就会自动补签：每个账号每天成功一次，失败按 30 分钟 → 1 → 2 → 4 小时退避重试
                  {/* Here rather than in the toolbar: cleaning up profiles is maintenance, and
                      the toolbar's three controls are what someone came to the page to press.
                      A link keeps it reachable without competing with them on a phone. */}
                  <button
                    type="button"
                    onClick={onCleanupOpen}
                    className="ml-2 underline decoration-dotted underline-offset-2 hover:text-default-700"
                  >
                    清理 profile
                  </button>
                </p>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Input
              isClearable
              value={search}
              onValueChange={setSearch}
              onClear={() => setSearch('')}
              placeholder="搜索名称或网站"
              aria-label="搜索账号"
              startContent={<SearchIcon className="shrink-0 text-default-400" />}
              className="min-w-0 flex-1 sm:w-56 sm:flex-none"
              classNames={FIELD}
            />
            <Button color="primary" variant="flat" className="shrink-0" onPress={() => openForm(null)}>
              <span className="sm:hidden">+ 添加</span>
              <span className="hidden sm:inline">+ 添加账号</span>
            </Button>
            <Button
              color="primary"
              className="shrink-0"
              isLoading={batching}
              isDisabled={batchTargets.length === 0}
              onPress={() => checkInMany(batchTargets)}
            >
              {selected.size ? `签到选中 (${selected.size})` : query ? `签到筛选结果 (${enabledVisibleIds.length})` : '全部签到'}
            </Button>
          </div>
        </header>

        {error && (
          <Card className="border border-danger-200 bg-danger-50">
            <CardBody className="text-danger-700 text-sm py-3">{error}</CardBody>
          </Card>
        )}

        {loading ? (
          <div className="flex justify-center py-8">
            <Spinner label="加载中..." />
          </div>
        ) : accounts.length === 0 ? (
          <Card className="border border-default-200">
            <CardBody className="py-10 text-center text-default-500 text-sm">
              还没有账号 —— 点右上角「+ 添加账号」，填一个网站地址，签到方式会自动检测。
            </CardBody>
          </Card>
        ) : visible.length === 0 ? (
          <Card className="border border-default-200">
            <CardBody className="py-10 text-center text-default-500 text-sm">
              没有匹配「{search.trim()}」的账号 —— 换个关键词，或清空搜索框看全部 {accounts.length} 个。
            </CardBody>
          </Card>
        ) : (
          <>
            {/* Phones get cards, not a table: eight columns need ~1100px, and squeezing them into
                390 turns reaching 删除 into a horizontal-scroll hunt. Same data, same actions. */}
            <div className="space-y-2 lg:hidden">
              {rendered.map((row) => {
                const { acc, chip, method, note, avatar } = row
                return (
                  <Card key={acc.id} className="border border-default-200" shadow="none">
                    <CardBody className="gap-2.5 p-3">
                      <div className="flex items-start gap-2">
                        <Checkbox
                          size="sm"
                          className="mt-1"
                          aria-label={`选择「${acc.name}」`}
                          isSelected={selected.has(String(acc.id))}
                          onValueChange={(on) => toggleOne(acc.id, on)}
                        />
                        {avatar}
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-medium">{acc.name}</p>
                          <p className="truncate font-mono text-tiny text-default-400">{host(acc.base_url)}</p>
                        </div>
                        <Chip color={chip.color} variant="flat" size="sm" className="shrink-0">
                          {chip.text}
                        </Chip>
                      </div>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-tiny text-default-500">
                        <span className="inline-flex items-center gap-1">
                          <method.Icon className="shrink-0 text-default-400" />
                          {method.label}
                          {acc.username && acc.password && <span className="text-success">✓</span>}
                        </span>
                        <span className="font-mono tabular-nums text-default-600">{money(acc.last_quota)}</span>
                        <span className="font-mono tabular-nums text-default-400">{runAt(acc.last_run_at)}</span>
                        <span className="text-default-400">
                          {acc.mechanism === 'visit' ? '打开页面即签到' : '自动检测'}
                          {acc.checkin_after ? ` · ${acc.checkin_after} 起` : ''}
                        </span>
                      </div>
                      {/* Spelled out rather than a tooltip: there is no hover on a touch screen. */}
                      {note && <p className="text-tiny text-default-500">{note}</p>}
                      {actionRow(row, 'card')}
                    </CardBody>
                  </Card>
                )
              })}
            </div>

            <Table
              aria-label="账号列表"
              removeWrapper={false}
              className="hidden lg:block"
              classNames={{ th: 'text-tiny uppercase tracking-wide', td: 'py-3' }}
              selectionMode="multiple"
              selectedKeys={selected}
              onSelectionChange={(keys) =>
                // 'all' means the visible rows: selecting filtered-out accounts is not what
                // the header checkbox looks like it does.
                setSelected(keys === 'all' ? new Set(visible.map((a) => String(a.id))) : new Set(Array.from(keys, String)))
              }
            >
              <TableHeader>
                <TableColumn>名称</TableColumn>
                <TableColumn className="hidden 2xl:table-cell">网站</TableColumn>
                {/* Tablet width cannot hold all seven; these two are the ones the name and site
                    cells already hint at. */}
                <TableColumn className="hidden xl:table-cell">登录方式</TableColumn>
                <TableColumn>状态</TableColumn>
                <TableColumn>余额</TableColumn>
                <TableColumn className="hidden xl:table-cell">最近运行</TableColumn>
                <TableColumn align="end">操作</TableColumn>
              </TableHeader>
              <TableBody>
                {rendered.map((row) => {
                  const { acc, chip, method, note, avatar } = row
                  return (
                    <TableRow key={acc.id}>
                      <TableCell className="font-medium">
                        {/* min-w-0: a flex child defaults to min-width:auto, and truncate does nothing without it. */}
                        <div className="flex items-center gap-2 min-w-0">
                          {avatar}
                          <div className="flex min-w-0 flex-col leading-tight">
                            {/* Tooltip wraps the text only: over the whole row it would fight the avatar's popover. */}
                            <Tooltip content={acc.name} showArrow>
                              <span className="truncate max-w-[168px]">{acc.name}</span>
                            </Tooltip>
                            {/* Stands in for the 网站 column until there is room for it, so no
                                width short of 2xl loses information. */}
                            <span className="2xl:hidden truncate max-w-[220px] text-tiny text-default-400">
                              <span className="font-mono">{host(acc.base_url)}</span>
                              {' · '}
                              {acc.mechanism === 'visit' ? '打开页面即签到' : '自动检测'}
                              {acc.checkin_after ? ` · ${acc.checkin_after} 起` : ''}
                            </span>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="hidden 2xl:table-cell">
                        <div className="flex flex-col leading-tight">
                          <span className="text-default-600 font-mono truncate max-w-[176px]">{host(acc.base_url)}</span>
                          <span className="text-tiny text-default-400">
                            {acc.mechanism === 'visit' ? '打开页面即签到' : '自动检测'}
                            {acc.checkin_after ? ` · ${acc.checkin_after} 起` : ''}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell className="hidden xl:table-cell text-default-500 whitespace-nowrap">
                        <span className="inline-flex items-center gap-1.5">
                          <method.Icon className="shrink-0 text-default-400" />
                          {method.label}
                          {acc.username && acc.password && (
                            <Tooltip content="已有账号密码，签到走纯 HTTP，不开浏览器">
                              <span className="text-success">✓</span>
                            </Tooltip>
                          )}
                        </span>
                      </TableCell>
                      <TableCell>
                        <Tooltip content={note} isDisabled={!note}>
                          <Chip color={chip.color} variant="flat" size="sm">
                            {chip.text}
                          </Chip>
                        </Tooltip>
                      </TableCell>
                      <TableCell className="text-default-600 font-mono tabular-nums">{money(acc.last_quota)}</TableCell>
                      <TableCell className="hidden xl:table-cell text-default-400 text-xs font-mono tabular-nums whitespace-nowrap">
                        {runAt(acc.last_run_at)}
                      </TableCell>
                      <TableCell>{actionRow(row, 'table')}</TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </>
        )}
      </div>

      <AccountForm account={editing} isOpen={isOpen} onClose={onClose} onSaved={afterSaved} />
      <IdpCookiesModal
        account={injecting}
        isOpen={injectOpen}
        onClose={onInjectClose}
        onInjected={(acc, result) => void afterInjected(acc, result)}
      />
      <DeleteAccountModal
        account={deleting}
        isOpen={deleteOpen}
        onClose={onDeleteClose}
        onConfirm={(acc, forgetProfile) => void remove(acc, forgetProfile)}
      />
      <ProfileCleanupModal
        isOpen={cleanupOpen}
        onClose={onCleanupClose}
        onDeleted={(removed) =>
          toast(removed ? `已清理 ${removed} 个浏览器 profile` : '没有删掉任何 profile', removed > 0)
        }
      />
      <PromoCard />
    </div>
  )
}
