import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import Sessions from './pages/Sessions'
import Session from './pages/Session'
import Usage from './pages/Usage'

function NavItem({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      end
      className={({ isActive }) =>
        `px-3 py-1.5 rounded-md text-sm transition-colors ${
          isActive
            ? 'bg-zinc-800 text-zinc-50'
            : 'text-zinc-400 hover:text-zinc-100 hover:bg-zinc-900'
        }`
      }
    >
      {children}
    </NavLink>
  )
}

export default function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-zinc-800 bg-zinc-950/80 backdrop-blur sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center gap-6">
          <h1 className="text-sm font-semibold tracking-tight text-zinc-100">
            Claude Context
          </h1>
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
          <Route path="*" element={<div className="text-zinc-400">Not found.</div>} />
        </Routes>
      </main>
    </div>
  )
}
