import { useEffect, useState } from 'react'
import { Button, Modal, ModalBody, ModalContent, ModalFooter, ModalHeader, Switch } from '@heroui/react'
import { type Account } from './api'

interface DeleteAccountModalProps {
  account: Account | null
  isOpen: boolean
  onClose: () => void
  /** Given the owner's choice about the profile. The caller does the deleting, so one place
   *  still owns the busy state and the toast. */
  onConfirm: (account: Account, forgetProfile: boolean) => void
}

const BROWSER_METHODS = ['linuxdo', 'github']

/** Confirm deleting an account, and decide what happens to its browser profile.
 *
 *  A modal rather than `window.confirm`, which cannot hold a switch — and the switch is the
 *  point. The profile is not a cache: it holds the *IdP* session, the whole github.com or
 *  linux.do login. Until this existed the profile was always kept, so a deleted account left
 *  its credential on disk with nothing on any screen naming the directory.
 *
 *  Defaulting to delete, because the two mistakes are not the same size: a profile removed by
 *  accident costs one visible login, while a profile kept by accident is a live session nobody
 *  is looking after. */
export default function DeleteAccountModal({ account, isOpen, onClose, onConfirm }: DeleteAccountModalProps) {
  const [forget, setForget] = useState(true)

  useEffect(() => {
    if (isOpen) setForget(true)
  }, [isOpen, account])

  // Only an OAuth account has an IdP session worth naming. A password or token account may
  // still have a profile (a browser check-in leaves one), so the switch stays either way —
  // what changes is how much the sentence has to warn about.
  const oauth = account ? BROWSER_METHODS.includes(account.login_method) : false

  return (
    <Modal isOpen={isOpen} onClose={onClose} size="md">
      <ModalContent>
        <ModalHeader className="flex-col items-start gap-1 pb-2">
          <span>删除「{account?.name}」？</span>
          <span className="text-tiny font-normal text-default-500">这一步不能撤销</span>
        </ModalHeader>
        <ModalBody className="gap-4 py-2">
          <p className="text-small text-default-600">账号和它存着的凭据会一起删掉。</p>
          <div className="flex items-start justify-between gap-3 rounded-medium bg-default-100 px-3 py-2">
            <div className="min-w-0">
              <p className="text-small">同时删除浏览器 profile</p>
              <p className="text-tiny text-default-500">
                {oauth
                  ? '这个 profile 里存着授权登录用的会话，等于整个账号的登录状态。不删的话，账号没了它还留在硬盘上。'
                  : '这个账号如果用浏览器签到过，硬盘上就有一份 profile。不删的话，账号没了它还留着。'}
              </p>
            </div>
            <Switch isSelected={forget} onValueChange={setForget} size="sm" aria-label="同时删除浏览器 profile" />
          </div>
          {!forget && (
            <p className="text-tiny text-warning">
              留下的 profile 之后没有账号认领，会出现在「清理 profile」里，可以在那儿再删。
            </p>
          )}
        </ModalBody>
        <ModalFooter className="border-t border-default-200">
          <Button variant="flat" onPress={onClose}>
            取消
          </Button>
          <Button
            color="danger"
            onPress={() => {
              if (account) onConfirm(account, forget)
              onClose()
            }}
          >
            删除
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  )
}
