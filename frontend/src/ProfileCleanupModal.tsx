import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Checkbox,
  Chip,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Spinner,
} from '@heroui/react'
import { api, type OrphanProfile } from './api'

interface ProfileCleanupModalProps {
  isOpen: boolean
  onClose: () => void
  /** So the caller can say what happened in its own toast. */
  onDeleted: (removed: number) => void
}

function mb(bytes: number): string {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`
}

/** Browser profiles no account claims any more, and a way to delete them.
 *
 *  These accumulate: a profile is created on the first browser login and nothing removed it,
 *  so renaming an account or deleting one (before the delete dialog offered the choice) left
 *  the directory behind. Size is the visible cost — tens of MB each — but the reason this
 *  screen exists is that an orphan may still hold a live IdP session.
 *
 *  Nothing is pre-selected and nothing is deleted without naming it. The server checks each
 *  key against its own fresh listing before removing anything, because this list is a
 *  snapshot and the operation behind it is a recursive delete. */
export default function ProfileCleanupModal({ isOpen, onClose, onDeleted }: ProfileCleanupModalProps) {
  const [profiles, setProfiles] = useState<OrphanProfile[]>([])
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const found = await api.orphanProfiles()
      setProfiles(found.profiles)
      setChosen(new Set())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (isOpen) void load()
  }, [isOpen, load])

  function toggle(key: string, on: boolean) {
    setChosen((prev) => {
      const next = new Set(prev)
      if (on) next.add(key)
      else next.delete(key)
      return next
    })
  }

  async function remove() {
    setBusy(true)
    setError(null)
    try {
      const { removed } = await api.deleteOrphanProfiles(Array.from(chosen))
      onDeleted(removed)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const total = profiles.reduce((sum, p) => sum + p.bytes, 0)
  const picked = profiles.filter((p) => chosen.has(p.key)).reduce((sum, p) => sum + p.bytes, 0)
  const allPicked = profiles.length > 0 && chosen.size === profiles.length

  return (
    <Modal isOpen={isOpen} onClose={onClose} size="lg" scrollBehavior="inside">
      <ModalContent className="max-h-[88vh]">
        <ModalHeader className="flex-col items-start gap-1 pb-2">
          <span>清理浏览器 profile</span>
          <span className="text-tiny font-normal text-default-500">
            {loading ? '正在查看硬盘…' : `${profiles.length} 个没有账号认领，共 ${mb(total)}`}
          </span>
        </ModalHeader>
        <ModalBody className="gap-3 overflow-y-auto py-2">
          <p className="text-tiny text-default-500">
            每个账号第一次用浏览器登录时会建一份 profile。改名或删号都可能留下不再有人认领的那份 ——
            它占几十 MB，更要紧的是里面可能还存着可用的授权登录会话。
          </p>
          {error && <p className="text-sm text-danger">{error}</p>}
          {loading ? (
            <div className="flex justify-center py-6">
              <Spinner size="sm" />
            </div>
          ) : profiles.length === 0 ? (
            <p className="py-6 text-center text-small text-default-500">没有要清理的，硬盘是干净的。</p>
          ) : (
            <>
              <Checkbox
                size="sm"
                isSelected={allPicked}
                isIndeterminate={chosen.size > 0 && !allPicked}
                onValueChange={(on) => setChosen(on ? new Set(profiles.map((p) => p.key)) : new Set())}
              >
                <span className="text-small">全选</span>
              </Checkbox>
              <ul className="flex flex-col gap-1">
                {profiles.map((p) => (
                  <li
                    key={p.key}
                    className="flex items-center justify-between gap-3 rounded-medium bg-default-100 px-3 py-2"
                  >
                    <Checkbox size="sm" isSelected={chosen.has(p.key)} onValueChange={(on) => toggle(p.key, on)}>
                      <span className="min-w-0">
                        <span className="block truncate font-mono text-tiny">{p.key}</span>
                        {p.old_layout && (
                          <Chip size="sm" variant="flat" className="mt-1 h-4 text-[10px]">
                            旧目录结构
                          </Chip>
                        )}
                      </span>
                    </Checkbox>
                    <span className="shrink-0 font-mono text-tiny text-default-500">{mb(p.bytes)}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </ModalBody>
        <ModalFooter className="border-t border-default-200">
          <Button variant="flat" onPress={onClose}>
            关闭
          </Button>
          <Button color="danger" isLoading={busy} isDisabled={chosen.size === 0} onPress={() => void remove()}>
            删除选中的 {chosen.size ? `${chosen.size} 个（${mb(picked)}）` : ''}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  )
}
