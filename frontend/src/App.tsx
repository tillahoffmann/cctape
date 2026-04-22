import { Link, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import Sessions from './pages/Sessions'
import Session from './pages/Session'
import Usage from './pages/Usage'
import Settings from './pages/Settings'
import { CassetteTape } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { HeaderSlotProvider } from './lib/HeaderSlot'
import { useHeaderSlotValue } from './lib/headerSlotContext'

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
          <nav className="flex items-center gap-1">
            <NavItem to="/sessions">Sessions</NavItem>
            <NavItem to="/usage">Usage</NavItem>
            <NavItem to="/settings">Settings</NavItem>
          </nav>
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
          <Routes>
            <Route path="/" element={<Navigate to="/sessions" replace />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/sessions/:sessionId" element={<Session />} />
            <Route path="/usage" element={<Usage />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<div className="text-muted-foreground">Not found.</div>} />
          </Routes>
        </main>
      </div>
    </HeaderSlotProvider>
  )
}
