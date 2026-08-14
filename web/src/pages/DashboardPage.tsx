import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { apiJson } from '../api';
import { recentOrNewest } from '../sessionMonths';
import type { ReviewStats, Session, SharedLink, Upload } from '../types';

export default function DashboardPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [review, setReview] = useState<ReviewStats | null>(null);
  const [loading, setLoading] = useState(true);
  // Captured when the data loads, not during render: reading the clock while
  // rendering is impure, and the cutoff logically belongs to the fetch anyway.
  const [recentCutoff, setRecentCutoff] = useState('');

  useEffect(() => {
    Promise.all([
      apiJson<Session[]>('/api/sessions').catch(() => []),
      apiJson<Upload[]>('/api/uploads').catch(() => []),
      apiJson<ReviewStats>('/api/review/stats').catch(() => null),
    ]).then(([s, u, r]) => {
      setRecentCutoff(new Date(Date.now() - 14 * 86400000).toISOString().slice(0, 10));
      setSessions(s);
      setUploads(u);
      setReview(r);
    }).finally(() => setLoading(false));
  }, []);

  const { sessions: recentSessions, isFallback } = useMemo(
    () => recentOrNewest(sessions, recentCutoff),
    [sessions, recentCutoff],
  );

  if (loading) return <div className="text-gray-400">Loading...</div>;

  const totalLessons = sessions.filter(s => s.lesson_content_count >= 3).length;
  const summarized = sessions.filter(s => s.has_summary).length;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>

      {/* Daily review — the single entry point. Deliberately above everything
          else: the app only pays off if this gets opened, and it only gets
          opened if starting takes one tap and no decisions. */}
      <ReviewCard stats={review} />

      {/* Stats cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        <StatCard label="Total Sessions" value={sessions.length} />
        <StatCard label="Lessons (3+ content)" value={totalLessons} />
        <StatCard label="Summaries Total" value={summarized} />
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

      {/* Empty state */}
      {sessions.length === 0 && (
        <div className="text-center py-8 space-y-3">
          <p className="text-gray-400">No sessions yet. Upload a chat export to get started.</p>
          <div className="flex gap-3 justify-center">
            <Link to="/upload" className="text-indigo-400 hover:text-indigo-300 text-sm">Upload now</Link>
            <span className="text-gray-600">|</span>
            <Link to="/setup" className="text-indigo-400 hover:text-indigo-300 text-sm">Setup instructions</Link>
          </div>
        </div>
      )}

      {/* Recent sessions */}
      {recentSessions.length > 0 ? (
        <div>
          <p className="text-sm text-gray-500 mb-3">
            {isFallback
              ? `No lessons in the last 2 weeks — showing your ${recentSessions.length} most recent`
              : `${recentSessions.length} session${recentSessions.length !== 1 ? 's' : ''} in the past 2 weeks`}
          </p>
          <div className="space-y-2">
            {recentSessions.map(s => <RecentSessionCard key={s.session_id} session={s} />)}
          </div>
          {isFallback && (
            <p className="text-sm text-gray-600 mt-3">
              Newer lessons in LINE?{' '}
              <Link to="/upload" className="text-indigo-400 hover:text-indigo-300">
                Sync a chat export
              </Link>{' '}
              to bring them in.
            </p>
          )}
        </div>
      ) : (
        sessions.length > 0 && (
          // Every session archived. Say so — a blank panel under a stat card
          // reading "243 Total Sessions" looks like a bug, not a filter.
          <p className="text-sm text-gray-500">
            All {sessions.length} of your sessions are archived.{' '}
            <Link to="/sessions" className="text-indigo-400 hover:text-indigo-300">
              Browse them
            </Link>
            .
          </p>
        )
      )}
    </div>
  );
}

function RecentSessionCard({ session: s }: { session: Session }) {
  const navigate = useNavigate();

  return (
    <div
      onClick={() => navigate(`/sessions/${s.session_id}`)}
      className="bg-gray-900 border border-gray-800 rounded-lg p-4 hover:border-indigo-600 transition-colors cursor-pointer"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:justify-between sm:items-center">
        <div className="min-w-0">
          <span className="font-medium">{s.date}</span>
          <span className="block text-gray-400 text-sm sm:inline sm:ml-2">{s.start_time}–{s.end_time}</span>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm sm:justify-end">
          <span className="text-gray-400">{s.lesson_content_count} lesson msgs</span>
          {s.has_summary && (
            <span className="bg-green-900 text-green-300 px-2 py-0.5 rounded text-xs">Summarized</span>
          )}
          <ConfidenceBadge level={s.boundary_confidence} />
        </div>
      </div>

      {s.shared_links.length > 0 && (
        <SharedLinkPanel link={s.shared_links[0]} compact className="mt-3" />
      )}
    </div>
  );
}

function SharedLinkPanel({ link, compact = false, className = '' }: { link: SharedLink; compact?: boolean; className?: string }) {
  return (
    <div className={`rounded-lg border border-sky-800/60 bg-sky-950/20 p-3 ${className}`.trim()}>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-sky-300">Shared Link</div>
          <div className="text-sm text-gray-300 truncate">{link.label || link.url}</div>
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
        <div className={`mt-2 text-gray-400 ${compact ? 'text-xs' : 'text-sm'}`}>
          {link.before_text && <div>{link.before_text}</div>}
          {link.after_text && <div>{link.after_text}</div>}
        </div>
      )}
    </div>
  );
}

function ReviewCard({ stats }: { stats: ReviewStats | null }) {
  // No stats endpoint (older server) or no material yet — say nothing rather
  // than showing an empty prompt that can't be acted on.
  if (!stats || stats.total_items === 0) return null;

  const nothingDue = stats.due_count === 0;
  const minutes = stats.daily_target <= 8 ? 5 : 10;

  return (
    <div className="bg-gradient-to-br from-indigo-950/70 to-gray-900 border border-indigo-900/60 rounded-xl p-4 sm:p-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold">Daily Review</h2>
            {stats.streak > 0 && (
              <span className="text-sm text-amber-300">🔥 {stats.streak}</span>
            )}
          </div>
          <p className="text-sm text-gray-400 mt-1">
            {nothingDue ? (
              stats.completed_today
                ? 'Done for today. Nice.'
                : 'Nothing due — everything is scheduled ahead.'
            ) : (
              <>
                <span className="text-white font-medium">{stats.due_count}</span> due
                {stats.new_count > 0 && ` · ${stats.new_count} new`}
                {' · about '}{minutes} min
              </>
            )}
          </p>
        </div>

        {!nothingDue && (
          <Link
            to={`/review?minutes=${minutes}`}
            className="shrink-0 text-center px-6 py-3 rounded-lg bg-indigo-600 hover:bg-indigo-500 font-medium transition-colors"
          >
            Start review
          </Link>
        )}
      </div>
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
