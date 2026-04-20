import { createContext, useContext, useEffect, type ReactNode } from 'react'

export type Slot = ReactNode | null

export interface HeaderSlotCtx {
  slot: Slot
  setSlot: (s: Slot) => void
}

export const HeaderSlotContext = createContext<HeaderSlotCtx | null>(null)

export function useHeaderSlotValue(): Slot {
  const ctx = useContext(HeaderSlotContext)
  return ctx?.slot ?? null
}

export function useHeaderSlot(node: ReactNode) {
  const ctx = useContext(HeaderSlotContext)
  useEffect(() => {
    if (!ctx) return
    ctx.setSlot(node)
    return () => ctx.setSlot(null)
  }, [ctx, node])
}
