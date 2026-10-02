import { createContext, useContext } from 'react'
import type { SessionView } from '@/api/types'
import type { RealmSession } from './useRealmSession'

export const CustomerSessionContext = createContext<RealmSession<SessionView> | null>(null)

export function useCustomerSession() {
  const ctx = useContext(CustomerSessionContext)
  if (!ctx) throw new Error('useCustomerSession must be used inside CustomerSessionProvider')
  return ctx
}
