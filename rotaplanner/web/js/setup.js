// Loading and changing the permanent set-up (people, tasks, regular week…).
import { api, ApiError, monthPath } from './api.js';
import { setMonth, setSession, state } from './state.js';
import { toast } from './ui.js';
import { needPen, saidAlready } from './actions.js';

export async function loadSetup() {
  setSession({ setup: await api.get('/api/setup') });
}

/**
 * Send a set-up change. Resolves with the server's answer, or throws an
 * ApiError whose message is meant for the user (shown in the form).
 */
export async function saveSetup(path, body, { message } = {}) {
  needPen();  // another computer edits: say so, send nothing
  const result = await api.post(`/api/${path}`, body);
  if (result.catalog) setSession({ setup: result, monthSetup: null });  // Set up lists types, areas, skills: load them again
  if (message) toast(message);
  // People and tasks shape the rota on screen: refresh it quietly.
  if (state.month) {
    api.get(monthPath(state.month.id)).then(setMonth).catch(() => {});
  }
  return result;
}

export function errorText(error) {
  return error instanceof ApiError ? error.message : 'Something went wrong.';
}

/** Say that something failed, unless a window already said why (another computer edits). */
export function failed(error) {
  if (!saidAlready(error)) toast(errorText(error), { tone: 'bad' });
}

// ---- Lookups used by several screens --------------------------------------

export const skillName = (setup, id) => setup.catalog.skills.find((s) => s.id === id)?.name || 'Anyone';
export const personById = (setup) => new Map(setup.people.map((p) => [p.id, p]));

export const WEEKDAYS = [
  ['mon', 'Mon'], ['tue', 'Tue'], ['wed', 'Wed'], ['thu', 'Thu'], ['fri', 'Fri'], ['sat', 'Sat'], ['sun', 'Sun'],
];
export const PERIOD_LABELS = { morning: 'Morning', afternoon: 'Afternoon', evening: 'Evening', anytime: 'Any time' };
export const PRIORITY_LABELS = { critical: 'Essential', normal: 'Normal', optional: 'Optional' };

/** People needed on a weekday by a task (the usual number, no event). */
export function usualCount(task, weekday) {
  return task.headcount[weekday] ?? task.headcount.default ?? 0;
}

/** "Every day 1 · Sun 7 · Events 2" */
export function headcountSummary(task) {
  const counts = WEEKDAYS.map(([key]) => usualCount(task, key));
  const parts = [];
  if (counts.every((n) => n === counts[0])) {
    if (counts[0]) parts.push(`Every day ${counts[0]}`);
  } else {
    WEEKDAYS.forEach(([, label], i) => { if (counts[i]) parts.push(`${label} ${counts[i]}`); });
  }
  if (task.headcount.event) parts.push(`Events ${task.headcount.event}`);
  return parts.join(' · ') || 'Not needed (except days set by hand)';
}

export function timeText(task) {
  const range = task.start && task.end ? `${task.start}–${task.end}` : '';
  return [range, task.time_label].filter(Boolean).join(' · ') || (task.periods.includes('anytime') ? 'Any time' : '');
}

/** "Morning & afternoon" */
export function partsText(task) {
  if (task.periods.includes('anytime')) return 'Any time';
  const text = task.periods.map((p) => PERIOD_LABELS[p].toLowerCase()).join(' & ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function fullName(person) {
  return `${person.first_name} ${person.last_name}`.trim() || person.name;
}
