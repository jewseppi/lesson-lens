import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { apiJson, apiFetch } from '../api';
import { useFontSize } from '../FontSizeContext';
import type { SessionDetail, Message, SharedLink, SessionAttachment } from '../types';

export default function SessionDetailPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const [attachments, setAttachments] = useState<SessionAttachment[]>([]);

  useEffect(() => {
    if (!sessionId) return;
    apiJson<SessionDetail>(`/api/sessions/${sessionId}`)
      .then(data => {
        setSession(data);
        if (data.lesson_content_count === 0 && data.shared_links.length > 0) {
          setShowAll(true);
        }
      })
      .finally(() => setLoading(false));
    apiJson<{ attachments: SessionAttachment[] }>(`/api/sessions/${sessionId}/attachments`)
      .then(data => setAttachments(data.attachments))
      .catch(() => {});
  }, [sessionId]);

  if (loading) return <div className="text-gray-400">Loading...</div>;
  if (!session) return <div className="text-red-400">Session not found</div>;

  const lessonMessages = session.messages.filter(m => m.message_type === 'lesson-content');
  const displayMessages = showAll ? session.messages : lessonMessages;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <Link to="/sessions" className="text-indigo-400 hover:text-indigo-300 text-sm">← Sessions</Link>
          <h1 className="text-2xl font-bold mt-1">{session.date}</h1>
          <p className="text-sm text-gray-400 sm:text-base">{session.start_time}–{session.end_time} · {session.message_count} messages · {session.lesson_content_count} lesson content</p>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 sm:self-start">
          <Link
            to={`/sessions/${sessionId}/summary`}
            className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-3 rounded-lg text-sm font-medium transition-colors text-center"
          >
            View Summary
          </Link>
          <Link
            to={`/sessions/${sessionId}/study`}
            className="bg-green-700 hover:bg-green-600 text-white px-4 py-3 rounded-lg text-sm font-medium transition-colors text-center"
          >
            Study Mode
          </Link>
        </div>
      </div>

      {session.shared_links.length > 0 && (
        <SharedLinkPanel link={session.shared_links[0]} />
      )}

      {attachments.length > 0 && (
        <AttachmentPanel attachments={attachments} sessionId={sessionId!} onRemove={(attId) => {
          apiFetch(`/api/sessions/${sessionId}/attachments/${attId}`, { method: 'DELETE' })
            .then(res => { if (res.ok) setAttachments(prev => prev.filter(a => a.attachment_id !== attId)); });
        }} />
      )}

      {/* Filter toggle */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
        <button
          onClick={() => setShowAll(!showAll)}
          className={`text-sm px-3 py-1 rounded-lg transition-colors ${
            showAll ? 'bg-gray-700 text-white' : 'bg-indigo-600 text-white'
          }`}
        >
          {showAll ? 'Show All' : 'Lesson Content Only'}
        </button>
        <span className="text-sm text-gray-500">
          Showing {displayMessages.length} of {session.messages.length} messages
        </span>
      </div>

      {/* Messages */}
      <div className="space-y-2">
        {displayMessages.map(msg => (
          <MessageBubble key={msg.message_id} message={msg} />
        ))}
      </div>
    </div>
  );
}

function SharedLinkPanel({ link }: { link: SharedLink }) {
  return (
    <div className="rounded-xl border border-sky-800/60 bg-sky-950/20 p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-sky-300">Shared Link</div>
          <div className="mt-1 break-all text-sm text-gray-300">{link.label || link.url}</div>
          {(link.before_text || link.after_text) && (
            <div className="mt-2 space-y-1 text-sm text-gray-400">
              {link.before_text && <div>{link.before_text}</div>}
              {link.after_text && <div>{link.after_text}</div>}
            </div>
          )}
        </div>
        <a
          href={link.url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center justify-center rounded-lg bg-sky-700 px-4 py-3 text-sm font-medium text-white hover:bg-sky-600"
        >
          Open Shared Link
        </a>
      </div>
    </div>
  );
}

function MessageBubble({ message: m }: { message: Message }) {
  const { zhClass } = useFontSize();
  const isTeacher = m.speaker_role === 'teacher';
  const isLesson = m.message_type === 'lesson-content';
  const typeColors: Record<string, string> = {
    'lesson-content': 'border-l-indigo-500',
    'logistics': 'border-l-yellow-600',
    'media-reference': 'border-l-gray-600',
    'link': 'border-l-blue-500',
    'other': 'border-l-gray-700',
  };

  return (
    <div className={`bg-gray-900 border border-gray-800 border-l-4 ${typeColors[m.message_type] || 'border-l-gray-700'} rounded-lg p-3`}>
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <span className={`text-xs font-medium ${isTeacher ? 'text-cyan-400' : 'text-orange-400'}`}>
          {isTeacher ? '👩‍🏫 Teacher' : '🎓 Student'}
        </span>
        <span className="text-xs text-gray-600">{m.time}</span>
        <span className="text-xs text-gray-700">{m.message_id}</span>
        {m.tags.length > 0 && m.tags.map(t => (
          <span key={t} className="text-xs bg-gray-800 text-gray-400 px-1.5 py-0.5 rounded">{t}</span>
        ))}
      </div>
      <div className={`text-gray-200 whitespace-pre-wrap ${isLesson ? zhClass : ''}`}>{m.text_raw}</div>
    </div>
  );
}

const confidenceColors: Record<string, string> = {
  high: 'bg-green-900/50 text-green-400 border-green-700',
  medium: 'bg-yellow-900/50 text-yellow-400 border-yellow-700',
  low: 'bg-orange-900/50 text-orange-400 border-orange-700',
  unmatched: 'bg-gray-800 text-gray-400 border-gray-700',
};

function AttachmentPanel({ attachments, sessionId, onRemove }: {
  attachments: SessionAttachment[];
  sessionId: string;
  onRemove: (attachmentId: number) => void;
}) {
  const API_BASE = import.meta.env.VITE_API_BASE ?? '';

  return (
    <div className="rounded-xl border border-emerald-800/60 bg-emerald-950/20 p-4 space-y-3">
      <div className="text-xs font-semibold uppercase tracking-wide text-emerald-300">
        📷 Lesson Photos ({attachments.length})
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
        {attachments.map(att => (
          <div key={att.attachment_id} className="relative group">
            <img
              src={`${API_BASE}/api/attachments/${att.attachment_id}/image`}
              alt={att.original_filename}
              className="w-full h-32 object-cover rounded-lg border border-gray-700"
              loading="lazy"
            />
            <div className="mt-1 flex items-center gap-1">
              <span className={`text-[10px] px-1.5 py-0.5 rounded border ${confidenceColors[att.match_confidence]}`}>
                {att.match_confidence}
              </span>
              {att.assigned_by === 'manual' && (
                <span className="text-[10px] text-gray-500">manual</span>
              )}
            </div>
            <p className="text-[10px] text-gray-500 truncate">{att.original_filename}</p>
            <button
              onClick={() => onRemove(att.attachment_id)}
              className="absolute top-1 right-1 hidden group-hover:block bg-black/70 text-red-400 text-xs rounded px-1.5 py-0.5 hover:text-red-300"
              title="Remove from session"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
