import { useState, type ReactNode } from 'react'
import { HeaderSlotContext, type Slot } from './headerSlotContext'

export function HeaderSlotProvider({ children }: { children: ReactNode }) {
  const [slot, setSlot] = useState<Slot>(null)
  return (
    <HeaderSlotContext.Provider value={{ slot, setSlot }}>
      {children}
    </HeaderSlotContext.Provider>
  )
}
