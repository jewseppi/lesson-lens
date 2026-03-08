import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { apiJson } from '../api';
import type { Session, SharedLink } from '../types';

type SortMode = 'date' | 'content';

export default function SessionsPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [sortMode, setSortMode] = useState<SortMode>('date');
  const [filter, setFilter] = useState('');

  useEffect(() => {
    apiJson<Session[]>('/api/sessions')
      .then(setSessions)
      .finally(() => setLoading(false));
  }, []);

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

  // Filter
  const filtered = sessions.filter(s => {
    if (!filter) return true;
    const q = filter.toLowerCase();
    return s.date.includes(q) || s.session_id.toLowerCase().includes(q) ||
      s.topics.some(t => t.toLowerCase().includes(q));
  });

  // Sort
  const sorted = [...filtered].sort((a, b) => {
    if (sortMode === 'content') return b.lesson_content_count - a.lesson_content_count;
    return b.date.localeCompare(a.date) || b.start_time.localeCompare(a.start_time);
  });

  // Group by month for date view
  const groupedByMonth = new Map<string, Session[]>();
  for (const s of sorted) {
    const month = s.date.slice(0, 7); // YYYY-MM
    if (!groupedByMonth.has(month)) groupedByMonth.set(month, []);
    groupedByMonth.get(month)!.push(s);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-2xl font-bold">Sessions</h1>
        <span className="text-sm text-gray-400">{filtered.length} of {sessions.length}</span>
      </div>

      {/* Controls */}
      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap">
        <input
          type="text"
          placeholder="Search by date, topic..."
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500 w-full sm:w-64"
        />
        <div className="flex bg-gray-800 rounded-lg border border-gray-700 overflow-hidden w-full sm:w-auto">
          <button
            onClick={() => setSortMode('date')}
            className={`flex-1 px-3 py-2 text-sm transition-colors ${sortMode === 'date' ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
          >
            By Date
          </button>
          <button
            onClick={() => setSortMode('content')}
            className={`flex-1 px-3 py-2 text-sm transition-colors ${sortMode === 'content' ? 'bg-indigo-600 text-white' : 'text-gray-400 hover:text-white'}`}
          >
            By Content
          </button>
        </div>
      </div>

      {/* List */}
      {sortMode === 'date' ? (
        // Grouped by month
        Array.from(groupedByMonth.entries()).map(([month, items]) => (
          <div key={month}>
            <h3 className="text-sm font-medium text-gray-500 mb-2 sticky top-0 bg-gray-950 py-1">{month}</h3>
            <div className="space-y-2">
              {items.map(s => <SessionCard key={s.session_id} session={s} />)}
            </div>
          </div>
        ))
      ) : (
        <div className="space-y-2">
          {sorted.map(s => <SessionCard key={s.session_id} session={s} />)}
        </div>
      )}
    </div>
  );
}

function SessionCard({ session: s }: { session: Session }) {
  const navigate = useNavigate();

  return (
    <div
      onClick={() => navigate(`/sessions/${s.session_id}`)}
      className="bg-gray-900 border border-gray-800 rounded-lg p-4 hover:border-indigo-600 transition-colors cursor-pointer"
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
          <div>
            <span className="text-indigo-400 font-medium">{s.lesson_content_count}</span>
            <span className="text-gray-500"> lesson</span>
          </div>
          <div className="text-gray-500">{s.message_count} total</div>
          {s.has_summary && (
            <span className="inline-block mt-1 bg-green-900 text-green-300 px-2 py-0.5 rounded text-xs">
              ✓ Summary
            </span>
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
