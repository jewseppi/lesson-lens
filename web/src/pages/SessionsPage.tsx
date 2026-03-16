import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { apiJson, apiFetch } from '../api';
import type { Session, SharedLink } from '../types';

export default function SessionsPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [showArchived, setShowArchived] = useState(false);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    apiJson<Session[]>('/api/sessions')
      .then(setSessions)
      .finally(() => setLoading(false));
  }, []);

  const toggleArchive = async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      const res = await apiFetch(`/api/sessions/${sessionId}/archive`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setSessions(prev => prev.map(s =>
          s.session_id === sessionId ? { ...s, is_archived: data.is_archived } : s
        ));
      }
    } catch { /* ignore */ }
  };

  if (loading) return <div className="text-gray-400">Loading sessions...</div>;
  if (sessions.length === 0) {
    return (
      <div className="text-center py-12">
        <div className="text-4xl mb-3">📭</div>
        <p className="text-gray-400 mb-4">No sessions yet. Upload a chat export to get started.</p>
        <Link to="/upload" className="text-indigo-400 hover:text-indigo-300">Upload now →</Link>
      </div>
    );
  }

  // Separate active and archived
  const activeSessions = sessions.filter(s => !s.is_archived);
  const archivedSessions = sessions.filter(s => s.is_archived);

  // Apply text search
  const searchFiltered = activeSessions.filter(s => {
    if (!filter) return true;
    const q = filter.toLowerCase();
    return s.date.includes(q) || s.session_id.toLowerCase().includes(q) ||
      s.topics.some(t => t.toLowerCase().includes(q));
  });

  const archivedFiltered = archivedSessions.filter(s => {
    if (!filter) return true;
    const q = filter.toLowerCase();
    return s.date.includes(q) || s.session_id.toLowerCase().includes(q) ||
      s.topics.some(t => t.toLowerCase().includes(q));
  });

  // Sort by date descending
  const sorted = [...searchFiltered].sort((a, b) =>
    b.date.localeCompare(a.date) || b.start_time.localeCompare(a.start_time)
  );
  const archivedSorted = [...archivedFiltered].sort((a, b) =>
    b.date.localeCompare(a.date) || b.start_time.localeCompare(a.start_time)
  );

  // Group by month
  const groupByMonth = (list: Session[]) => {
    const map = new Map<string, Session[]>();
    for (const s of list) {
      const month = s.date.slice(0, 7);
      if (!map.has(month)) map.set(month, []);
      map.get(month)!.push(s);
    }
    return map;
  };

  const groupedByMonth = groupByMonth(sorted);

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-2xl font-bold">Sessions</h1>
        <span className="text-sm text-gray-400">
          {searchFiltered.length} session{searchFiltered.length !== 1 ? 's' : ''}
          {archivedSessions.length > 0 && ` · ${archivedSessions.length} archived`}
        </span>
      </div>

      {/* Search */}
      <input
        type="text"
        placeholder="Search by date, topic..."
        value={filter}
        onChange={e => setFilter(e.target.value)}
        className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500 w-full sm:w-64"
      />

      {/* Active sessions grouped by month */}
      {Array.from(groupedByMonth.entries()).map(([month, items]) => (
        <div key={month}>
          <h3 className="text-sm font-medium text-gray-500 mb-2 sticky top-0 bg-gray-950 py-1">{month}</h3>
          <div className="space-y-2">
            {items.map(s => (
              <SessionCard key={s.session_id} session={s} onArchive={toggleArchive} />
            ))}
          </div>
        </div>
      ))}

      {sorted.length === 0 && (
        <p className="text-gray-500 text-sm py-4 text-center">
          No sessions match your search.
        </p>
      )}

      {/* Archived section */}
      {archivedSessions.length > 0 && (
        <div className="border-t border-gray-800 pt-4 mt-6">
          <button
            onClick={() => setShowArchived(!showArchived)}
            className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-300 transition-colors"
          >
            <span className={`transition-transform ${showArchived ? 'rotate-90' : ''}`}>▶</span>
            Archived ({archivedFiltered.length})
          </button>

          {showArchived && (
            <div className="space-y-2 mt-3">
              {archivedSorted.map(s => (
                <SessionCard key={s.session_id} session={s} onArchive={toggleArchive} />
              ))}
              {archivedFiltered.length === 0 && (
                <p className="text-gray-600 text-sm">No archived sessions match your search.</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SessionCard({ session: s, onArchive }: { session: Session; onArchive: (id: string, e: React.MouseEvent) => void }) {
  const navigate = useNavigate();

  return (
    <div
      onClick={() => navigate(`/sessions/${s.session_id}`)}
      className={`bg-gray-900 border rounded-lg p-4 transition-colors cursor-pointer ${
        s.is_archived
          ? 'border-gray-800/50 opacity-70 hover:opacity-100 hover:border-gray-600'
          : 'border-gray-800 hover:border-indigo-600'
      }`}
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:justify-between sm:items-start">
        <div className="min-w-0">
          <div className="font-medium">{s.date}</div>
          <div className="text-sm text-gray-400">{s.start_time}–{s.end_time}</div>
          {s.topics.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1">
              {s.topics.map(t => (
                <span key={t} className="bg-gray-800 text-gray-300 text-xs px-2 py-0.5 rounded">
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="text-sm sm:text-right">
          <div className="flex gap-3 sm:justify-end">
            {s.teacher_message_count > 0 && (
              <span>
                <span className="text-pink-400 font-medium">{s.teacher_message_count}</span>
                <span className="text-gray-500"> teacher</span>
              </span>
            )}
            {s.student_message_count > 0 && (
              <span>
                <span className="text-cyan-400 font-medium">{s.student_message_count}</span>
                <span className="text-gray-500"> me</span>
              </span>
            )}
          </div>
          <div className="text-gray-500">{s.message_count} total</div>
          {s.has_summary && (
            <Link
              to={`/sessions/${s.session_id}/summary`}
              onClick={e => e.stopPropagation()}
              className="inline-block mt-1 bg-green-900 text-green-300 px-2 py-0.5 rounded text-xs hover:bg-green-800 transition-colors"
            >
              Summary
            </Link>
          )}
          {s.needs_summary && !s.has_summary && (
            <span className="inline-block mt-1 text-gray-600 text-xs">No summary</span>
          )}
          {/* Unarchive button only shown in archived section */}
          {s.is_archived && (
            <button
              onClick={(e) => onArchive(s.session_id, e)}
              className="mt-1 text-xs text-gray-500 hover:text-gray-300 transition-colors"
              title="Unarchive"
            >
              ↩ Unarchive
            </button>
          )}
        </div>
      </div>

      {s.shared_links.length > 0 && (
        <SharedLinkPanel link={s.shared_links[0]} className="mt-3" />
      )}
    </div>
  );
}

function SharedLinkPanel({ link, className = '' }: { link: SharedLink; className?: string }) {
  return (
    <div className={`rounded-lg border border-sky-800/60 bg-sky-950/20 p-3 ${className}`.trim()}>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-sky-300">Shared Link</div>
          <div className="truncate text-sm text-gray-300">{link.label || link.url}</div>
        </div>
        <a
          href={link.url}
          target="_blank"
          rel="noreferrer"
          onClick={event => event.stopPropagation()}
          className="inline-flex items-center justify-center rounded-lg bg-sky-700 px-3 py-2 text-sm font-medium text-white hover:bg-sky-600"
        >
          Open Link
        </a>
      </div>
      {(link.before_text || link.after_text) && (
        <div className="mt-2 text-xs text-gray-400">
          {link.before_text && <div>{link.before_text}</div>}
          {link.after_text && <div>{link.after_text}</div>}
        </div>
      )}
    </div>
  );
}
