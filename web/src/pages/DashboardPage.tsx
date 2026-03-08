import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiJson } from '../api';
import type { Session, Upload } from '../types';

export default function DashboardPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      apiJson<Session[]>('/api/sessions').catch(() => []),
      apiJson<Upload[]>('/api/uploads').catch(() => []),
    ]).then(([s, u]) => {
      setSessions(s);
      setUploads(u);
    }).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-gray-400">Loading...</div>;

  const recentSessions = sessions.slice(0, 5);
  const totalLessons = sessions.filter(s => s.lesson_content_count >= 3).length;
  const summarized = sessions.filter(s => s.has_summary).length;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>

      {/* Stats cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Sessions" value={sessions.length} />
        <StatCard label="Lessons (3+ content)" value={totalLessons} />
        <StatCard label="Summarized" value={summarized} />
        <StatCard label="Uploads" value={uploads.length} />
      </div>

      {/* Quick actions */}
      <div className="flex gap-3 flex-wrap">
        <Link
          to="/upload"
          className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg font-medium transition-colors"
        >
          Sync Chat Export
        </Link>
        <Link
          to="/sessions"
          className="bg-gray-800 hover:bg-gray-700 text-white px-4 py-2 rounded-lg font-medium transition-colors border border-gray-700"
        >
          Browse Sessions
        </Link>
      </div>

      {/* Recent sessions */}
      {recentSessions.length > 0 && (
        <div>
          <h2 className="text-lg font-semibold mb-3">Recent Sessions</h2>
          <div className="space-y-2">
            {recentSessions.map(s => (
              <Link
                key={s.session_id}
                to={`/sessions/${s.session_id}`}
                className="block bg-gray-900 border border-gray-800 rounded-lg p-4 hover:border-indigo-600 transition-colors"
              >
                <div className="flex justify-between items-center">
                  <div>
                    <span className="font-medium">{s.date}</span>
                    <span className="text-gray-400 ml-2 text-sm">{s.start_time}–{s.end_time}</span>
                  </div>
                  <div className="flex items-center gap-3 text-sm">
                    <span className="text-gray-400">{s.lesson_content_count} lesson msgs</span>
                    {s.has_summary && (
                      <span className="bg-green-900 text-green-300 px-2 py-0.5 rounded text-xs">Summarized</span>
                    )}
                    <ConfidenceBadge level={s.boundary_confidence} />
                  </div>
                </div>
              </Link>
            ))}
          </div>
          {sessions.length > 5 && (
            <Link to="/sessions" className="text-indigo-400 hover:text-indigo-300 text-sm mt-2 inline-block">
              View all {sessions.length} sessions →
            </Link>
          )}
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <div className="text-2xl font-bold text-indigo-400">{value}</div>
      <div className="text-sm text-gray-400">{label}</div>
    </div>
  );
}

function ConfidenceBadge({ level }: { level: string }) {
  const colors: Record<string, string> = {
    high: 'bg-green-900 text-green-300',
    medium: 'bg-yellow-900 text-yellow-300',
    low: 'bg-red-900 text-red-300',
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs ${colors[level] || 'bg-gray-700 text-gray-400'}`}>
      {level}
    </span>
  );
}
