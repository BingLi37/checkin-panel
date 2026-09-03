import { useEffect, useState } from 'react'
import {
  Button,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Switch,
  Textarea,
} from '@heroui/react'
import { api, type Account, type IdpInjection } from './api'
import { FIELD } from './AccountForm'

interface IdpCookiesModalProps {
  account: Account | null
  isOpen: boolean
  onClose: () => void
  /** Called once an injection reports back, so the list can pick up the session it won. */
  onInjected: (account: Account, result: IdpInjection) => void
}

const PROVIDER_NAME: Record<string, string> = { linuxdo: 'LinuxDO', github: 'GitHub' }
// Named, not described: a cookie extension reads the tab it is on, and "the site's page" is
// what sent someone to the check-in site's tab and an empty cookie list.
const PROVIDER_HOST: Record<string, string> = { linuxdo: 'linux.do', github: 'github.com' }

/** Paste an exported IdP session into an account's browser profile.
 *
 *  The server-deployment path: the daily OAuth hop runs headless fine, but the IdP session
 *  it renews from normally arrives through one visible login window, which a box with no
 *  desktop cannot show. This carries that session over as cookie values instead.
 *
 *  The disclosure at the bottom is not decoration. What gets pasted here is the whole
 *  forum or GitHub account, not a site credential, and the panel has no login of its own. */
export default function IdpCookiesModal({ account, isOpen, onClose, onInjected }: IdpCookiesModalProps) {
  const [cookies, setCookies] = useState('')
  const [verify, setVerify] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!isOpen) return
    setCookies('')
    setVerify(true)
    setError(null)
  }, [isOpen, account])

  const provider = account ? (PROVIDER_NAME[account.login_method] ?? account.login_method) : ''
  const host = account ? (PROVIDER_HOST[account.login_method] ?? '') : ''

  async function submit() {
    if (!account) return
    setError(null)
    setBusy(true)
    try {
      const result = await api.injectIdpCookies(account.id, cookies, verify)
      onInjected(account, result)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal isOpen={isOpen} onClose={onClose} size="lg" scrollBehavior="inside">
      <ModalContent className="max-h-[88vh]">
        <ModalHeader className="flex-col items-start gap-1 pb-2">
          <span>注入 {provider} 会话</span>
          <span className="text-tiny font-normal text-default-500">{account?.name}</span>
        </ModalHeader>
        <ModalBody className="gap-4 overflow-y-auto py-2">
          <ol className="list-decimal space-y-1 pl-4 text-xs text-default-500">
            <li>在你自己电脑的浏览器里登录 {provider}</li>
            <li>
              用 cookie 扩展（如 Cookie-Editor），<b>停在 {host || provider} 这个页面上</b>点导出 / Export
              —— 不是签到站点的页面
            </li>
            <li>整段 JSON 粘到下面</li>
          </ol>
          <Textarea
            label="导出的 cookie JSON"
            labelPlacement="outside"
            value={cookies}
            onValueChange={setCookies}
            minRows={6}
            maxRows={14}
            placeholder='[{"domain": ".linux.do", "name": "_t", "value": "...", ...}, ...]'
            description="整段粘进来就行，面板自己挑出要用的那几条"
            classNames={{ ...FIELD, input: 'font-mono text-xs' }}
          />
          <div className="flex items-start justify-between gap-3 rounded-medium bg-default-100 px-3 py-2">
            <div className="min-w-0">
              <p className="text-small">注入后立刻验证一次</p>
              <p className="text-tiny text-default-500">
                跑一次无头授权登录，当场确认这段会话真的能用。关掉的话，成不成要等下次定时签到才知道。
              </p>
            </div>
            <Switch isSelected={verify} onValueChange={setVerify} size="sm" aria-label="注入后立刻验证一次" />
          </div>
          <p className="text-tiny text-warning">
            这段 cookie 等于你的整个 {provider} 账号，不只是签到站点的凭据。它写进这个账号的浏览器 profile，
            面板不会存进数据库；但面板本身没有登录保护，谁能访问面板就能用这个身份。
          </p>
          {error && <p className="text-sm text-danger">{error}</p>}
        </ModalBody>
        <ModalFooter className="border-t border-default-200">
          <Button variant="flat" onPress={onClose}>
            取消
          </Button>
          <Button color="primary" isLoading={busy} isDisabled={!cookies.trim()} onPress={() => void submit()}>
            注入
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  )
}
