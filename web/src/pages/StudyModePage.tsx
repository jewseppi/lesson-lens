import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { apiJson, trackEvent } from '../api';
import { useFontSize } from '../FontSizeContext';
import type { LessonSummary, Flashcard, QuizQuestion } from '../types';

type StudyTab = 'flashcards' | 'quiz' | 'fill-blank' | 'translation';

export default function StudyModePage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [summary, setSummary] = useState<LessonSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<StudyTab>('flashcards');

  useEffect(() => {
    if (!sessionId) return;
    apiJson<LessonSummary>(`/api/sessions/${sessionId}/summary`)
      .then(setSummary)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [sessionId]);

  if (loading) return <div className="text-gray-400">Loading...</div>;
  if (!summary) {
    return (
      <div className="text-center py-12">
        <p className="text-gray-400 mb-4">No study materials yet. Generate a summary first.</p>
        <Link to={`/sessions/${sessionId}`} className="text-indigo-400">← Back</Link>
      </div>
    );
  }

  const tabs: { id: StudyTab; label: string; count: number }[] = [
    { id: 'flashcards', label: '🃏 Flashcards', count: summary.review?.flashcards?.length || 0 },
    { id: 'quiz', label: '❓ Quiz', count: summary.review?.quiz?.length || 0 },
    { id: 'fill-blank', label: '✏️ Fill Blank', count: summary.review?.fill_blank?.length || 0 },
    { id: 'translation', label: '🔄 Translation', count: summary.review?.translation_drills?.length || 0 },
  ];

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <Link to={`/sessions/${sessionId}/summary`} className="text-indigo-400 hover:text-indigo-300 text-sm">← Summary</Link>
        <h1 className="text-2xl font-bold mt-1">🎯 Study Mode</h1>
        <p className="text-gray-400">{summary.lesson_date} — {summary.title}</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-900 p-1 rounded-lg">
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`flex-1 py-2 px-3 rounded-md text-sm font-medium transition-colors ${
              tab === t.id
                ? 'bg-indigo-600 text-white'
                : 'text-gray-400 hover:text-white hover:bg-gray-800'
            }`}
          >
            {t.label} ({t.count})
          </button>
        ))}
      </div>

      {/* Content */}
      {tab === 'flashcards' && <FlashcardDeck cards={summary.review?.flashcards || []} sessionId={sessionId!} />}
      {tab === 'quiz' && <QuizMode questions={summary.review?.quiz || []} sessionId={sessionId!} />}
      {tab === 'fill-blank' && <FillBlankMode items={summary.review?.fill_blank || []} sessionId={sessionId!} />}
      {tab === 'translation' && <TranslationMode drills={summary.review?.translation_drills || []} sessionId={sessionId!} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Flashcards
// ---------------------------------------------------------------------------
function FlashcardDeck({ cards, sessionId }: { cards: Flashcard[]; sessionId: string }) {
  const { zhClass } = useFontSize();
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [known, setKnown] = useState<Set<string>>(new Set());

  if (cards.length === 0) return <p className="text-gray-400">No flashcards available.</p>;

  const card = cards[index];
  const progress = Math.round(((known.size) / cards.length) * 100);

  const next = (gotIt: boolean) => {
    if (gotIt) {
      known.add(card.id);
      setKnown(new Set(known));
      trackEvent('flashcard_known', { session_id: sessionId, card_id: card.id });
    }
    setFlipped(false);
    setIndex((index + 1) % cards.length);
  };

  return (
    <div className="space-y-4">
      {/* Progress */}
      <div className="flex items-center gap-3">
        <div className="flex-1 bg-gray-800 rounded-full h-2">
          <div className="bg-green-500 h-2 rounded-full transition-all" style={{ width: `${progress}%` }} />
        </div>
        <span className="text-sm text-gray-400">{known.size}/{cards.length}</span>
      </div>

      {/* Card */}
      <div
        onClick={() => setFlipped(!flipped)}
        className="bg-gray-900 border border-gray-800 rounded-xl p-8 min-h-[200px] flex items-center justify-center cursor-pointer hover:border-indigo-600 transition-all"
      >
        <div className="text-center">
          {flipped ? (
            <div>
              <div className="text-gray-300 text-lg">{card.back}</div>
              {card.hint && <div className="text-gray-500 text-sm mt-2">Hint: {card.hint}</div>}
            </div>
          ) : (
            <div className={`${zhClass} text-2xl`}>{card.front}</div>
          )}
          <div className="text-gray-600 text-xs mt-4">
            {flipped ? 'Click to flip back' : 'Click to reveal'}
          </div>
        </div>
      </div>

      {/* Controls */}
      {flipped && (
        <div className="flex gap-3">
          <button
            onClick={() => next(false)}
            className="flex-1 bg-red-900/50 hover:bg-red-900 text-red-300 py-3 rounded-lg font-medium transition-colors"
          >
            ❌ Again
          </button>
          <button
            onClick={() => next(true)}
            className="flex-1 bg-green-900/50 hover:bg-green-900 text-green-300 py-3 rounded-lg font-medium transition-colors"
          >
            ✅ Got it
          </button>
        </div>
      )}

      <div className="text-center text-sm text-gray-600">
        Card {index + 1} of {cards.length}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Quiz
// ---------------------------------------------------------------------------
function QuizMode({ questions, sessionId }: { questions: QuizQuestion[]; sessionId: string }) {
  const [index, setIndex] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [score, setScore] = useState(0);
  const [finished, setFinished] = useState(false);

  if (questions.length === 0) return <p className="text-gray-400">No quiz questions available.</p>;

  if (finished) {
    const pct = Math.round((score / questions.length) * 100);
    trackEvent('quiz_complete', { session_id: sessionId, score, total: questions.length });
    return (
      <div className="text-center py-8 space-y-4">
        <div className="text-6xl">{pct >= 80 ? '🎉' : pct >= 50 ? '💪' : '📚'}</div>
        <div className="text-2xl font-bold">{score}/{questions.length}</div>
        <div className="text-gray-400">{pct}% correct</div>
        <button
          onClick={() => { setIndex(0); setSelected(null); setScore(0); setFinished(false); }}
          className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-2 rounded-lg"
        >
          Try Again
        </button>
      </div>
    );
  }

  const q = questions[index];
  const isCorrect = selected === q.correct_index;
  const answered = selected !== null;

  const handleNext = () => {
    if (isCorrect) setScore(s => s + 1);
    if (index + 1 >= questions.length) {
      setFinished(true);
    } else {
      setIndex(index + 1);
      setSelected(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between text-sm text-gray-400">
        <span>Question {index + 1} of {questions.length}</span>
        <span>Score: {score}</span>
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
        <p className="text-lg font-medium mb-4">{q.question}</p>
        <div className="space-y-2">
          {q.options.map((opt, i) => {
            let btnClass = 'bg-gray-800 border-gray-700 text-gray-300 hover:border-indigo-500';
            if (answered) {
              if (i === q.correct_index) btnClass = 'bg-green-900/50 border-green-600 text-green-300';
              else if (i === selected) btnClass = 'bg-red-900/50 border-red-600 text-red-300';
              else btnClass = 'bg-gray-800/50 border-gray-700 text-gray-500';
            } else if (i === selected) {
              btnClass = 'bg-indigo-900 border-indigo-500 text-indigo-300';
            }

            return (
              <button
                key={i}
                onClick={() => !answered && setSelected(i)}
                disabled={answered}
                className={`w-full text-left px-4 py-3 rounded-lg border transition-colors ${btnClass}`}
              >
                <span className="font-medium mr-2">{String.fromCharCode(65 + i)}.</span>
                {opt}
              </button>
            );
          })}
        </div>

        {answered && q.explanation && (
          <div className="mt-4 p-3 bg-gray-800 rounded-lg text-sm text-gray-300">
            💡 {q.explanation}
          </div>
        )}
      </div>

      {answered && (
        <button
          onClick={handleNext}
          className="w-full bg-indigo-600 hover:bg-indigo-700 text-white py-3 rounded-lg font-medium transition-colors"
        >
          {index + 1 >= questions.length ? 'See Results' : 'Next Question'}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Fill in the Blank
// ---------------------------------------------------------------------------
function FillBlankMode({ items, sessionId }: {
  items: Array<{ id: string; sentence: string; answer: string; hint?: string }>;
  sessionId: string;
}) {
  const [index, setIndex] = useState(0);
  const [input, setInput] = useState('');
  const [revealed, setRevealed] = useState(false);
  const [correct, setCorrect] = useState(0);

  if (items.length === 0) return <p className="text-gray-400">No fill-in-the-blank exercises.</p>;

  const item = items[index];
  const isCorrect = input.trim() === item.answer;

  const check = () => {
    setRevealed(true);
    if (isCorrect) setCorrect(c => c + 1);
    trackEvent('fill_blank_attempt', { session_id: sessionId, correct: isCorrect });
  };

  const next = () => {
    setRevealed(false);
    setInput('');
    setIndex((index + 1) % items.length);
  };

  return (
    <div className="space-y-4">
      <div className="text-sm text-gray-400">{index + 1} of {items.length} · {correct} correct</div>

      <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
        <p className="text-lg mb-4 leading-relaxed">{item.sentence}</p>
        {item.hint && <p className="text-sm text-gray-500 mb-3">Hint: {item.hint}</p>}

        <input
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !revealed && check()}
          placeholder="Type your answer..."
          disabled={revealed}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-white text-lg focus:outline-none focus:border-indigo-500"
        />

        {revealed && (
          <div className={`mt-3 p-3 rounded-lg ${isCorrect ? 'bg-green-900/30 text-green-300' : 'bg-red-900/30 text-red-300'}`}>
            {isCorrect ? '✅ Correct!' : `❌ Answer: ${item.answer}`}
          </div>
        )}
      </div>

      {!revealed ? (
        <button onClick={check} className="w-full bg-indigo-600 hover:bg-indigo-700 text-white py-3 rounded-lg font-medium">
          Check
        </button>
      ) : (
        <button onClick={next} className="w-full bg-gray-800 hover:bg-gray-700 text-white py-3 rounded-lg font-medium">
          Next
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Translation Drills
// ---------------------------------------------------------------------------
function TranslationMode({ drills, sessionId }: {
  drills: Array<{ id: string; source_lang: string; source_text: string; target_text: string; hint?: string }>;
  sessionId: string;
}) {
  const [index, setIndex] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [selfScore, setSelfScore] = useState(0);

  if (drills.length === 0) return <p className="text-gray-400">No translation drills available.</p>;

  const drill = drills[index];
  const isZhToEn = drill.source_lang === 'zh';

  const next = (gotIt: boolean) => {
    if (gotIt) setSelfScore(s => s + 1);
    trackEvent('translation_attempt', { session_id: sessionId, correct: gotIt });
    setRevealed(false);
    setIndex((index + 1) % drills.length);
  };

  return (
    <div className="space-y-4">
      <div className="text-sm text-gray-400">{index + 1} of {drills.length} · {selfScore} self-scored correct</div>

      <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 text-center">
        <div className="text-xs text-gray-500 mb-2">
          Translate {isZhToEn ? 'Chinese → English' : 'English → Chinese'}
        </div>
        <div className={`text-xl mb-4 ${isZhToEn ? 'zh-text' : ''}`}>
          {drill.source_text}
        </div>

        {revealed ? (
          <div className="bg-gray-800 rounded-lg p-4 mt-4">
            <div className="text-gray-400 text-xs mb-1">Answer</div>
            <div className="text-lg text-gray-200">{drill.target_text}</div>
          </div>
        ) : (
          <button
            onClick={() => setRevealed(true)}
            className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-2 rounded-lg transition-colors"
          >
            Reveal Answer
          </button>
        )}
      </div>

      {revealed && (
        <div className="flex gap-3">
          <button onClick={() => next(false)} className="flex-1 bg-red-900/50 hover:bg-red-900 text-red-300 py-3 rounded-lg font-medium">
            ❌ Wrong
          </button>
          <button onClick={() => next(true)} className="flex-1 bg-green-900/50 hover:bg-green-900 text-green-300 py-3 rounded-lg font-medium">
            ✅ Got it
          </button>
        </div>
      )}
    </div>
  );
}
