import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { apiJson, trackEvent } from '../api';
import { useFontSize } from '../FontSizeContext';
import type { LessonSummary } from '../types';

type Provider = 'openai' | 'anthropic' | 'gemini' | 'ollama' | 'openai_compatible_local';
const STORAGE_KEY = 'lessonlens-provider';

const ALL_PROVIDERS: Provider[] = ['openai', 'anthropic', 'gemini', 'ollama', 'openai_compatible_local'];

function getStoredProvider(): Provider {
  const value = localStorage.getItem(STORAGE_KEY);
  if (value && ALL_PROVIDERS.includes(value as Provider)) return value as Provider;
  return 'openai';
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
  const [provider] = useState<Provider>(getStoredProvider);


  useEffect(() => {
    if (!sessionId) return;
    apiJson<LessonSummary>(`/api/sessions/${sessionId}/summary`)
      .then(data => {
        setSummary(data);
        trackEvent('view_summary', { session_id: sessionId });
      })
      .catch(() => setNoSummary(true))
      .finally(() => setLoading(false));
  }, [sessionId]);

  const handleGenerate = async () => {
    if (!sessionId) return;
    setGenerating(true);
    setGenError('');
    try {
      const data = await apiJson<LessonSummary>(`/api/sessions/${sessionId}/generate`, {
        method: 'POST',
        body: JSON.stringify({ provider }),
      });
      setSummary(data);
      setNoSummary(false);
      trackEvent('generate_summary', { session_id: sessionId, provider });
    } catch (err) {
      setGenError(err instanceof Error ? err.message : 'Generation failed');
    } finally {
      setGenerating(false);
    }
  };

  if (loading) return <div className="text-gray-400">Loading summary...</div>;

  if (noSummary && !summary) {
    return (
      <div className="max-w-lg mx-auto text-center py-12 space-y-6">
        <div className="text-5xl">📝</div>
        <p className="text-gray-300 text-lg">No summary generated yet for this session.</p>

        <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-4 text-left">
          <h3 className="font-semibold text-white">Generate with AI</h3>
          <p className="text-sm text-gray-400">
            This will call the LLM to summarize the lesson, extract vocabulary,
            key sentences, corrections, and create study exercises.
          </p>

          <p className="text-xs text-gray-500">
            Default provider: <span className="text-gray-300">{provider}</span>. Change it in Settings.
          </p>

          {genError && (
            <div className="bg-red-900/50 border border-red-700 text-red-300 text-sm rounded-lg p-3">
              {genError}
            </div>
          )}

          <button
            onClick={handleGenerate}
            disabled={generating}
            className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white py-3 rounded-lg font-medium transition-colors"
          >
            {generating ? '🔄 Generating summary...' : '🚀 Generate Summary'}
          </button>

          {generating && (
            <p className="text-xs text-gray-500 text-center">
              This may take 15–30 seconds (2 LLM passes: summary + exercises).
            </p>
          )}
        </div>

        <Link to={`/sessions/${sessionId}`} className="text-indigo-400 hover:text-indigo-300 text-sm">
          ← Back to session
        </Link>
      </div>
    );
  }
  if (!summary) return null;

  const { zhClass } = useFontSize();
  const hasVocabularyZhuyin = summary.vocabulary.some(item => Boolean(item.zhuyin));

  return (
    <div className="max-w-4xl mx-auto space-y-6 sm:space-y-8">
      <div>
        <Link to={`/sessions/${sessionId}`} className="text-indigo-400 hover:text-indigo-300 text-sm">← Session</Link>
        <h1 className="text-2xl font-bold mt-1">{summary.title}</h1>
        <p className="text-gray-400">{summary.lesson_date}</p>
      </div>

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
            {summary.key_sentences.map(ks => (
              <div key={ks.id} className="bg-gray-900 border border-gray-800 rounded-lg p-4">
                <div className={zhClass}>{ks.zh}</div>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <div className="pinyin-text">{ks.pinyin}</div>
                  <PronunciationNote note={ks.pronunciation_note} />
                </div>
                {ks.zhuyin && <div className="zhuyin-text mt-1">{ks.zhuyin}</div>}
                <div className="text-gray-300 mt-1">{ks.en}</div>
                {ks.context_note && <div className="text-gray-500 text-sm mt-1 italic">{ks.context_note}</div>}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Vocabulary */}
      {summary.vocabulary.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3 text-indigo-400">Vocabulary</h2>
          <div className="md:hidden space-y-3">
            {summary.vocabulary.map((v, i) => (
              <div key={`${v.term_zh}-${i}-mobile`} className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-2">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className={`${zhClass} text-base`}>{v.term_zh}</div>
                    <div className="pinyin-text mt-1">{v.pinyin}</div>
                    {v.zhuyin && <div className="zhuyin-text mt-1">{v.zhuyin}</div>}
                  </div>
                  <PronunciationNote note={v.pronunciation_note} />
                </div>
                <div className="text-gray-300">{v.en}</div>
                <div className="text-xs uppercase tracking-wide text-gray-500">{v.pos_or_type}</div>
              </div>
            ))}
          </div>

          <div className="hidden md:block overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-500 border-b border-gray-800">
                  <th className="pb-2 pr-4">Term</th>
                  <th className="pb-2 pr-4">Pinyin</th>
                  {hasVocabularyZhuyin && <th className="pb-2 pr-4">Zhuyin</th>}
                  <th className="pb-2 pr-4">English</th>
                  <th className="pb-2">Type</th>
                </tr>
              </thead>
              <tbody>
                {summary.vocabulary.map((v, i) => (
                  <tr key={i} className="border-b border-gray-800/50">
                    <td className="py-2 pr-4 font-medium"><span className={zhClass}>{v.term_zh}</span></td>
                    <td className="py-2 pr-4 text-gray-400 italic">
                      <div className="flex flex-wrap items-center gap-2">
                        <span>{v.pinyin}</span>
                        <PronunciationNote note={v.pronunciation_note} />
                      </div>
                    </td>
                    {hasVocabularyZhuyin && <td className="py-2 pr-4 text-sky-300">{v.zhuyin || '—'}</td>}
                    <td className="py-2 pr-4 text-gray-300">{v.en}</td>
                    <td className="py-2 text-gray-500">{v.pos_or_type}</td>
                  </tr>
                ))}
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
            {summary.corrections.map(c => (
              <div key={c.id} className="bg-gray-900 border border-gray-800 rounded-lg p-4">
                <div className="text-red-400 line-through">{c.learner_original}</div>
                <div className="text-green-400 font-medium mt-1">→ {c.teacher_correction}</div>
                <div className="text-gray-400 text-sm mt-1">{c.reason}</div>
              </div>
            ))}
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
          🎯 Study This Lesson
        </Link>
      </div>
    </div>
  );
}
