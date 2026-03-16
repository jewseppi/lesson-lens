import { useEffect, useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { apiJson, apiFetch, trackEvent } from '../api';
import { useFontSize } from '../FontSizeContext';
import type { LessonSummary, Annotation, AIReview } from '../types';

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

function buildCurlCommand(endpoint: string, sessionId: string, body: Record<string, string | undefined>): string {
  const token = localStorage.getItem('lessonlens-token') || '<TOKEN>';
  const cleanBody: Record<string, string> = {};
  for (const [k, v] of Object.entries(body)) { if (v) cleanBody[k] = v; }
  return `curl -X POST http://localhost:5001/api/sessions/${sessionId}/${endpoint} \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer ${token}" \\\n  -d '${JSON.stringify(cleanBody)}'`;
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

function PronunciationNote({ note }: { note?: string }) {
  if (!note) return null;

  return (
    <span className="pronunciation-note" title={note} aria-label={note}>
      Tone note
    </span>
  );
}

export default function SummaryPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [summary, setSummary] = useState<LessonSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [noSummary, setNoSummary] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState('');
  const [policyWarning, setPolicyWarning] = useState('');
  const [provider] = useState<Provider>(getStoredProvider);
  const model = getStoredModel(provider);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [selectedItem, setSelectedItem] = useState<string | null>(null);
  const [summaryReview, setSummaryReview] = useState<AIReview | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState('');

  const loadAnnotations = useCallback(() => {
    if (!sessionId) return;
    apiJson<Annotation[]>(`/api/sessions/${sessionId}/annotations?target_type=summary_item`)
      .then(setAnnotations)
      .catch(() => {});
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    apiJson<LessonSummary>(`/api/sessions/${sessionId}/summary`)
      .then(data => {
        setSummary(data);
        trackEvent('view_summary', { session_id: sessionId });
      })
      .catch(() => setNoSummary(true))
      .finally(() => setLoading(false));
    loadAnnotations();
    // Load most recent summary review
    apiJson<AIReview[]>(`/api/sessions/${sessionId}/reviews`)
      .then(reviews => {
        const summaryReview = reviews.find(r => r.review_type === 'summary');
        if (summaryReview) setSummaryReview(summaryReview);
      })
      .catch(() => {});
  }, [sessionId, loadAnnotations]);

  const triggerSummaryReview = async () => {
    if (!sessionId) return;
    setReviewLoading(true);
    setReviewError('');
    try {
      const reviewBody: Record<string, string> = { review_type: 'summary', provider };
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
      setSummaryReview(data);
    } catch {
      setReviewError('Failed to run review');
    } finally {
      setReviewLoading(false);
    }
  };

  const handleReviewAction = async (findingIdx: number, action: 'accept' | 'dismiss') => {
    if (!summaryReview || !sessionId) return;
    try {
      const res = await apiFetch(
        `/api/sessions/${sessionId}/reviews/${summaryReview.id}/findings/${findingIdx}/${action}`,
        { method: 'POST' },
      );
      if (res.ok) {
        const data = await res.json();
        setSummaryReview(prev => {
          if (!prev) return prev;
          const updated = { ...prev };
          updated.findings = [...updated.findings];
          updated.findings[findingIdx] = data.finding;
          updated.status = data.review_status;
          if (action === 'accept') updated.accepted_count++;
          else updated.dismissed_count++;
          return updated;
        });
      }
    } catch { /* ignore */ }
  };

  const handleGenerate = async () => {
    if (!sessionId) return;
    setGenerating(true);
    setGenError('');
    try {
      const body: Record<string, string> = { provider };
      if (model) body.model = model;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const data = await apiJson<any>(`/api/sessions/${sessionId}/generate`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      if (data._policy_warning) {
        setPolicyWarning(data._policy_warning);
        delete data._policy_warning;
      }
      setSummary(data as LessonSummary);
      setNoSummary(false);
      trackEvent('generate_summary', { session_id: sessionId, provider });
    } catch (err) {
      setGenError(err instanceof Error ? err.message : 'Generation failed');
    } finally {
      setGenerating(false);
    }
  };

  const { zhClass } = useFontSize();

  // Build annotation lookup: target_id -> annotations[]
  const annotationsByTarget = new Map<string, Annotation[]>();
  for (const a of annotations) {
    const list = annotationsByTarget.get(a.target_id) || [];
    list.push(a);
    annotationsByTarget.set(a.target_id, list);
  }

  if (loading) return <div className="text-gray-400">Loading summary...</div>;

  if (noSummary && !summary) {
    return (
      <div className="max-w-lg mx-auto text-center py-12 space-y-6">
        <div className="text-5xl">{'\uD83D\uDCDD'}</div>
        <p className="text-gray-300 text-lg">No summary generated yet for this session.</p>

        <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-4 text-left">
          <h3 className="font-semibold text-white">Generate with AI</h3>
          <p className="text-sm text-gray-400">
            This will call the LLM to summarize the lesson, extract vocabulary,
            key sentences, corrections, and create study exercises.
          </p>

          <p className="text-xs text-gray-500">
            Provider: <span className="text-gray-300">{provider}</span>
            {model && <>, Model: <span className="text-gray-300">{model}</span></>}
            . Change in Settings.
          </p>

          {genError && (
            <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3 space-y-1">
              <div>{genError}</div>
              <LocalErrorHint error={genError} provider={provider} model={model || ''} />
            </div>
          )}

          <div className="flex gap-2">
            <button
              onClick={handleGenerate}
              disabled={generating}
              className="flex-1 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white py-3 rounded-lg font-medium transition-colors"
            >
              {generating ? '\uD83D\uDD04 Generating summary...' : '\uD83D\uDE80 Generate Summary'}
            </button>
            <CopyButton text={buildCurlCommand('generate', sessionId!, { provider, model })} />
          </div>

          {generating && (
            <p className="text-xs text-gray-500 text-center">
              This may take 15-30 seconds (2 LLM passes: summary + exercises).
            </p>
          )}
        </div>

        <Link to={`/sessions/${sessionId}`} className="text-indigo-400 hover:text-indigo-300 text-sm">
          &larr; Back to session
        </Link>
      </div>
    );
  }
  if (!summary) return null;
  const hasVocabularyZhuyin = summary.vocabulary.some(item => Boolean(item.zhuyin));

  return (
    <div className="max-w-4xl mx-auto space-y-6 sm:space-y-8">
      {policyWarning && (
        <div className="bg-yellow-900/40 border border-yellow-700/60 text-yellow-200 text-sm rounded-lg p-3 flex items-start gap-2">
          <span className="flex-shrink-0">⚠️</span>
          <div>
            <div className="font-medium">Quality Warning</div>
            <div className="text-yellow-300/80 text-xs mt-0.5">{policyWarning}</div>
          </div>
        </div>
      )}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <Link to={`/sessions/${sessionId}`} className="text-indigo-400 hover:text-indigo-300 text-sm">&larr; Session</Link>
          <h1 className="text-2xl font-bold mt-1">{summary.title}</h1>
          <p className="text-gray-400">{summary.lesson_date}</p>
        </div>
        <div className="flex flex-wrap gap-2 items-center">
          {annotations.length > 0 && (
            <span className="text-sm text-amber-400 bg-amber-900/30 px-2 py-1 rounded">
              {annotations.length} annotation{annotations.length !== 1 ? 's' : ''}
            </span>
          )}
          <button
            onClick={triggerSummaryReview}
            disabled={reviewLoading}
            className="bg-purple-700 hover:bg-purple-600 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
          >
            {reviewLoading ? 'Reviewing...' : 'AI Review'}
          </button>
          <CopyButton text={buildCurlCommand('review', sessionId!, { review_type: 'summary', provider, model })} />
        </div>
      </div>

      {reviewError && (
        <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">
          {reviewError}
        </div>
      )}

      {summaryReview && summaryReview.findings.length > 0 && (
        <SummaryReviewPanel review={summaryReview} onAction={handleReviewAction} />
      )}

      {/* Overview */}
      <section>
        <h2 className="text-lg font-semibold mb-2 text-indigo-400">Overview</h2>
        <p className="text-gray-300">{summary.summary.overview}</p>
      </section>

      {/* Key Sentences */}
      {summary.key_sentences.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3 text-indigo-400">Key Sentences</h2>
          <div className="space-y-3">
            {summary.key_sentences.map(ks => {
              const itemAnns = annotationsByTarget.get(ks.id) || [];
              const isActive = selectedItem === ks.id;
              return (
                <div key={ks.id}>
                  <div
                    onClick={() => setSelectedItem(isActive ? null : ks.id)}
                    className={`bg-gray-900 border rounded-lg p-4 cursor-pointer transition-colors ${
                      isActive ? 'border-indigo-500 bg-gray-800' : 'border-gray-800 hover:border-gray-700'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className={zhClass}>{ks.zh}</div>
                        <div className="mt-1 flex flex-wrap items-center gap-2">
                          <div className="pinyin-text">{ks.pinyin}</div>
                          <PronunciationNote note={ks.pronunciation_note} />
                        </div>
                        {ks.zhuyin && <div className="zhuyin-text mt-1">{ks.zhuyin}</div>}
                        <div className="text-gray-300 mt-1">{ks.en}</div>
                        {ks.context_note && <div className="text-gray-500 text-sm mt-1 italic">{ks.context_note}</div>}
                      </div>
                      {itemAnns.length > 0 && (
                        <span className="text-xs bg-amber-900/60 text-amber-400 px-1.5 py-0.5 rounded flex-shrink-0">
                          {itemAnns.length} note{itemAnns.length !== 1 ? 's' : ''}
                        </span>
                      )}
                    </div>
                  </div>
                  {isActive && itemAnns.length > 0 && (
                    <div className="ml-4 mt-1 space-y-1">
                      {itemAnns.map(a => (
                        <SummaryAnnotationCard
                          key={a.id}
                          annotation={a}
                          sessionId={sessionId!}
                          onDelete={loadAnnotations}
                        />
                      ))}
                    </div>
                  )}
                  {isActive && (
                    <SummaryAnnotationForm
                      sessionId={sessionId!}
                      targetId={ks.id}
                      targetSection="key_sentences"
                      onCreated={loadAnnotations}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Vocabulary */}
      {summary.vocabulary.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3 text-indigo-400">Vocabulary</h2>
          {/* Mobile cards */}
          <div className="md:hidden space-y-3">
            {summary.vocabulary.map((v, i) => {
              const vocabId = `vocab-${i}`;
              const itemAnns = annotationsByTarget.get(vocabId) || [];
              const isActive = selectedItem === vocabId;
              return (
                <div key={`${v.term_zh}-${i}-mobile`}>
                  <div
                    onClick={() => setSelectedItem(isActive ? null : vocabId)}
                    className={`bg-gray-900 border rounded-lg p-4 space-y-2 cursor-pointer transition-colors ${
                      isActive ? 'border-indigo-500 bg-gray-800' : 'border-gray-800 hover:border-gray-700'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className={`${zhClass} text-base`}>{v.term_zh}</div>
                        <div className="pinyin-text mt-1">{v.pinyin}</div>
                        {v.zhuyin && <div className="zhuyin-text mt-1">{v.zhuyin}</div>}
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        <PronunciationNote note={v.pronunciation_note} />
                        {itemAnns.length > 0 && (
                          <span className="text-xs bg-amber-900/60 text-amber-400 px-1.5 py-0.5 rounded">
                            {itemAnns.length}
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="text-gray-300">{v.en}</div>
                    <div className="text-xs uppercase tracking-wide text-gray-500">{v.pos_or_type}</div>
                  </div>
                  {isActive && itemAnns.length > 0 && (
                    <div className="ml-4 mt-1 space-y-1">
                      {itemAnns.map(a => (
                        <SummaryAnnotationCard key={a.id} annotation={a} sessionId={sessionId!} onDelete={loadAnnotations} />
                      ))}
                    </div>
                  )}
                  {isActive && (
                    <SummaryAnnotationForm
                      sessionId={sessionId!}
                      targetId={vocabId}
                      targetSection="vocabulary"
                      onCreated={loadAnnotations}
                    />
                  )}
                </div>
              );
            })}
          </div>

          {/* Desktop table */}
          <div className="hidden md:block overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-500 border-b border-gray-800">
                  <th className="pb-2 pr-4">Term</th>
                  <th className="pb-2 pr-4">Pinyin</th>
                  {hasVocabularyZhuyin && <th className="pb-2 pr-4">Zhuyin</th>}
                  <th className="pb-2 pr-4">English</th>
                  <th className="pb-2 pr-2">Type</th>
                  <th className="pb-2"></th>
                </tr>
              </thead>
              <tbody>
                {summary.vocabulary.map((v, i) => {
                  const vocabId = `vocab-${i}`;
                  const itemAnns = annotationsByTarget.get(vocabId) || [];
                  const isActive = selectedItem === vocabId;
                  return (
                    <>
                      <tr
                        key={i}
                        onClick={() => setSelectedItem(isActive ? null : vocabId)}
                        className={`border-b border-gray-800/50 cursor-pointer transition-colors ${
                          isActive ? 'bg-gray-800' : 'hover:bg-gray-900'
                        }`}
                      >
                        <td className="py-2 pr-4 font-medium"><span className={zhClass}>{v.term_zh}</span></td>
                        <td className="py-2 pr-4 text-gray-400 italic">
                          <div className="flex flex-wrap items-center gap-2">
                            <span>{v.pinyin}</span>
                            <PronunciationNote note={v.pronunciation_note} />
                          </div>
                        </td>
                        {hasVocabularyZhuyin && <td className="py-2 pr-4 text-sky-300">{v.zhuyin || '\u2014'}</td>}
                        <td className="py-2 pr-4 text-gray-300">{v.en}</td>
                        <td className="py-2 pr-2 text-gray-500">{v.pos_or_type}</td>
                        <td className="py-2">
                          {itemAnns.length > 0 && (
                            <span className="text-xs bg-amber-900/60 text-amber-400 px-1.5 py-0.5 rounded">
                              {itemAnns.length}
                            </span>
                          )}
                        </td>
                      </tr>
                      {isActive && (
                        <tr key={`${i}-annotation`}>
                          <td colSpan={hasVocabularyZhuyin ? 6 : 5} className="pb-2">
                            {itemAnns.length > 0 && (
                              <div className="ml-4 mt-1 space-y-1">
                                {itemAnns.map(a => (
                                  <SummaryAnnotationCard key={a.id} annotation={a} sessionId={sessionId!} onDelete={loadAnnotations} />
                                ))}
                              </div>
                            )}
                            <SummaryAnnotationForm
                              sessionId={sessionId!}
                              targetId={vocabId}
                              targetSection="vocabulary"
                              onCreated={loadAnnotations}
                            />
                          </td>
                        </tr>
                      )}
                    </>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="mt-4 space-y-3">
            {summary.vocabulary.map((v, i) => (
              (v.example_pinyin || v.example_zhuyin) ? (
                <div key={`${v.term_zh}-${i}-example`} className="bg-gray-900/40 border border-gray-800 rounded-lg p-3">
                  <div className={`${zhClass} text-base`}>{v.example_zh}</div>
                  {v.example_pinyin && <div className="pinyin-text mt-1">{v.example_pinyin}</div>}
                  {v.example_zhuyin && <div className="zhuyin-text mt-1">{v.example_zhuyin}</div>}
                  <div className="text-gray-400 text-sm mt-1">{v.example_en}</div>
                </div>
              ) : null
            ))}
          </div>
        </section>
      )}

      {/* Corrections */}
      {summary.corrections.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3 text-indigo-400">Teacher Corrections</h2>
          <div className="space-y-3">
            {summary.corrections.map(c => {
              const itemAnns = annotationsByTarget.get(c.id) || [];
              const isActive = selectedItem === c.id;
              return (
                <div key={c.id}>
                  <div
                    onClick={() => setSelectedItem(isActive ? null : c.id)}
                    className={`bg-gray-900 border rounded-lg p-4 cursor-pointer transition-colors ${
                      isActive ? 'border-indigo-500 bg-gray-800' : 'border-gray-800 hover:border-gray-700'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="text-red-400 line-through">{c.learner_original}</div>
                        <div className="text-green-400 font-medium mt-1">&rarr; {c.teacher_correction}</div>
                        <div className="text-gray-400 text-sm mt-1">{c.reason}</div>
                      </div>
                      {itemAnns.length > 0 && (
                        <span className="text-xs bg-amber-900/60 text-amber-400 px-1.5 py-0.5 rounded flex-shrink-0">
                          {itemAnns.length} note{itemAnns.length !== 1 ? 's' : ''}
                        </span>
                      )}
                    </div>
                  </div>
                  {isActive && itemAnns.length > 0 && (
                    <div className="ml-4 mt-1 space-y-1">
                      {itemAnns.map(a => (
                        <SummaryAnnotationCard key={a.id} annotation={a} sessionId={sessionId!} onDelete={loadAnnotations} />
                      ))}
                    </div>
                  )}
                  {isActive && (
                    <SummaryAnnotationForm
                      sessionId={sessionId!}
                      targetId={c.id}
                      targetSection="corrections"
                      onCreated={loadAnnotations}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Usage Notes */}
      {summary.summary.usage_notes && (
        <section>
          <h2 className="text-lg font-semibold mb-2 text-indigo-400">Usage Notes</h2>
          <p className="text-gray-300">{summary.summary.usage_notes}</p>
        </section>
      )}

      {/* Recap */}
      {summary.summary.short_recap && (
        <section className="bg-indigo-950/30 border border-indigo-800 rounded-lg p-4">
          <h2 className="text-sm font-semibold text-indigo-400 mb-1">Quick Recap</h2>
          <p className="text-gray-300">{summary.summary.short_recap}</p>
        </section>
      )}

      {/* Study link */}
      <div className="flex gap-3 flex-wrap">
        <Link
          to={`/sessions/${sessionId}/study`}
          className="bg-green-700 hover:bg-green-600 text-white px-6 py-3 rounded-lg font-medium transition-colors"
        >
          {'\uD83C\uDFAF'} Study This Lesson
        </Link>
      </div>
    </div>
  );
}

// --- Annotation components for summary items ---

function SummaryAnnotationCard({ annotation: a, sessionId, onDelete }: {
  annotation: Annotation;
  sessionId: string;
  onDelete: () => void;
}) {
  const typeLabels: Record<string, string> = {
    correction: 'Correction',
    note: 'Note',
    flag: 'Flag',
  };
  const typeColors: Record<string, string> = {
    correction: 'border-l-red-500',
    note: 'border-l-blue-500',
    flag: 'border-l-amber-500',
  };

  const content = a.content as Record<string, string>;
  const displayParts: string[] = [];
  if (content.field) displayParts.push(`[${content.field}]`);
  if (content.corrected) displayParts.push(content.corrected);
  else if (content.text) displayParts.push(content.text);
  else if (content.detail) displayParts.push(content.detail);
  if (content.reason) displayParts.push(`\u2014 ${content.reason}`);
  const displayText = displayParts.join(' ') || JSON.stringify(content);

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

function SummaryAnnotationForm({ sessionId, targetId, targetSection, onCreated }: {
  sessionId: string;
  targetId: string;
  targetSection: string;
  onCreated: () => void;
}) {
  const [type, setType] = useState<string>('correction');
  const [field, setField] = useState('pinyin');
  const [text, setText] = useState('');
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);

  const handleSubmit = () => {
    if (!text.trim()) return;
    setSaving(true);
    const content: Record<string, string> = {};
    if (type === 'correction') {
      content.field = field;
      content.corrected = text;
      if (reason.trim()) content.reason = reason;
    } else if (type === 'note') {
      content.text = text;
    } else if (type === 'flag') {
      content.detail = text;
    }

    apiFetch(`/api/sessions/${sessionId}/annotations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_type: 'summary_item',
        target_id: targetId,
        target_section: targetSection,
        annotation_type: type,
        content,
      }),
    })
      .then(res => {
        if (res.ok) {
          setText('');
          setReason('');
          onCreated();
        }
      })
      .finally(() => setSaving(false));
  };

  return (
    <div className="ml-4 mt-1 bg-gray-900/30 border border-gray-800 rounded p-2 space-y-2">
      <div className="flex gap-1 flex-wrap">
        {['correction', 'note', 'flag'].map(t => (
          <button
            key={t}
            onClick={() => setType(t)}
            className={`text-xs px-2 py-1 rounded transition-colors ${
              type === t ? 'bg-indigo-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-white'
            }`}
          >
            {t === 'correction' ? 'Correct' : t === 'note' ? 'Add Note' : 'Flag Issue'}
          </button>
        ))}
      </div>

      {type === 'correction' && (
        <div className="flex gap-1 flex-wrap">
          {['pinyin', 'english', 'chinese', 'zhuyin'].map(f => (
            <button
              key={f}
              onClick={() => setField(f)}
              className={`text-[10px] px-2 py-0.5 rounded transition-colors ${
                field === f ? 'bg-purple-700 text-white' : 'bg-gray-800 text-gray-500 hover:text-white'
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      )}

      <div className="flex gap-2">
        <input
          type="text"
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleSubmit(); }}
          placeholder={
            type === 'correction' ? `Corrected ${field}...`
            : type === 'note' ? 'Your note...'
            : 'Describe the issue...'
          }
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

      {type === 'correction' && (
        <input
          type="text"
          value={reason}
          onChange={e => setReason(e.target.value)}
          placeholder="Reason (optional)..."
          className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-indigo-500"
        />
      )}
    </div>
  );
}

function SummaryReviewPanel({ review, onAction }: {
  review: AIReview;
  onAction: (idx: number, action: 'accept' | 'dismiss') => void;
}) {
  const pendingCount = review.findings.filter(f => f.status === 'pending').length;

  return (
    <div className="rounded-xl border border-purple-800/60 bg-purple-950/20 p-4 space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-purple-300">
            AI Review
          </div>
          <div className="text-xs text-gray-400 mt-0.5">
            {review.provider}/{review.model} &middot; {review.findings_count} finding{review.findings_count !== 1 ? 's' : ''}
            {pendingCount > 0 && ` &middot; ${pendingCount} pending`}
          </div>
        </div>
      </div>

      <div className="space-y-2">
        {review.findings.map((f, idx) => {
          const confidencePct = Math.round(f.confidence * 100);
          const confidenceColor = f.confidence >= 0.8 ? 'text-green-400'
            : f.confidence >= 0.6 ? 'text-yellow-400' : 'text-orange-400';

          return (
            <div key={idx} className={`bg-gray-900 border rounded-lg p-3 ${
              f.status === 'accepted' ? 'border-green-700/50 opacity-70'
                : f.status === 'dismissed' ? 'border-gray-800 opacity-50'
                : 'border-purple-800/40'
            }`}>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 mb-1">
                    {f.section && <span className="text-xs bg-gray-800 text-gray-300 px-1.5 py-0.5 rounded">{f.section}</span>}
                    {f.field && <span className="text-xs text-gray-500">{f.field}</span>}
                    <span className={`text-xs font-medium ${confidenceColor}`}>{confidencePct}%</span>
                    {f.status !== 'pending' && (
                      <span className={`text-xs px-1.5 py-0.5 rounded ${
                        f.status === 'accepted' ? 'bg-green-900/50 text-green-400' : 'bg-gray-700 text-gray-400'
                      }`}>
                        {f.status}
                      </span>
                    )}
                  </div>
                  {f.current_value && f.suggested_value && (
                    <div className="text-sm text-gray-300">
                      <span className="text-red-400 line-through">{f.current_value}</span>
                      {' \u2192 '}
                      <span className="text-green-400">{f.suggested_value}</span>
                    </div>
                  )}
                  <div className="text-xs text-gray-400 mt-1">{f.issue || f.reason}</div>
                </div>
                {f.status === 'pending' && (
                  <div className="flex gap-2 shrink-0">
                    <button
                      onClick={() => onAction(idx, 'accept')}
                      className="px-3 py-1 bg-green-700 hover:bg-green-600 text-white text-xs rounded transition-colors"
                    >
                      Accept
                    </button>
                    <button
                      onClick={() => onAction(idx, 'dismiss')}
                      className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-white text-xs rounded transition-colors"
                    >
                      Dismiss
                    </button>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function LocalErrorHint({ error, provider, model }: { error: string; provider: string; model: string }) {
  const e = error.toLowerCase();
  const isLocal = provider === 'ollama' || provider === 'local';

  if (!isLocal) return null;

  let hint = '';
  if (e.includes('connection refused') || e.includes('econnrefused') || e.includes('failed to fetch') || e.includes('network')) {
    hint = 'Is Ollama running? Start it with: ollama serve';
  } else if (e.includes('not found') || e.includes('model') && e.includes('pull')) {
    hint = `Model not available. Pull it with: ollama pull ${model || '<model>'}`;
  } else if (e.includes('out of memory') || e.includes('oom') || e.includes('memory')) {
    hint = 'Not enough memory for this model. Try a smaller model (7B or 3B).';
  } else if (e.includes('timeout') || e.includes('timed out')) {
    hint = 'Generation timed out. Try a smaller model or shorter session.';
  } else if (e.includes('context length') || e.includes('too long') || e.includes('token')) {
    hint = 'Session may be too long for this model. Try a larger model or summarize fewer messages.';
  }

  if (!hint) return null;

  return (
    <div className="text-xs text-yellow-300/80 mt-1 flex items-start gap-1.5">
      <span className="flex-shrink-0">💡</span>
      <span>{hint}</span>
    </div>
  );
}
