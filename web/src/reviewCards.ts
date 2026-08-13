import type { ReviewItem } from './types';

/**
 * Card direction for Daily Review.
 *
 * Recognition (中文 → English) is the easy direction, and the one lessons
 * already exercise — the teacher writes Chinese, the learner reads it.
 * Production (English → 中文) is the skill actually missing, but it is roughly
 * three times harder, and leading with it on brand-new material is how a review
 * habit dies in its first week.
 *
 * So each item earns its way into the harder direction: see it, learn it, then
 * be asked to produce it. Same two buttons either way — the difficulty ramps on
 * its own, exactly like the daily target does.
 */

export const PRODUCTION_STREAK = 2;

export type CardDirection = 'recognition' | 'production';

export const DIRECTION_LABEL: Record<CardDirection, string> = {
  recognition: 'Recall',
  production: 'Produce',
};

export function directionFor(item: Pick<ReviewItem, 'streak'>): CardDirection {
  return (item.streak ?? 0) >= PRODUCTION_STREAK ? 'production' : 'recognition';
}

/** Front of the card: the prompt you have to answer from memory. */
export function promptFor(item: ReviewItem): string {
  const d = item.data || {};

  // Corrections are exempt. The card is "you said X — what should it have
  // been?", which is production whichever way you turn it; reversing it would
  // mean showing the right answer and asking what you got wrong.
  if (item.item_type === 'correction') {
    return d.learner_original || d.student_said || item.item_key;
  }

  const produce = directionFor(item) === 'production';
  if (item.item_type === 'key_sentence') {
    return produce ? d.en || d.zh || item.item_key : d.zh || item.item_key;
  }
  return produce ? d.en || d.term_zh || item.item_key : d.term_zh || item.item_key;
}
