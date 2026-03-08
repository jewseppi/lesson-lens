import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import { FontSizeToggle } from '../FontSizeContext';

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

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
        <div className="max-w-6xl mx-auto px-4 flex items-center justify-between h-14">
          <div className="flex items-center gap-1">
            <NavLink to="/" className="text-lg font-bold text-indigo-400 mr-6">
              📖 LessonLens
            </NavLink>
            <NavLink to="/" className={linkClass} end>Dashboard</NavLink>
            <NavLink to="/upload" className={linkClass}>Sync</NavLink>
            <NavLink to="/sessions" className={linkClass}>Sessions</NavLink>
          </div>
          <div className="flex items-center gap-3">
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
        </div>
      </nav>

      {/* Content */}
      <main className="max-w-6xl mx-auto px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
