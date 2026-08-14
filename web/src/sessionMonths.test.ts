import { describe, expect, it } from 'vitest';
import { monthsOf, defaultMonth, recentOrNewest } from './sessionMonths';
import type { Session } from './types';

function session(date: string, archived = false): Session {
  return {
    session_id: date,
    date,
    start_time: '11:00',
    end_time: '12:00',
    message_count: 10,
    lesson_content_count: 5,
    teacher_message_count: 8,
    student_message_count: 2,
    boundary_confidence: 'high',
    topics: [],
    is_archived: archived,
    has_summary: false,
    needs_summary: true,
  } as unknown as Session;
}

describe('monthsOf', () => {
  it('lists months newest first', () => {
    expect(monthsOf([session('2026-01-05'), session('2026-04-29'), session('2025-12-01')]))
      .toEqual(['2026-04', '2026-01', '2025-12']);
  });

  it('deduplicates months', () => {
    expect(monthsOf([session('2026-04-01'), session('2026-04-29')])).toEqual(['2026-04']);
  });

  it('includes months that hold only archived sessions', () => {
    // Deriving from active sessions only made such a month unreachable: it
    // disappeared from the picker, so its archive could never be opened.
    expect(monthsOf([session('2026-04-29'), session('2026-05-02', true)]))
      .toEqual(['2026-05', '2026-04']);
  });

  it('is empty for no sessions', () => {
    expect(monthsOf([])).toEqual([]);
  });
});

describe('defaultMonth', () => {
  it('picks the newest month that actually has sessions', () => {
    // The bug: defaulting to the current month showed nothing whenever the
    // newest lesson predated it, and because no <option> carried that value the
    // select displayed a different month than the one being filtered on.
    expect(defaultMonth([session('2026-04-29'), session('2026-03-01')])).toBe('2026-04');
  });

  it('never returns a month with no sessions', () => {
    const months = monthsOf([session('2026-04-29')]);
    expect(months).toContain(defaultMonth([session('2026-04-29')]));
  });

  it('falls back to all when there is nothing', () => {
    expect(defaultMonth([])).toBe('all');
  });
});

describe('recentOrNewest', () => {
  const cutoff = '2026-08-01';

  it('returns sessions inside the window', () => {
    const result = recentOrNewest([session('2026-08-10'), session('2026-04-01')], cutoff);
    expect(result.isFallback).toBe(false);
    expect(result.sessions.map(s => s.date)).toEqual(['2026-08-10']);
  });

  it('falls back to the newest when the window is empty', () => {
    // 243 sessions and a blank panel reads as data loss, not as "no lessons
    // lately" — which is the normal state after a break.
    const result = recentOrNewest([session('2026-04-29'), session('2026-04-16')], cutoff);
    expect(result.isFallback).toBe(true);
    expect(result.sessions.map(s => s.date)).toEqual(['2026-04-29', '2026-04-16']);
  });

  it('caps the fallback at five', () => {
    const many = Array.from({ length: 12 }, (_, i) => session(`2026-04-${String(i + 1).padStart(2, '0')}`));
    expect(recentOrNewest(many, cutoff).sessions).toHaveLength(5);
  });

  it('sorts newest first', () => {
    const result = recentOrNewest([session('2026-04-16'), session('2026-04-29')], cutoff);
    expect(result.sessions[0].date).toBe('2026-04-29');
  });

  it('excludes archived sessions from both paths', () => {
    const result = recentOrNewest([session('2026-08-10', true), session('2026-04-29')], cutoff);
    expect(result.isFallback).toBe(true);
    expect(result.sessions.map(s => s.date)).toEqual(['2026-04-29']);
  });

  it('returns nothing when everything is archived', () => {
    const result = recentOrNewest([session('2026-04-29', true)], cutoff);
    expect(result.sessions).toEqual([]);
  });
});
