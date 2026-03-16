import { useState } from 'react';
import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import { FontSizeToggle } from '../FontSizeContext';

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const handleLogout = () => {
    setMobileMenuOpen(false);
    logout();
    navigate('/login');
  };

  const closeMobileMenu = () => setMobileMenuOpen(false);

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `px-3 py-2 rounded-md text-sm font-medium transition-colors ${
      isActive
        ? 'bg-indigo-600 text-white'
        : 'text-gray-300 hover:bg-gray-700 hover:text-white'
    }`;

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      {/* Nav */}
      <nav className="bg-gray-900 border-b border-gray-800">
        <div className="max-w-6xl mx-auto px-3 sm:px-4 min-h-14 flex items-center justify-between gap-3 py-2">
          <div className="flex items-center gap-2 min-w-0">
            <NavLink to="/" className="mr-6 inline-flex items-center gap-2 text-lg font-bold text-indigo-400 min-w-0">
              <img src="/lessonlens-favicon.svg" alt="" aria-hidden="true" className="h-7 w-7 shrink-0 rounded-md" />
              <span className="truncate">LessonLens</span>
            </NavLink>
            <div className="hidden md:flex items-center gap-1">
              <NavLink to="/" className={linkClass} end>Dashboard</NavLink>
              <NavLink to="/upload" className={linkClass}>Sync</NavLink>
              <NavLink to="/sessions" className={linkClass}>Sessions</NavLink>
              {user?.is_admin && <NavLink to="/eval" className={linkClass}>Eval</NavLink>}
              {user?.is_admin && <NavLink to="/admin" className={linkClass}>Admin</NavLink>}
            </div>
          </div>
          <div className="flex items-center gap-2 sm:gap-3">
            <div className="hidden md:flex items-center gap-3">
              <NavLink
                to="/settings"
                title="Settings"
                aria-label="Settings"
                className={({ isActive }: { isActive: boolean }) =>
                  `px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-indigo-600 text-white'
                      : 'text-gray-300 hover:bg-gray-700 hover:text-white'
                  }`
                }
              >
                <span aria-hidden="true">⚙</span>
              </NavLink>
              <FontSizeToggle />
              <span className="text-sm text-gray-400">{user?.display_name || user?.email}</span>
              <button
                onClick={handleLogout}
                className="text-sm text-gray-400 hover:text-white transition-colors"
              >
                Logout
              </button>
            </div>

            <button
              type="button"
              onClick={() => setMobileMenuOpen(open => !open)}
              className="md:hidden inline-flex items-center justify-center rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200"
              aria-label={mobileMenuOpen ? 'Close navigation menu' : 'Open navigation menu'}
              aria-expanded={mobileMenuOpen}
            >
              {mobileMenuOpen ? '✕' : '☰'}
            </button>
          </div>
        </div>

        {mobileMenuOpen && (
          <div className="md:hidden border-t border-gray-800 bg-gray-900/95">
            <div className="max-w-6xl mx-auto px-3 py-3 space-y-3 sm:px-4">
              <div className="grid grid-cols-2 gap-2">
                <NavLink to="/" onClick={closeMobileMenu} className={linkClass} end>Dashboard</NavLink>
                <NavLink to="/upload" onClick={closeMobileMenu} className={linkClass}>Sync</NavLink>
                <NavLink to="/sessions" onClick={closeMobileMenu} className={linkClass}>Sessions</NavLink>
                <NavLink to="/settings" onClick={closeMobileMenu} className={linkClass}>Settings</NavLink>
                {user?.is_admin && <NavLink to="/eval" onClick={closeMobileMenu} className={linkClass}>Eval</NavLink>}
                {user?.is_admin && <NavLink to="/admin" onClick={closeMobileMenu} className={linkClass}>Admin</NavLink>}
              </div>

              <div className="flex items-center justify-between gap-3 rounded-lg border border-gray-800 bg-gray-950/60 px-3 py-2">
                <div className="min-w-0">
                  <div className="truncate text-sm text-gray-300">{user?.display_name || user?.email}</div>
                  <div className="text-xs text-gray-500">Signed in</div>
                </div>
                <FontSizeToggle />
              </div>

              <button
                onClick={handleLogout}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-4 py-3 text-sm font-medium text-gray-200"
              >
                Logout
              </button>
            </div>
          </div>
        )}
      </nav>

      {/* Content */}
      <main className="max-w-6xl mx-auto px-3 py-4 sm:px-4 sm:py-6">
        <Outlet />
      </main>
    </div>
  );
}
