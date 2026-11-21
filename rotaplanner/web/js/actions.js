// Every change to the rota goes through here, so feedback (toast with Undo,
// error messages, "assign anyway" confirmation) is the same everywhere. So do
// undo, redo and going back several steps (Recent changes, history.js): the
// words they show all come from the program.
//
// A shift belongs to the month of its date. The Event view can show days of
// the next (or previous) month: changes to those go to that month, and its
// refreshed view is kept in `state.session.otherMonths`.
import { api, ApiError, monthPath } from './api.js';
import { monthViewFor, peopleById, setMonth, setSession, state } from './state.js';
import { mayChange } from './sync.js';
import { confirmForce, toast } from './ui.js';

export const slotKey = (taskId, date, position) => `${taskId}|${date}|${position}`;

export function parseSlotKey(key) {
  const [taskId, date, position] = key.split('|');
  return { taskId, date, position: Number(position) };
}

/** The month a date belongs to: "2027-01-02" → "2027-01". */
export const monthOf = (date) => date.slice(0, 7);

export function taskById(taskId, date = null) {
  const month = date ? monthViewFor(date) : state.month;
  for (const area of month?.areas || []) {
    const task = area.tasks.find((t) => t.id === taskId);
    if (task) return task;
  }
  return null;
}

/** A change not even sent: another computer edits (the reason was shown, see sync.js). */
export class LookingOnly extends ApiError {
  constructor() { super(423, { error: 'Another computer is editing: you can look, not change.', kind: 'looking' }); }
}

/**
 * Throws LookingOnly (after saying why, and what to do) while another computer edits.
 * Called before every change: it also notes which step each month on screen has on
 * top, so that the message after the change offers Undo only for a new step.
 */
export function needPen() {
  if (!mayChange()) throw new LookingOnly();
  rememberTops();
}

/** Refused because of the editing (another computer, no connection): a window already says so, with the way out. */
export const saidAlready = (error) => error instanceof ApiError && error.status === 423
  && ['looking', 'held', 'handing', 'stale', 'behind', 'offline'].includes(error.payload.kind);

function reportError(error) {
  if (saidAlready(error)) return;
  toast(error instanceof ApiError ? error.message : 'Something went wrong.', { tone: 'bad' });
}

/** Send a change to a month (the one on screen unless another is named) and show the result. */
async function post(rest, body, monthId = state.month.id) {
  needPen();
  changesSent += 1;
  const month = await api.post(monthPath(monthId, rest), body);
  if (monthId === state.month.id) {
    setMonth(month);
  } else {
    setSession({ otherMonths: { ...state.session.otherMonths, [monthId]: month } });
  }
  await refreshNextDoor(monthId);
  // The highlighted person's possible slots change with every assignment.
  const person = state.session.person;
  if (person) {
    const eligible = await loadEligible(person);
    if (state.session.person === person) setSession({ eligible });
  }
  return month;
}

let changesSent = 0;  // every change sent from here: an answer fetched before a later one is out of date

/**
 * After a change to `changedId` in the Event view over two months, fetch again the
 * other months on screen: the rules look a few days into the month next door, so a
 * mark there can change too.
 */
async function refreshNextDoor(changedId) {
  if (state.route !== 'build' || state.ui.view !== 'event') return;
  const others = [state.month.id, ...Object.keys(state.session.otherMonths)].filter((id) => id !== changedId);
  if (!Object.keys(state.session.otherMonths).length || !others.length) return;
  const asked = changesSent;
  let views;
  try {
    views = await Promise.all(others.map((id) => api.get(monthPath(id))));
  } catch {
    return;  // the change itself was made; the marks there are fetched again next time
  }
  if (asked !== changesSent) return;  // something else was changed meanwhile: its own answer is newer
  others.forEach((id, i) => {
    if (id === state.month?.id) setMonth(views[i]);
    else if (state.session.otherMonths[id]) setSession({ otherMonths: { ...state.session.otherMonths, [id]: views[i] } });
  });
}

// ---- Undo, redo, and going back several steps ------------------------------

const viewOf = (monthId) => (monthId === state.month?.id ? state.month : state.session.otherMonths[monthId]);

const MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
/** The shortcuts as tooltips name them (Cmd on a Mac; the keys themselves are handled in main.js). */
export const KEYS = MAC ? { undo: '⌘Z', redo: '⇧⌘Z' } : { undo: 'Ctrl Z', redo: 'Ctrl Y' };

/** Why the arrows do nothing on People, Regular week, Settings and the typical month's tasks. */
export const NO_UNDO_HERE = 'Undo works on a month’s rota. Changes on this screen are saved as you make them.';

/** Months whose rota is on screen: the month, and in the Event view the other month(s) of the event. */
export function monthsOnScreen() {
  if (!state.month) return [];
  const others = state.route === 'build' && state.ui.view === 'event' ? Object.keys(state.session.otherMonths) : [];
  return [state.month.id, ...others];
}

/**
 * The month the arrows, Ctrl+Z and Recent changes act on: { monthId, title, history },
 * or { monthId: null, reason } on a screen whose changes cannot be undone (no reason:
 * no month yet). In the Event view over two months: the one changed last (step ids
 * grow across the program), or for redo the one undone last.
 */
export function undoTarget(redo = false) {
  const { route, session } = state;
  if (['people', 'regular', 'settings'].includes(route)) return { monthId: null, reason: NO_UNDO_HERE };
  if (route === 'tasks') {
    const config = session.config;
    if (!config || !config.history) return { monthId: null, reason: NO_UNDO_HERE };  // the typical month
    const fresher = config.scope === state.month?.id ? state.month.history : config.history;
    return { monthId: config.scope, title: config.title, history: fresher };
  }
  const ids = monthsOnScreen();
  if (!ids.length) return { monthId: null };
  const top = (id) => viewOf(id)?.history?.[redo ? 'redo' : 'undo']?.id;
  const ready = ids.filter((id) => top(id) != null);
  const chosen = ready.length ? ready.reduce((a, b) => ((redo ? top(b) < top(a) : top(b) > top(a)) ? b : a)) : ids[0];
  return { monthId: chosen, title: viewOf(chosen).title, history: viewOf(chosen).history };
}

export const redoTarget = () => undoTarget(true);

/** Undo the last change (of `monthId`, else of the month the arrows act on). With `expect`, only that step. */
export async function undo(monthId = null, expect = null) {
  const target = monthId ? { monthId } : undoTarget();
  if (!target.monthId) {
    if (target.reason) toast(target.reason);
    return;
  }
  if (!monthId && !target.history?.undo) return;
  await restore(target.monthId, '/undo', expect != null ? { expect } : {});
}

export async function redo(monthId = null) {
  const target = monthId ? { monthId } : redoTarget();
  if (!target.monthId) {
    if (target.reason) toast(target.reason);
    return;
  }
  if (!monthId && !target.history?.redo) return;
  await restore(target.monthId, '/redo', {});
}

/** Recent changes: back to before step `id` (in the undo list), or redo up to it (in the redo list). */
export function goTo(monthId, id) {
  return restore(monthId, '/history/go', { to: id });
}

/** Undo, redo or go, then say what was done, with the way back. */
async function restore(monthId, path, body) {
  try {
    needPen();
    changesSent += 1;
    const view = await api.post(monthPath(monthId, path), body);
    await afterRestore(monthId, view);
    const { did } = view;
    toast(did.text, { actionLabel: did.back_label, onAction: () => goTo(monthId, did.back) });
  } catch (error) {
    reportError(error);
  }
}

/**
 * The one refresh after undo, redo or going back, for the month restored (not
 * necessarily the one on screen): its view, the Set up, Availability and Tasks &
 * Areas data loaded for it, and the other months on screen (an event shared with
 * them follows the undo there). Everything is swapped in at once: no flash.
 */
export async function afterRestore(monthId, view) {
  const { session } = state;
  const asked = changesSent;
  const quiet = (promise) => (promise ? promise.catch(() => null) : null);
  const others = [state.month?.id, ...Object.keys(session.otherMonths)].filter((id) => id && id !== monthId);
  const [setup, availability, config, ...views] = await Promise.all([
    quiet(session.monthSetup?.id === monthId ? api.get(monthPath(monthId, '/setup')) : null),
    quiet(session.availability?.id === monthId ? api.get(monthPath(monthId, '/availability')) : null),
    quiet(session.config?.scope === monthId ? api.get(`/api/config?scope=${encodeURIComponent(monthId)}`) : null),
    ...others.map((id) => quiet(api.get(monthPath(id)))),
  ]);
  if (asked !== changesSent) return;  // something else was changed meanwhile: its own answer is newer
  const patch = { otherMonths: { ...state.session.otherMonths } };
  if (setup) patch.monthSetup = setup;
  if (availability) patch.availability = availability;
  if (config) patch.config = config;  // the same scope, with its tasks as they are now
  let main = monthId === state.month?.id ? view : null;
  if (!main && patch.otherMonths[monthId]) patch.otherMonths[monthId] = view;
  others.forEach((id, i) => {
    if (!views[i]) return;
    if (id === state.month?.id) main = views[i];
    else if (patch.otherMonths[id]) patch.otherMonths[id] = views[i];
  });
  const person = state.session.person;
  if (person) patch.eligible = null;
  setSession(patch);
  if (main) setMonth(main);
  if (person) {
    const eligible = await loadEligible(person);
    if (state.session.person === person) setSession({ eligible });
  }
}

let topsBefore = new Map();  // month id -> id of its last step, as shown before the change being sent

function rememberTops() {
  const tops = new Map();
  const add = (id, history) => { if (id && history) tops.set(id, history.undo?.id ?? null); };
  const { session } = state;
  add(session.availability?.id, session.availability?.history);
  add(session.monthSetup?.id, session.monthSetup?.history);
  add(session.config?.scope, session.config?.history);
  for (const [id, view] of Object.entries(session.otherMonths)) add(id, view.history);
  add(state.month?.id, state.month?.history);  // refreshed after every change: trusted last
  topsBefore = tops;
}

/**
 * After a change to `monthId`, with the month's `history` as the program answered:
 * says what was done, with Undo when the change made a new step (that Undo sends
 * the step's id, so it never undoes something else). `message`: the words to show,
 * by default the step's own name; a function is given the new step (or null);
 * null: nothing.
 */
export function changed(history, monthId, message) {
  const step = history?.undo;
  const made = Boolean(step) && step.id !== topsBefore.get(monthId);
  if (made) topsBefore.set(monthId, step.id);
  const words = message === undefined ? (made ? step.label : null)
    : typeof message === 'function' ? message(made ? step : null) : message;
  if (!words) return;
  toast(words, made ? { actionLabel: 'Undo', onAction: () => undo(monthId, step.id) } : {});
}

/**
 * Put someone in a slot. `who` is { personId }, or { guestKind: 'volunteer' | 'guest', guest: name or '' }.
 * If a rule is broken and `force` is not set, asks before forcing.
 * Resolves true when the slot was filled.
 */
export async function assign(key, who, { force = false, reason = null } = {}) {
  const { taskId, date, position } = parseSlotKey(key);
  const body = { task_id: taskId, date, position, person_id: who.personId || '', guest: who.guest || '', guest_kind: who.guestKind || '', force, reason };
  let month;
  try {
    month = await post('/assign', body, monthOf(date));
  } catch (error) {
    if (error instanceof ApiError && error.status === 409 && error.payload.findings) {
      const name = peopleById().get(who.personId)?.name || 'This person';
      const typed = await confirmForce({ title: `${name} — assign anyway?`, findings: error.payload.findings });
      if (typed === null) return false;
      return assign(key, who, { force: true, reason: typed });
    }
    reportError(error);
    return false;
  }
  changed(month.history, monthOf(date));
  return true;
}

// ---- Suggestions -----------------------------------------------------------

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** What a round of suggestions says: how many, and how many shifts stay open (the Assistant says why). */
function suggestedToast(proposed, left, more = false) {
  const stay = `${plural(left, 'shift')} ${left === 1 ? 'stays' : 'stay'} open: the Assistant says why.`;
  if (proposed) {
    toast(`${plural(proposed, more ? 'more suggestion' : 'suggestion')}, shown dashed. ${left ? stay : 'Check them, then accept.'}`);
  } else if (left) {
    toast(`Nobody${more ? ' else' : ''} can be suggested for these shifts: the Assistant says why.`);
  } else {
    toast('No open shifts here: nothing to suggest.');
  }
}

/** Ask for suggestions for open shifts: `scope` is {} (whole month), {dates} or {area_id}. */
export async function prefill(scope = {}) {
  if (state.session.looking) return;
  setSession({ looking: 'prefill' });
  try {
    // Days of an event can belong to two months: each month suggests for its own days.
    const byMonth = new Map();
    for (const date of scope.dates || []) byMonth.set(monthOf(date), [...(byMonth.get(monthOf(date)) || []), date]);
    const calls = scope.dates ? [...byMonth].map(([id, dates]) => [id, { dates }]) : [[state.month.id, scope]];
    let proposed = 0;
    let left = 0;
    for (const [monthId, body] of calls) {
      const month = await post('/prefill', body, monthId);
      proposed += month.prefill.proposed;
      left += month.prefill.left_open;
    }
    suggestedToast(proposed, left);
  } catch (error) {
    reportError(error);
  } finally {
    setSession({ looking: null });
  }
}

/**
 * "Suggest for these too": go further over people's targets (never over a limit on
 * their card) for the shifts the suggestions left open. Each month keeps, on the
 * server, the days and area its suggestions were asked for, so this asks every
 * month on screen whose suggestions could go further (an event can run over two).
 */
export async function prefillBeyond() {
  if (state.session.looking) return;
  const others = state.ui.view === 'event' ? Object.keys(state.session.otherMonths) : [];
  const ids = [state.month.id, ...others].filter((id) => viewOf(id)?.suggested?.beyond);
  if (!ids.length) return;
  setSession({ looking: 'beyond' });
  try {
    let proposed = 0;
    let left = 0;
    for (const monthId of ids) {
      const month = await post('/prefill', { beyond: true }, monthId);
      proposed += month.prefill.proposed;
      left += month.prefill.left_open;
    }
    suggestedToast(proposed, left, true);
  } catch (error) {
    reportError(error);
  } finally {
    setSession({ looking: null });
  }
}

export async function rebalance() {
  if (state.session.looking) return;
  setSession({ looking: 'rebalance' });
  try {
    const month = await post('/rebalance');
    toast(month.rebalance.proposed ? `${month.rebalance.proposed} moves suggested to even out hours` : 'Hours are already as even as they can be.');
  } catch (error) {
    reportError(error);
  } finally {
    setSession({ looking: null });
  }
}

/** The month of the first slot key, else the month on screen. */
const monthOfKeys = (keys) => (keys?.length ? monthOf(parseSlotKey(keys[0]).date) : state.month.id);

/** Accept suggestions (all of a month's, or those whose slot keys are given) as one change. */
export async function acceptProposals(keys = null, monthId = monthOfKeys(keys)) {
  try {
    const month = await post('/proposals/accept', keys ? { keys } : {}, monthId);
    const late = month.skipped ? ` · ${month.skipped} no longer fitted` : '';
    changed(month.history, monthId, (step) => (step ? `${step.label}${late}` : late && `Nothing accepted${late}`));
  } catch (error) {
    reportError(error);
  }
}

export async function discardProposals(keys = null, monthId = monthOfKeys(keys)) {
  try {
    await post('/proposals/discard', keys ? { keys } : {}, monthId);
  } catch (error) {
    reportError(error);
  }
}

/** Free `personId` for `key` by giving their shift `from` to `replacement` (that day or that week, same month). */
export async function swap(key, personId, from, replacement) {
  const monthId = monthOf(parseSlotKey(key).date);
  try {
    const month = await post('/swap', { key, person_id: personId, from, replacement }, monthId);
    changed(month.history, monthId);
  } catch (error) {
    reportError(error);
  }
}

// ---- One shift, one day ------------------------------------------------------

/** Highlight an open slot that should be filled when possible (or stop). */
export async function setWanted(key, on) {
  const { taskId, date, position } = parseSlotKey(key);
  try {
    const month = await post('/highlight', { task_id: taskId, date, position, on }, monthOf(date));
    changed(month.history, monthOf(date));
  } catch (error) {
    reportError(error);
  }
}

/** People needed for a task on one day; `count` null goes back to the usual number. */
export async function setHeadcount(taskId, date, count) {
  try {
    const month = await post('/headcount', { task_id: taskId, date, count }, monthOf(date));
    changed(month.history, monthOf(date));
  } catch (error) {
    reportError(error);
  }
}

/** Other times for a task on one day (`times` null: back to the usual times). */
export async function setTime(taskId, date, times) {
  const month = await post('/time', times ? { task_id: taskId, date, ...times } : { task_id: taskId, date, clear: true }, monthOf(date));
  changed(month.history, monthOf(date));
}  // errors go back to the form that called it

/** A short practical note on someone's place (empty: remove it). */
export async function setNote(key, note) {
  const { taskId, date, position } = parseSlotKey(key);
  try {
    const month = await post('/note', { task_id: taskId, date, position, note }, monthOf(date));
    changed(month.history, monthOf(date));
  } catch (error) {
    reportError(error);
  }
}

export async function clearSlot(key) {
  const { taskId, date, position } = parseSlotKey(key);
  try {
    const month = await post('/clear', { task_id: taskId, date, position }, monthOf(date));
    changed(month.history, monthOf(date));
  } catch (error) {
    reportError(error);
  }
}

/** Slots `personId` could take, in the month on screen and the other months loaded for an event. */
export async function loadEligible(personId) {
  if (!personId) return null;
  const ids = [state.month.id, ...Object.keys(state.session.otherMonths)];
  try {
    const results = await Promise.all(ids.map((id) => api.get(monthPath(id, `/people/${encodeURIComponent(personId)}/eligible`))));
    return Object.assign({}, ...results.map((r) => r.slots));
  } catch (error) {
    reportError(error);
    return null;
  }
}

const loadingMonths = new Set();

/** Load the views of other existing months (for an event running into them). */
export async function loadOtherMonths(ids) {
  const known = new Set((state.boot?.months || []).map((m) => m.id));
  const wanted = ids.filter((id) => id !== state.month.id && known.has(id) && !state.session.otherMonths[id] && !loadingMonths.has(id));
  if (!wanted.length) return;
  wanted.forEach((id) => loadingMonths.add(id));
  try {
    const views = await Promise.all(wanted.map((id) => api.get(monthPath(id))));
    setSession({ otherMonths: { ...state.session.otherMonths, ...Object.fromEntries(wanted.map((id, i) => [id, views[i]])) } });
  } catch (error) {
    reportError(error);
  } finally {
    wanted.forEach((id) => loadingMonths.delete(id));
  }
}

/**
 * Create a month (`monthId` "2027-01") with the tasks of `source`
 * ("month:2026-12", "template:…" or "typical"). Events and closures already
 * planned that reach into it come with it. Resolves with the server's answer.
 */
export async function createMonth(monthId, source) {
  needPen();
  const [year, month] = monthId.split('-').map(Number);
  const result = await api.post('/api/months/create', { year, month, source });
  state.boot = result.bootstrap;
  setSession({ newMonth: null });  // the "Next month" card asks again
  return result;
}

export { reportError };
