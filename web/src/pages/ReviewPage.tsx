import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { apiJson } from '../api';
import {
  DIRECTION_LABEL,
  PRODUCTION_STREAK,
  directionFor,
  promptFor,
} from '../reviewCards';
import type {
  ReviewCompleteResult,
  ReviewGradeResult,
  ReviewItem,
  ReviewQueue,
} from '../types';

/**
 * Daily Review — one queue across every lesson, time-boxed.
 *
 * The deliberate constraint here is *no decisions*. You land on this page and
 * the first card is already in front of you: no session picker, no mode picker,
 * no deck size. Two grade buttons, because four is a choice and choices are
 * what stopped this app from being used at all.
 */

const TYPE_LABEL: Record<string, string> = {
  correction: 'Your correction',
  key_sentence: 'Sentence',
  vocab: 'Vocabulary',
};

const TYPE_STYLE: Record<string, string> = {
  correction: 'bg-amber-900/40 text-amber-300 border-amber-800/60',
  key_sentence: 'bg-indigo-900/40 text-indigo-300 border-indigo-800/60',
  vocab: 'bg-emerald-900/40 text-emerald-300 border-emerald-800/60',
};

function AnswerBody({ item }: { item: ReviewItem }) {
  const d = item.data || {};

  if (item.item_type === 'correction') {
    return (
      <div className="space-y-3">
        <div>
          <div className="text-xs uppercase tracking-wide text-gray-500 mb-1">You said</div>
          <div className="text-lg text-red-300 line-through decoration-red-500/50">
            {d.learner_original || d.student_said || item.item_key}
          </div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wide text-gray-500 mb-1">Correct</div>
          <div className="text-2xl text-emerald-300">
            {d.teacher_correction || d.correct_form || '—'}
          </div>
        </div>
        {(d.reason || d.explanation) && (
          <p className="text-sm text-gray-400 border-t border-gray-800 pt-3">
            {d.reason || d.explanation}
          </p>
        )}
      </div>
    );
  }

  // The back always shows the whole item, but leads with whichever side you
  // were actually being asked for — so the thing you tried to recall is the
  // thing you see first, not buried under what was already on the front.
  const produce = directionFor(item) === 'production';

  if (item.item_type === 'key_sentence') {
    const zh = d.zh || item.item_key;
    return (
      <div className="space-y-2">
        {produce ? (
          <>
            <div className="text-2xl">{zh}</div>
            {d.pinyin && <div className="text-sm text-gray-400">{d.pinyin}</div>}
            {d.en && (
              <div className="text-base text-gray-300 border-t border-gray-800 pt-2">{d.en}</div>
            )}
          </>
        ) : (
          <>
            <div className="text-2xl text-gray-100">{d.en || '—'}</div>
            <div className="border-t border-gray-800 pt-2">
              <div className="text-lg text-gray-300">{zh}</div>
              {d.pinyin && <div className="text-sm text-gray-500">{d.pinyin}</div>}
            </div>
          </>
        )}
      </div>
    );
  }

  const term = d.term_zh || item.item_key;
  const example = d.example_zh && (
    <div className="border-t border-gray-800 pt-2 text-sm text-gray-400">
      <div>{d.example_zh}</div>
      {d.example_en && <div className="text-gray-500">{d.example_en}</div>}
    </div>
  );

  return (
    <div className="space-y-2">
      {produce ? (
        <>
          <div className="text-3xl">{term}</div>
          {d.pinyin && <div className="text-sm text-gray-400">{d.pinyin}</div>}
          {d.en && <div className="text-lg text-gray-300">{d.en}</div>}
        </>
      ) : (
        <>
          <div className="text-2xl text-gray-100">{d.en || '—'}</div>
          <div className="border-t border-gray-800 pt-2">
            <div className="text-3xl text-gray-300">{term}</div>
            {d.pinyin && <div className="text-sm text-gray-500">{d.pinyin}</div>}
          </div>
        </>
      )}
      {example}
    </div>
  );
}

export default function ReviewPage() {
  const [params] = useSearchParams();
  const minutes = params.get('minutes') || '5';

  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [loading, setLoading] = useState(true);
  const [index, setIndex] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [grading, setGrading] = useState(false);
  const [done, setDone] = useState<ReviewCompleteResult | null>(null);
  const [gradedCount, setGradedCount] = useState(0);
  const [againCount, setAgainCount] = useState(0);

  useEffect(() => {
    apiJson<ReviewQueue>(`/api/review/queue?minutes=${encodeURIComponent(minutes)}`)
      .then(setQueue)
      .catch(() => setQueue(null))
      .finally(() => setLoading(false));
  }, [minutes]);

  const items = useMemo(() => queue?.items ?? [], [queue]);
  const current = items[index];

  const finish = useCallback(async () => {
    try {
      const result = await apiJson<ReviewCompleteResult>('/api/review/complete', {
        method: 'POST',
      });
      setDone(result);
    } catch {
      // Never strand the learner on a finished queue because a stats write
      // failed — the reviews themselves are already recorded.
      setDone({ streak: queue?.streak ?? 0, daily_target: queue?.daily_target ?? 0, target_increased: false, last_completed_on: '' });
    }
  }, [queue]);

  const grade = useCallback(
    async (value: 'again' | 'good') => {
      if (!current || grading) return;
      setGrading(true);
      try {
        await apiJson<ReviewGradeResult>('/api/review/grade', {
          method: 'POST',
          body: JSON.stringify({ item_key: current.item_key, grade: value }),
        });
      } catch {
        /* keep moving: a lost grade costs one repetition, a stuck card costs the habit */
      }
      setGradedCount(n => n + 1);
      if (value === 'again') setAgainCount(n => n + 1);
      setGrading(false);
      setRevealed(false);
      if (index + 1 >= items.length) {
        await finish();
      } else {
        setIndex(i => i + 1);
      }
    },
    [current, grading, index, items.length, finish],
  );

  if (loading) return <div className="text-gray-400">Loading review...</div>;

  if (done) {
    return (
      <div className="max-w-lg mx-auto text-center py-10 space-y-4">
        <div className="text-5xl">🎉</div>
        <h1 className="text-2xl font-bold">Done for today</h1>
        <p className="text-gray-400">
          {gradedCount} item{gradedCount === 1 ? '' : 's'} reviewed
          {againCount > 0 && ` · ${againCount} coming back tomorrow`}
        </p>
        <div className="inline-flex items-center gap-2 rounded-full bg-gray-900 border border-gray-800 px-4 py-2">
          <span className="text-xl">🔥</span>
          <span className="font-medium">{done.streak}-day streak</span>
        </div>
        {done.target_increased && (
          <p className="text-sm text-emerald-400">
            Streak held — tomorrow steps up to {done.daily_target} items.
          </p>
        )}
        <div>
          <Link to="/" className="text-indigo-400 hover:text-indigo-300 text-sm">← Dashboard</Link>
        </div>
      </div>
    );
  }

  if (!items.length) {
    return (
      <div className="max-w-lg mx-auto text-center py-12 space-y-3">
        <div className="text-4xl">✅</div>
        <h1 className="text-xl font-bold">Nothing to review</h1>
        <p className="text-gray-400 text-sm">
          {queue?.total_items
            ? 'Everything is scheduled for later. Come back tomorrow.'
            : 'Generate a lesson summary and its vocabulary, sentences, and corrections land here automatically.'}
        </p>
        <Link to="/" className="text-indigo-400 hover:text-indigo-300 text-sm">← Dashboard</Link>
      </div>
    );
  }

  const progress = Math.round((index / items.length) * 100);

  return (
    <div className="max-w-2xl mx-auto space-y-4 mobile-safe-bottom">
      <div className="flex items-center justify-between">
        <Link to="/" className="text-indigo-400 hover:text-indigo-300 text-sm">← Exit</Link>
        <span className="text-sm text-gray-500">
          {index + 1} / {items.length}
        </span>
      </div>

      <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div
          className="h-full bg-indigo-500 transition-all duration-300"
          style={{ width: `${progress}%` }}
        />
      </div>

      {current && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5 sm:p-8 min-h-[16rem] flex flex-col">
          <div className="flex items-center gap-2 mb-4">
            <span
              className={`text-[11px] px-2 py-0.5 rounded border ${
                TYPE_STYLE[current.item_type] || 'bg-gray-800 text-gray-400 border-gray-700'
              }`}
            >
              {TYPE_LABEL[current.item_type] || current.item_type}
            </span>
            {current.is_new && (
              <span className="text-[11px] px-2 py-0.5 rounded border bg-sky-900/40 text-sky-300 border-sky-800/60">
                New
              </span>
            )}
            {/* Which way round this card is. Shown because the direction changes
                under you as an item matures, and an unexplained switch reads as
                a bug rather than progress. */}
            {current.item_type !== 'correction' && (
              <span
                className={`text-[11px] px-2 py-0.5 rounded border ${
                  directionFor(current) === 'production'
                    ? 'bg-purple-900/40 text-purple-300 border-purple-800/60'
                    : 'bg-gray-800 text-gray-400 border-gray-700'
                }`}
                title={
                  directionFor(current) === 'production'
                    ? 'You know this one — produce it in Chinese'
                    : `Recall the meaning. Gets ${PRODUCTION_STREAK - (current.streak ?? 0)} more right and it flips to production.`
                }
              >
                {DIRECTION_LABEL[directionFor(current)]}
              </span>
            )}
            <Link
              to={`/sessions/${current.session_id}/summary`}
              className="text-[11px] text-gray-600 hover:text-gray-400 ml-auto"
            >
              {current.session_id}
            </Link>
          </div>

          <div className="flex-1 flex items-center justify-center text-center">
            {revealed ? (
              <div className="w-full text-left">
                <AnswerBody item={current} />
              </div>
            ) : (
              <div className="text-2xl sm:text-3xl">{promptFor(current)}</div>
            )}
          </div>
        </div>
      )}

      {!revealed ? (
        <button
          onClick={() => setRevealed(true)}
          className="w-full py-4 rounded-xl bg-indigo-600 hover:bg-indigo-500 font-medium text-lg transition-colors"
        >
          Show answer
        </button>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          <button
            onClick={() => grade('again')}
            disabled={grading}
            className="py-4 rounded-xl bg-gray-800 hover:bg-gray-700 border border-gray-700 font-medium text-lg transition-colors disabled:opacity-50"
          >
            Again
          </button>
          <button
            onClick={() => grade('good')}
            disabled={grading}
            className="py-4 rounded-xl bg-emerald-700 hover:bg-emerald-600 font-medium text-lg transition-colors disabled:opacity-50"
          >
            Got it
          </button>
        </div>
      )}
    </div>
  );
}
