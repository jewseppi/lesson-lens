import { describe, expect, it } from 'vitest';
import { PRODUCTION_STREAK, directionFor, promptFor } from './reviewCards';
import type { ReviewItem } from './types';

function item(overrides: Partial<ReviewItem> = {}): ReviewItem {
  return {
    item_key: 'k',
    item_type: 'vocab',
    session_id: '2026-08-12',
    data: {},
    due_at: null,
    streak: 0,
    is_new: true,
    ...overrides,
  };
}

describe('directionFor', () => {
  it('starts new items in the easy direction', () => {
    expect(directionFor(item({ streak: 0 }))).toBe('recognition');
  });

  it('stays on recognition until the item is earned', () => {
    for (let s = 0; s < PRODUCTION_STREAK; s++) {
      expect(directionFor(item({ streak: s }))).toBe('recognition');
    }
  });

  it('flips to production once the streak is met', () => {
    expect(directionFor(item({ streak: PRODUCTION_STREAK }))).toBe('production');
    expect(directionFor(item({ streak: PRODUCTION_STREAK + 5 }))).toBe('production');
  });

  it('treats a missing streak as brand new', () => {
    expect(directionFor({ streak: undefined as unknown as number })).toBe('recognition');
  });

  it('drops back to recognition after a lapse resets the streak', () => {
    // The server zeroes streak on "Again", so a forgotten item becomes easy
    // again rather than staying in the hard direction it just failed.
    expect(directionFor(item({ streak: 0 }))).toBe('recognition');
  });
});

describe('promptFor', () => {
  const vocab = { term_zh: '城市', pinyin: 'cheng2shi4', en: 'city' };
  const sentence = { zh: '雨停了', pinyin: 'yu3 ting2 le', en: 'The rain stopped.' };

  it('asks a new vocabulary item for its meaning', () => {
    expect(promptFor(item({ data: vocab, streak: 0 }))).toBe('城市');
  });

  it('asks a mature vocabulary item to be produced', () => {
    expect(promptFor(item({ data: vocab, streak: PRODUCTION_STREAK }))).toBe('city');
  });

  it('asks a new sentence for its meaning', () => {
    expect(promptFor(item({ item_type: 'key_sentence', data: sentence, streak: 0 }))).toBe('雨停了');
  });

  it('asks a mature sentence to be produced', () => {
    expect(
      promptFor(item({ item_type: 'key_sentence', data: sentence, streak: PRODUCTION_STREAK })),
    ).toBe('The rain stopped.');
  });

  it('always shows corrections as the learner said them', () => {
    // Reversing a correction would mean showing the right answer and asking
    // what you got wrong, which teaches nothing.
    const data = { learner_original: '我是很好', teacher_correction: '我很好' };
    for (const streak of [0, PRODUCTION_STREAK, 99]) {
      expect(promptFor(item({ item_type: 'correction', data, streak }))).toBe('我是很好');
    }
  });

  it('falls back to the item key when the payload is thin', () => {
    expect(promptFor(item({ item_key: 'bare', data: {}, streak: 0 }))).toBe('bare');
    expect(promptFor(item({ item_key: 'bare', data: {}, streak: 9 }))).toBe('bare');
  });

  it('falls back to Chinese when an item has no translation', () => {
    expect(promptFor(item({ data: { term_zh: '忙' }, streak: 9 }))).toBe('忙');
  });
});
