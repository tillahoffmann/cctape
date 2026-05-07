import { lazy, Suspense } from 'react'
import { Link, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import Sessions from './pages/Sessions'
import Session from './pages/Session'
import { CassetteTape } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { HeaderSlotProvider } from './lib/HeaderSlot'
import { useHeaderSlotValue } from './lib/headerSlotContext'

// Non-default routes are split out so the session-page critical path stays
// lean. /usage in particular pulls in recharts (~1.16 MB raw, ~350 KB gzip).
const Usage = lazy(() => import('./pages/Usage'))
const Config = lazy(() => import('./pages/Config'))
const Setup = lazy(() => import('./pages/Setup'))

function NavItem({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink to={to} end>
      {({ isActive }) => (
        <Button variant={isActive ? 'secondary' : 'ghost'} size="sm">
          {children}
        </Button>
      )}
    </NavLink>
  )
}

function Header() {
  const slot = useHeaderSlotValue()
  return (
    <header className="border-b sticky top-0 z-50 bg-background">
      <div className="max-w-6xl mx-auto px-6 h-14 flex items-center gap-1 min-w-0">
        <Link
          to="/sessions"
          className="inline-flex h-8 items-center gap-2 rounded-md px-3 text-sm font-bold text-primary shrink-0 hover:bg-accent hover:text-accent-foreground"
        >
          <CassetteTape className="h-4 w-4" />
          cctape
        </Link>
        {slot ? (
          <div className="flex-1 min-w-0 flex items-center gap-1">{slot}</div>
        ) : (
          <>
            <nav className="flex items-center gap-1">
              <NavItem to="/sessions">Sessions</NavItem>
              <NavItem to="/usage">Usage</NavItem>
              <NavItem to="/config">Config</NavItem>
            </nav>
            <div className="flex-1" />
            <a
              href="https://github.com/tillahoffmann/cctape"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="GitHub repository"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground shrink-0"
            >
              <svg
                role="img"
                viewBox="0 0 24 24"
                aria-hidden="true"
                className="h-4 w-4"
                fill="currentColor"
              >
                <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.387.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.838 1.237 1.838 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.605-2.665-.3-5.467-1.332-5.467-5.93 0-1.31.468-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.3 1.23a11.5 11.5 0 0 1 3.003-.404c1.02.005 2.047.138 3.006.404 2.29-1.552 3.296-1.23 3.296-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.91 1.235 3.221 0 4.61-2.807 5.625-5.48 5.92.43.372.823 1.102.823 2.222 0 1.606-.014 2.898-.014 3.293 0 .322.216.697.825.576C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
              </svg>
            </a>
          </>
        )}
      </div>
    </header>
  )
}

export default function App() {
  return (
    <HeaderSlotProvider>
      <div className="min-h-screen flex flex-col">
        <Header />
        <main className="flex-1 max-w-6xl w-full mx-auto px-6 py-4">
          <Suspense fallback={null}>
            <Routes>
              <Route path="/" element={<Navigate to="/sessions" replace />} />
              <Route path="/sessions" element={<Sessions />} />
              <Route path="/sessions/:sessionId" element={<Session />} />
              <Route path="/usage" element={<Usage />} />
              <Route path="/config" element={<Config />} />
              <Route path="/settings" element={<Navigate to="/config" replace />} />
              <Route path="/setup" element={<Setup />} />
              <Route path="*" element={<div className="text-muted-foreground">Not found.</div>} />
            </Routes>
          </Suspense>
        </main>
      </div>
    </HeaderSlotProvider>
  )
}
