import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { apiJson } from '../api';
import { useFontSize } from '../FontSizeContext';
import type { SessionDetail, Message } from '../types';

export default function SessionDetailPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    if (!sessionId) return;
    apiJson<SessionDetail>(`/api/sessions/${sessionId}`)
      .then(setSession)
      .finally(() => setLoading(false));
  }, [sessionId]);

  if (loading) return <div className="text-gray-400">Loading...</div>;
  if (!session) return <div className="text-red-400">Session not found</div>;

  const lessonMessages = session.messages.filter(m => m.message_type === 'lesson-content');
  const displayMessages = showAll ? session.messages : lessonMessages;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <Link to="/sessions" className="text-indigo-400 hover:text-indigo-300 text-sm">← Sessions</Link>
          <h1 className="text-2xl font-bold mt-1">{session.date}</h1>
          <p className="text-gray-400">{session.start_time}–{session.end_time} · {session.message_count} messages · {session.lesson_content_count} lesson content</p>
        </div>
        <div className="flex gap-2">
          <Link
            to={`/sessions/${sessionId}/summary`}
            className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          >
            View Summary
          </Link>
          <Link
            to={`/sessions/${sessionId}/study`}
            className="bg-green-700 hover:bg-green-600 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          >
            Study Mode
          </Link>
        </div>
      </div>

      {/* Filter toggle */}
      <div className="flex items-center gap-3">
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
      <div className="flex items-center gap-2 mb-1">
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
