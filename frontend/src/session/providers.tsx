import type { ReactNode } from 'react'
import { customerApi } from '@/api/endpoints'
import { CustomerSessionContext } from './context'
import { useRealmSession } from './useRealmSession'

export function CustomerSessionProvider({ children }: { children: ReactNode }) {
  const value = useRealmSession('customer', customerApi.getSession, customerApi.logout)
  return <CustomerSessionContext value={value}>{children}</CustomerSessionContext>
}
