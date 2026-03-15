import { useEffect, useState } from 'react';
import { apiJson, apiFetch } from '../api';
import { useAuth } from '../AuthContext';
import { Navigate } from 'react-router-dom';
import type { SignupRequest, AdminUser } from '../types';

type Tab = 'requests' | 'users';

export default function AdminPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState<Tab>('requests');

  if (!user?.is_admin) return <Navigate to="/" replace />;

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Admin</h1>
      <div className="flex gap-2 mb-6">
        <button
          onClick={() => setTab('requests')}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
            tab === 'requests' ? 'bg-indigo-600 text-white' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
          }`}
        >
          Signup Requests
        </button>
        <button
          onClick={() => setTab('users')}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
            tab === 'users' ? 'bg-indigo-600 text-white' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
          }`}
        >
          Users
        </button>
      </div>

      {tab === 'requests' ? <SignupRequestsPanel /> : <UsersPanel />}
    </div>
  );
}

function SignupRequestsPanel() {
  const [requests, setRequests] = useState<SignupRequest[]>([]);
  const [statusFilter, setStatusFilter] = useState('pending');
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    apiJson<SignupRequest[]>(`/api/admin/signup-requests?status=${statusFilter}`)
      .then(data => { if (!cancelled) setRequests(data); })
      .catch(() => { if (!cancelled) setRequests([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [statusFilter, refreshKey]);

  const handleAction = async (id: number, action: 'approve' | 'deny') => {
    setActionError('');
    try {
      const res = await apiFetch(`/api/admin/signup-requests/${id}/${action}`, { method: 'POST' });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setActionError(data.error || `Failed to ${action}`);
        return;
      }
      setRefreshKey(k => k + 1);
    } catch {
      setActionError(`Failed to ${action}`);
    }
  };

  return (
    <div>
      <div className="flex gap-2 mb-4">
        {(['pending', 'approved', 'denied'] as const).map(s => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`px-3 py-1 rounded text-xs font-medium ${
              statusFilter === s ? 'bg-gray-600 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
            }`}
          >
            {s.charAt(0).toUpperCase() + s.slice(1)}
          </button>
        ))}
      </div>

      {actionError && (
        <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3 mb-4">
          {actionError}
        </div>
      )}

      {loading ? (
        <p className="text-gray-500 text-sm">Loading...</p>
      ) : requests.length === 0 ? (
        <p className="text-gray-500 text-sm">No {statusFilter} requests.</p>
      ) : (
        <div className="space-y-3">
          {requests.map(req => (
            <div key={req.id} className="bg-gray-900 border border-gray-800 rounded-lg p-4">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="text-white font-medium">{req.email}</div>
                  {req.display_name && <div className="text-gray-400 text-sm">{req.display_name}</div>}
                  {req.reason && <div className="text-gray-500 text-sm mt-1">{req.reason}</div>}
                  <div className="text-gray-600 text-xs mt-1">
                    Requested {new Date(req.created_at + 'Z').toLocaleDateString()}
                  </div>
                </div>
                {req.status === 'pending' && (
                  <div className="flex gap-2 shrink-0">
                    <button
                      onClick={() => handleAction(req.id, 'approve')}
                      className="px-3 py-1 bg-green-700 hover:bg-green-600 text-white text-sm rounded transition-colors"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => handleAction(req.id, 'deny')}
                      className="px-3 py-1 bg-red-700 hover:bg-red-600 text-white text-sm rounded transition-colors"
                    >
                      Deny
                    </button>
                  </div>
                )}
                {req.status !== 'pending' && (
                  <span className={`text-xs px-2 py-1 rounded ${
                    req.status === 'approved' ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'
                  }`}>
                    {req.status}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function UsersPanel() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionError, setActionError] = useState('');

  const loadUsers = async () => {
    setLoading(true);
    try {
      const data = await apiJson<AdminUser[]>('/api/admin/users');
      setUsers(data);
    } catch {
      setUsers([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadUsers(); }, []);

  const handleAction = async (userId: number, action: 'suspend' | 'reactivate') => {
    setActionError('');
    try {
      const res = await apiFetch(`/api/admin/users/${userId}/${action}`, { method: 'POST' });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setActionError(data.error || `Failed to ${action}`);
        return;
      }
      loadUsers();
    } catch {
      setActionError(`Failed to ${action}`);
    }
  };

  const handleRoleToggle = async (userId: number, currentRole: string) => {
    setActionError('');
    const newRole = currentRole === 'teacher' ? 'student' : 'teacher';
    try {
      const res = await apiFetch(`/api/admin/users/${userId}/role`, {
        method: 'POST',
        body: JSON.stringify({ role: newRole }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setActionError(data.error || 'Failed to change role');
        return;
      }
      loadUsers();
    } catch {
      setActionError('Failed to change role');
    }
  };

  const statusBadge = (status: string) => {
    const colors: Record<string, string> = {
      active: 'bg-green-900/50 text-green-400',
      suspended: 'bg-yellow-900/50 text-yellow-400',
      disabled: 'bg-red-900/50 text-red-400',
      pending: 'bg-gray-700 text-gray-300',
    };
    return (
      <span className={`text-xs px-2 py-0.5 rounded ${colors[status] || 'bg-gray-700 text-gray-300'}`}>
        {status}
      </span>
    );
  };

  const roleBadge = (role: string) => {
    if (role === 'teacher') {
      return <span className="text-xs px-2 py-0.5 rounded bg-pink-900/50 text-pink-400">teacher</span>;
    }
    return <span className="text-xs px-2 py-0.5 rounded bg-gray-700 text-gray-400">student</span>;
  };

  return (
    <div>
      {actionError && (
        <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3 mb-4">
          {actionError}
        </div>
      )}

      {loading ? (
        <p className="text-gray-500 text-sm">Loading...</p>
      ) : (
        <div className="space-y-3">
          {users.map(u => (
            <div key={u.id} className="bg-gray-900 border border-gray-800 rounded-lg p-4">
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-white font-medium">{u.email}</span>
                    {statusBadge(u.status)}
                    {roleBadge(u.role)}
                    {u.is_admin && <span className="text-xs px-2 py-0.5 rounded bg-indigo-900/50 text-indigo-400">admin</span>}
                  </div>
                  {u.display_name && <div className="text-gray-400 text-sm">{u.display_name}</div>}
                  <div className="text-gray-600 text-xs mt-1">
                    Joined {new Date(u.created_at + 'Z').toLocaleDateString()}
                    {u.last_login_at && <> &middot; Last login {new Date(u.last_login_at + 'Z').toLocaleDateString()}</>}
                  </div>
                </div>
                {u.email !== currentUser?.email && (
                  <div className="flex gap-2 shrink-0 flex-wrap">
                    <button
                      onClick={() => handleRoleToggle(u.id, u.role)}
                      className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-white text-xs rounded transition-colors"
                      title={`Switch to ${u.role === 'teacher' ? 'student' : 'teacher'}`}
                    >
                      {u.role === 'teacher' ? 'Set Student' : 'Set Teacher'}
                    </button>
                    {u.status === 'active' ? (
                      <button
                        onClick={() => handleAction(u.id, 'suspend')}
                        className="px-3 py-1 bg-yellow-700 hover:bg-yellow-600 text-white text-xs rounded transition-colors"
                      >
                        Suspend
                      </button>
                    ) : u.status === 'suspended' ? (
                      <button
                        onClick={() => handleAction(u.id, 'reactivate')}
                        className="px-3 py-1 bg-green-700 hover:bg-green-600 text-white text-xs rounded transition-colors"
                      >
                        Reactivate
                      </button>
                    ) : null}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
