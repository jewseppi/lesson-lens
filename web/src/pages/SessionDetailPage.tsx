import { useEffect, useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { apiJson, apiFetch } from '../api';
import { useFontSize } from '../FontSizeContext';
import type { SessionDetail, Message, SharedLink, SessionAttachment, Annotation, AIReview, AIReviewFinding } from '../types';

type Provider = 'openai' | 'anthropic' | 'gemini' | 'ollama' | 'openai_compatible_local';
const STORAGE_KEY = 'lessonlens-provider';
const MODEL_KEY_PREFIX = 'lessonlens-model-';
const ALL_PROVIDERS: Provider[] = ['openai', 'anthropic', 'gemini', 'ollama', 'openai_compatible_local'];

function getStoredProvider(): Provider {
  const value = localStorage.getItem(STORAGE_KEY);
  if (value && ALL_PROVIDERS.includes(value as Provider)) return value as Provider;
  return 'openai';
}

function getStoredModel(provider: Provider): string | undefined {
  return localStorage.getItem(`${MODEL_KEY_PREFIX}${provider}`) || undefined;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button
      onClick={handleCopy}
      title="Copy curl command"
      className="bg-gray-700 hover:bg-gray-600 text-gray-300 px-2 py-1.5 rounded-lg text-sm transition-colors flex-shrink-0"
    >
      {copied ? '\u2713' : '\uD83D\uDCCB'}
    </button>
  );
}

export default function SessionDetailPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [messageFilter, setMessageFilter] = useState<'all' | 'teacher' | 'me'>('all');
  const [attachments, setAttachments] = useState<SessionAttachment[]>([]);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [selectedMsg, setSelectedMsg] = useState<string | null>(null);
  const [review, setReview] = useState<AIReview | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState('');

  const loadAnnotations = useCallback(() => {
    if (!sessionId) return;
    apiJson<Annotation[]>(`/api/sessions/${sessionId}/annotations?target_type=message`)
      .then(setAnnotations)
      .catch(() => {});
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    apiJson<SessionDetail>(`/api/sessions/${sessionId}`)
      .then(data => {
        setSession(data);
        if (data.lesson_content_count === 0) {
          setShowAll(true);
        }
      })
      .finally(() => setLoading(false));
    apiJson<{ attachments: SessionAttachment[] }>(`/api/sessions/${sessionId}/attachments`)
      .then(data => setAttachments(data.attachments))
      .catch(() => {});
    loadAnnotations();
    // Load most recent review
    apiJson<AIReview[]>(`/api/sessions/${sessionId}/reviews`)
      .then(reviews => { if (reviews.length > 0) setReview(reviews[0]); })
      .catch(() => {});
  }, [sessionId, loadAnnotations]);

  const provider = getStoredProvider();
  const model = getStoredModel(provider);

  const triggerReview = async () => {
    if (!sessionId) return;
    setReviewLoading(true);
    setReviewError('');
    try {
      const reviewBody: Record<string, string> = { review_type: 'parse', provider };
      if (model) reviewBody.model = model;
      const res = await apiFetch(`/api/sessions/${sessionId}/review`, {
        method: 'POST',
        body: JSON.stringify(reviewBody),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setReviewError(data.error || 'Review failed');
        return;
      }
      const data = await res.json();
      setReview(data);
    } catch {
      setReviewError('Failed to run review');
    } finally {
      setReviewLoading(false);
    }
  };

  const handleFindingAction = async (findingIdx: number, action: 'accept' | 'dismiss') => {
    if (!review || !sessionId) return;
    try {
      const res = await apiFetch(
        `/api/sessions/${sessionId}/reviews/${review.id}/findings/${findingIdx}/${action}`,
        { method: 'POST' },
      );
      if (res.ok) {
        const data = await res.json();
        setReview(prev => {
          if (!prev) return prev;
          const updated = { ...prev };
          updated.findings = [...updated.findings];
          updated.findings[findingIdx] = data.finding;
          updated.status = data.review_status;
          if (action === 'accept') updated.accepted_count++;
          else updated.dismissed_count++;
          return updated;
        });
        if (action === 'accept') {
          // Reload session to reflect updated classifications
          apiJson<SessionDetail>(`/api/sessions/${sessionId}`).then(setSession);
        }
      }
    } catch { /* ignore */ }
  };

  if (loading) return <div className="text-gray-400">Loading...</div>;
  if (!session) return <div className="text-red-400">Session not found</div>;

  const displayMessages = messageFilter === 'all'
    ? session.messages
    : messageFilter === 'teacher'
      ? session.messages.filter(m => m.speaker_role === 'teacher')
      : session.messages.filter(m => m.speaker_role === 'student');

  const annotationsByMsg = new Map<string, Annotation[]>();
  for (const a of annotations) {
    const list = annotationsByMsg.get(a.target_id) || [];
    list.push(a);
    annotationsByMsg.set(a.target_id, list);
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <Link to="/sessions" className="text-indigo-400 hover:text-indigo-300 text-sm">&larr; Sessions</Link>
          <h1 className="text-2xl font-bold mt-1">{session.date}</h1>
          <p className="text-sm text-gray-400 sm:text-base">{session.start_time}&ndash;{session.end_time} &middot; {session.message_count} messages &middot; {session.lesson_content_count} lesson content</p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:self-start">
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
          <div className="flex gap-1">
            <button
              onClick={triggerReview}
              disabled={reviewLoading}
              className="flex-1 bg-purple-700 hover:bg-purple-600 disabled:opacity-50 text-white px-4 py-3 rounded-lg text-sm font-medium transition-colors text-center"
            >
              {reviewLoading ? 'Reviewing...' : 'AI Review'}
            </button>
            <CopyButton text={(() => {
              const token = localStorage.getItem('lessonlens-token') || '<TOKEN>';
              const body: Record<string, string> = { review_type: 'parse', provider };
              if (model) body.model = model;
              return `curl -X POST http://localhost:5001/api/sessions/${sessionId}/review \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer ${token}" \\\n  -d '${JSON.stringify(body)}'`;
            })()} />
          </div>
        </div>
      </div>

      {session.shared_links.length > 0 && (
        <SharedLinkPanel link={session.shared_links[0]} />
      )}

      {attachments.length > 0 && (
        <AttachmentPanel attachments={attachments} onRemove={(attId) => {
          apiFetch(`/api/sessions/${sessionId}/attachments/${attId}`, { method: 'DELETE' })
            .then(res => { if (res.ok) setAttachments(prev => prev.filter(a => a.attachment_id !== attId)); });
        }} />
      )}

      {/* Filter toggle */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
        <div className="inline-flex rounded-lg overflow-hidden border border-gray-700">
          {(['all', 'teacher', 'me'] as const).map((filter) => (
            <button
              key={filter}
              onClick={() => setMessageFilter(filter)}
              className={`text-sm px-3 py-1 transition-colors ${
                messageFilter === filter
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white'
              }`}
            >
              {filter === 'all' ? 'All' : filter === 'teacher' ? 'Teacher' : 'Me'}
            </button>
          ))}
        </div>
        <span className="text-sm text-gray-500">
          Showing {displayMessages.length} of {session.messages.length} messages
        </span>
        {annotations.length > 0 && (
          <span className="text-sm text-amber-400">{annotations.length} annotation{annotations.length !== 1 ? 's' : ''}</span>
        )}
      </div>

      {/* AI Review Error */}
      {reviewError && (
        <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">
          {reviewError}
        </div>
      )}

      {/* AI Review Findings */}
      {review && review.findings.length > 0 && (
        <ReviewFindingsPanel
          review={review}
          onAction={handleFindingAction}
        />
      )}

      {/* Messages */}
      <div className="space-y-2">
        {displayMessages.map(msg => (
          <MessageBubble
            key={msg.message_id}
            message={msg}
            sessionId={sessionId!}
            annotations={annotationsByMsg.get(msg.message_id) || []}
            isSelected={selectedMsg === msg.message_id}
            onSelect={() => setSelectedMsg(selectedMsg === msg.message_id ? null : msg.message_id)}
            onAnnotationChange={loadAnnotations}
          />
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

function MessageBubble({ message: m, sessionId, annotations, isSelected, onSelect, onAnnotationChange }: {
  message: Message;
  sessionId: string;
  annotations: Annotation[];
  isSelected: boolean;
  onSelect: () => void;
  onAnnotationChange: () => void;
}) {
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
    <div>
      <div
        onClick={onSelect}
        className={`bg-gray-900 border border-l-4 rounded-lg p-3 cursor-pointer transition-colors ${
          typeColors[m.message_type] || 'border-l-gray-700'
        } ${isSelected ? 'border-indigo-500 bg-gray-800' : 'border-gray-800 hover:border-gray-700'}`}
      >
        <div className="flex flex-wrap items-center gap-2 mb-1">
          <span className={`text-xs font-medium ${isTeacher ? 'text-cyan-400' : 'text-orange-400'}`}>
            {isTeacher ? '\uD83D\uDC69\u200D\uD83C\uDFEB Teacher' : '\uD83C\uDF93 Student'}
          </span>
          <span className="text-xs text-gray-600">{m.time}</span>
          <span className="text-xs text-gray-700">{m.message_id}</span>
          {m.tags.length > 0 && m.tags.map(t => (
            <span key={t} className="text-xs bg-gray-800 text-gray-400 px-1.5 py-0.5 rounded">{t}</span>
          ))}
          {annotations.length > 0 && (
            <span className="text-xs bg-amber-900/60 text-amber-400 px-1.5 py-0.5 rounded">
              {annotations.length} note{annotations.length !== 1 ? 's' : ''}
            </span>
          )}
        </div>
        <div className={`text-gray-200 whitespace-pre-wrap ${isLesson ? zhClass : ''}`}>{m.text_raw}</div>
      </div>

      {/* Existing annotations */}
      {isSelected && annotations.length > 0 && (
        <div className="ml-4 mt-1 space-y-1">
          {annotations.map(a => (
            <AnnotationCard key={a.id} annotation={a} sessionId={sessionId} onDelete={onAnnotationChange} />
          ))}
        </div>
      )}

      {/* Annotation form */}
      {isSelected && (
        <AnnotationForm sessionId={sessionId} targetId={m.message_id} onCreated={onAnnotationChange} />
      )}
    </div>
  );
}

function AnnotationCard({ annotation: a, sessionId, onDelete }: {
  annotation: Annotation;
  sessionId: string;
  onDelete: () => void;
}) {
  const typeLabels: Record<string, string> = {
    correction: 'Correction',
    note: 'Note',
    flag: 'Flag',
    reclassify: 'Reclassify',
  };
  const typeColors: Record<string, string> = {
    correction: 'border-l-red-500',
    note: 'border-l-blue-500',
    flag: 'border-l-amber-500',
    reclassify: 'border-l-purple-500',
  };

  const content = a.content as Record<string, string>;
  const displayText = content.text || content.corrected || content.detail ||
    (content.corrected_type ? `${content.original_type} → ${content.corrected_type}` : '') ||
    JSON.stringify(content);

  return (
    <div className={`bg-gray-900/50 border border-gray-800 border-l-4 ${typeColors[a.annotation_type] || 'border-l-gray-600'} rounded p-2 flex items-start gap-2`}>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-medium text-gray-500 uppercase">{typeLabels[a.annotation_type] || a.annotation_type}</span>
          {a.created_by_role && (
            <span className="text-[10px] text-gray-600">{a.created_by_role}</span>
          )}
        </div>
        <div className="text-xs text-gray-300 mt-0.5">{displayText}</div>
      </div>
      <button
        onClick={() => {
          apiFetch(`/api/sessions/${sessionId}/annotations/${a.id}`, { method: 'DELETE' })
            .then(res => { if (res.ok) onDelete(); });
        }}
        className="text-gray-600 hover:text-red-400 text-xs flex-shrink-0 p-1"
        title="Remove"
      >
        &times;
      </button>
    </div>
  );
}

function AnnotationForm({ sessionId, targetId, onCreated }: {
  sessionId: string;
  targetId: string;
  onCreated: () => void;
}) {
  const [type, setType] = useState<string>('note');
  const [text, setText] = useState('');
  const [saving, setSaving] = useState(false);

  const handleSubmit = () => {
    if (!text.trim()) return;
    setSaving(true);
    const content: Record<string, string> = {};
    if (type === 'note') content.text = text;
    else if (type === 'flag') content.detail = text;
    else if (type === 'correction') { content.corrected = text; }
    else if (type === 'reclassify') content.corrected_type = text;

    apiFetch(`/api/sessions/${sessionId}/annotations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_type: 'message',
        target_id: targetId,
        annotation_type: type,
        content,
      }),
    })
      .then(res => {
        if (res.ok) {
          setText('');
          onCreated();
        }
      })
      .finally(() => setSaving(false));
  };

  return (
    <div className="ml-4 mt-1 bg-gray-900/30 border border-gray-800 rounded p-2 space-y-2">
      <div className="flex gap-1 flex-wrap">
        {['note', 'flag', 'correction', 'reclassify'].map(t => (
          <button
            key={t}
            onClick={() => setType(t)}
            className={`text-xs px-2 py-1 rounded transition-colors ${
              type === t ? 'bg-indigo-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-white'
            }`}
          >
            {t === 'note' ? 'Add Note' : t === 'flag' ? 'Flag Issue' : t === 'correction' ? 'Correct' : 'Reclassify'}
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleSubmit(); }}
          placeholder={type === 'reclassify' ? 'lesson-content / logistics / other...' : 'Your annotation...'}
          className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-indigo-500 min-w-0"
        />
        <button
          onClick={handleSubmit}
          disabled={saving || !text.trim()}
          className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white px-3 py-1.5 rounded text-sm font-medium flex-shrink-0"
        >
          {saving ? '...' : 'Save'}
        </button>
      </div>
    </div>
  );
}

function ReviewFindingsPanel({ review, onAction }: {
  review: AIReview;
  onAction: (idx: number, action: 'accept' | 'dismiss') => void;
}) {
  const pendingCount = review.findings.filter(f => f.status === 'pending').length;

  return (
    <div className="rounded-xl border border-purple-800/60 bg-purple-950/20 p-4 space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-purple-300">
            AI Parse Review
          </div>
          <div className="text-xs text-gray-400 mt-0.5">
            {review.provider}/{review.model} &middot; {review.findings_count} finding{review.findings_count !== 1 ? 's' : ''}
            {pendingCount > 0 && ` &middot; ${pendingCount} pending`}
            {review.accepted_count > 0 && ` &middot; ${review.accepted_count} accepted`}
            {review.dismissed_count > 0 && ` &middot; ${review.dismissed_count} dismissed`}
          </div>
        </div>
        <span className={`text-xs px-2 py-1 rounded ${
          review.status === 'completed' ? 'bg-green-900/50 text-green-400'
            : review.status === 'reviewed' ? 'bg-yellow-900/50 text-yellow-400'
            : 'bg-purple-900/50 text-purple-400'
        }`}>
          {review.status}
        </span>
      </div>

      <div className="space-y-2">
        {review.findings.map((f, idx) => (
          <FindingCard key={idx} finding={f} index={idx} onAction={onAction} />
        ))}
      </div>
    </div>
  );
}

function FindingCard({ finding: f, index, onAction }: {
  finding: AIReviewFinding;
  index: number;
  onAction: (idx: number, action: 'accept' | 'dismiss') => void;
}) {
  const confidencePct = Math.round(f.confidence * 100);
  const confidenceColor = f.confidence >= 0.8 ? 'text-green-400'
    : f.confidence >= 0.6 ? 'text-yellow-400' : 'text-orange-400';

  return (
    <div className={`bg-gray-900 border rounded-lg p-3 ${
      f.status === 'accepted' ? 'border-green-700/50 opacity-70'
        : f.status === 'dismissed' ? 'border-gray-800 opacity-50'
        : 'border-purple-800/40'
    }`}>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <span className="text-xs text-gray-500 font-mono">{f.message_id}</span>
            <span className={`text-xs font-medium ${confidenceColor}`}>{confidencePct}%</span>
            {f.status !== 'pending' && (
              <span className={`text-xs px-1.5 py-0.5 rounded ${
                f.status === 'accepted' ? 'bg-green-900/50 text-green-400' : 'bg-gray-700 text-gray-400'
              }`}>
                {f.status}
              </span>
            )}
          </div>
          <div className="text-sm text-gray-300">
            {f.current_type && f.suggested_type && (
              <span>
                <span className="text-red-400 line-through">{f.current_type}</span>
                {' \u2192 '}
                <span className="text-green-400">{f.suggested_type}</span>
              </span>
            )}
            {f.suggested_role && (
              <span className="ml-2 text-xs text-gray-400">
                (role: {f.current_role} \u2192 {f.suggested_role})
              </span>
            )}
          </div>
          <div className="text-xs text-gray-400 mt-1">{f.reason}</div>
        </div>
        {f.status === 'pending' && (
          <div className="flex gap-2 shrink-0">
            <button
              onClick={() => onAction(index, 'accept')}
              className="px-3 py-1 bg-green-700 hover:bg-green-600 text-white text-xs rounded transition-colors"
            >
              Accept
            </button>
            <button
              onClick={() => onAction(index, 'dismiss')}
              className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-white text-xs rounded transition-colors"
            >
              Dismiss
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

const confidenceColors: Record<string, string> = {
  high: 'bg-green-900/50 text-green-400 border-green-700',
  medium: 'bg-yellow-900/50 text-yellow-400 border-yellow-700',
  low: 'bg-orange-900/50 text-orange-400 border-orange-700',
  unmatched: 'bg-gray-800 text-gray-400 border-gray-700',
};

function AttachmentPanel({ attachments, onRemove }: {
  attachments: SessionAttachment[];
  onRemove: (attachmentId: number) => void;
}) {
  const API_BASE = import.meta.env.VITE_API_BASE ?? '';

  return (
    <div className="rounded-xl border border-emerald-800/60 bg-emerald-950/20 p-4 space-y-3">
      <div className="text-xs font-semibold uppercase tracking-wide text-emerald-300">
        Lesson Photos ({attachments.length})
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
              &times;
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
