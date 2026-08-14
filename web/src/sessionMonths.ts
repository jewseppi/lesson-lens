import type { Session } from './types';

/**
 * Month-picker and recent-list selection, extracted so the rules are testable.
 *
 * Both functions here exist because of the same failure: a filter defaulting to
 * *now* rather than to the data. With no lesson this month, the sessions list
 * filtered on an empty month while the select displayed a populated one, and the
 * dashboard rendered a blank panel beneath a stat card reading "243 Total
 * Sessions". Both read as data loss.
 */

/** Months with any session at all, newest first — archived included.
 *
 * Deriving this from active sessions only made a month holding nothing but
 * archived lessons unreachable: it vanished from the picker, so there was no way
 * to select it and open the archive for that period.
 */
export function monthsOf(sessions: Session[]): string[] {
  return [...new Set(sessions.map(s => s.date.slice(0, 7)))].sort().reverse();
}

/** The month to select on load: the newest one that actually has sessions. */
export function defaultMonth(sessions: Session[]): string {
  return monthsOf(sessions)[0] ?? 'all';
}

/**
 * Sessions for the dashboard's recent list.
 *
 * Prefers the two-week window, but falls back to the newest handful when that
 * window is empty — after a break, or before a fresh export is synced, empty is
 * the normal state and showing nothing looks broken.
 */
export function recentOrNewest(
  sessions: Session[],
  cutoff: string,
  fallbackLimit = 5,
): { sessions: Session[]; isFallback: boolean } {
  const active = sessions
    .filter(s => !s.is_archived)
    .sort((a, b) => b.date.localeCompare(a.date) || b.start_time.localeCompare(a.start_time));
  const withinWindow = active.filter(s => s.date >= cutoff);
  return withinWindow.length > 0
    ? { sessions: withinWindow, isFallback: false }
    : { sessions: active.slice(0, fallbackLimit), isFallback: true };
}
