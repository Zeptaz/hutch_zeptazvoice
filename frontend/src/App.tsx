import { lazy, Suspense } from 'react'
import { LoadingState } from '@/components/states'

const VoicePage = lazy(() => import('@/routes/VoicePage'))

export default function App() {
  return (
    <Suspense fallback={<LoadingState />}>
      <VoicePage />
    </Suspense>
  )
}
