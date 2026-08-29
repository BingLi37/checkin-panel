import { useEffect, useRef, useState, type RefObject } from 'react'

/** Sticky state from a zero-height sentinel: attach the ref above the sticky element and
 *  `stuck` flips once the sentinel scrolls out. Deliberately not a scroll listener — that
 *  would set React state on every frame. */
export function useStuck(): { sentinel: RefObject<HTMLDivElement>; stuck: boolean } {
  const sentinel = useRef<HTMLDivElement>(null)
  const [stuck, setStuck] = useState(false)

  useEffect(() => {
    const node = sentinel.current
    if (!node) return
    const observer = new IntersectionObserver(([entry]) => setStuck(!entry.isIntersecting), { threshold: 1 })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  return { sentinel, stuck }
}
