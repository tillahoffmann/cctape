import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import Sessions from './pages/Sessions'
import Session from './pages/Session'
import Usage from './pages/Usage'
import { Button } from '@/components/ui/button'

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

export default function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b sticky top-0 z-50 bg-background">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center gap-6">
          <h1 className="text-sm font-semibold">ccaudit</h1>
          <nav className="flex items-center gap-1">
            <NavItem to="/sessions">Sessions</NavItem>
            <NavItem to="/usage">Usage</NavItem>
          </nav>
        </div>
      </header>
      <main className="flex-1 max-w-6xl w-full mx-auto px-6 py-8">
        <Routes>
          <Route path="/" element={<Navigate to="/sessions" replace />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/sessions/:sessionId" element={<Session />} />
          <Route path="/usage" element={<Usage />} />
          <Route path="*" element={<div className="text-muted-foreground">Not found.</div>} />
        </Routes>
      </main>
    </div>
  )
}
