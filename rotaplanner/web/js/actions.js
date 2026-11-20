// Every change to the rota goes through here, so feedback (toast with Undo,
// error messages, "assign anyway" confirmation) is the same everywhere.
//
// A shift belongs to the month of its date. The Event view can show days of
// the next (or previous) month: changes to those go to that month, and its
// refreshed view is kept in `state.session.otherMonths`.
import { api, ApiError, monthPath } from './api.js';
import { formatDate } from './dom.js';
import { monthViewFor, peopleById, setMonth, setSession, state } from './state.js';
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

function reportError(error) {
  toast(error instanceof ApiError ? error.message : 'Something went wrong.', { tone: 'bad' });
}

/** Send a change to a month (the one on screen unless another is named) and show the result. */
async function post(rest, body, monthId = state.month.id) {
  const month = await api.post(monthPath(monthId, rest), body);
  if (monthId === state.month.id) {
    setMonth(month);
  } else {
    setSession({ otherMonths: { ...state.session.otherMonths, [monthId]: month } });
  }
  // The highlighted person's possible slots change with every assignment.
  const person = state.session.person;
  if (person) {
    const eligible = await loadEligible(person);
    if (state.session.person === person) setSession({ eligible });
  }
  return month;
}

let lastChanged = null;  // month of the last change made here (an event can show two months)

const viewOf = (monthId) => (monthId === state.month?.id ? state.month : state.session.otherMonths[monthId]);
/** The month Ctrl+Z / Ctrl+Y act on: the one last changed, if it is still on screen. */
const defaultMonth = () => (lastChanged && viewOf(lastChanged) ? lastChanged : state.month?.id);

/** Undo the last change of a month (the one last changed, by default). */
export async function undo(monthId = defaultMonth()) {
  if (!viewOf(monthId)?.history.undo) return;
  try { await post('/undo', {}, monthId); } catch (error) { reportError(error); }
}

export async function redo(monthId = defaultMonth()) {
  if (!viewOf(monthId)?.history.redo) return;
  try { await post('/redo', {}, monthId); } catch (error) { reportError(error); }
}

/** Toast after a change to `monthId`, whose Undo undoes that month's change. */
function done(message, monthId) {
  lastChanged = monthId;
  toast(message, { actionLabel: 'Undo', onAction: () => undo(monthId) });
}

/**
 * Put someone in a slot. `who` is { personId }, or { guestKind: 'volunteer' | 'guest', guest: name or '' }.
 * If a rule is broken and `force` is not set, asks before forcing.
 * Resolves true when the slot was filled.
 */
export async function assign(key, who, { force = false, reason = null } = {}) {
  const { taskId, date, position } = parseSlotKey(key);
  const body = { task_id: taskId, date, position, person_id: who.personId || '', guest: who.guest || '', guest_kind: who.guestKind || '', force, reason };
  try {
    await post('/assign', body, monthOf(date));
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
  const label = who.guest || peopleById().get(who.personId)?.name || (who.guestKind === 'guest' ? 'A guest' : who.guestKind ? 'A volunteer' : 'Someone');
  const task = taskById(taskId, date);
  done(`${label} → ${task ? task.name : 'task'}, ${formatDate(date)}`, monthOf(date));
  return true;
}

// ---- Suggestions -----------------------------------------------------------

/** Ask for suggestions for open shifts: `scope` is {} (whole month), {dates} or {area_id}. */
export async function prefill(scope = {}) {
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
    toast(proposed
      ? `${proposed} suggestions, shown dashed. Check them, then accept${left ? ` · ${left} shifts have nobody free` : ''}.`
      : 'Nobody can be suggested for these shifts.');
  } catch (error) {
    reportError(error);
  }
}

export async function rebalance() {
  try {
    const month = await post('/rebalance');
    toast(month.rebalance.proposed ? `${month.rebalance.proposed} moves suggested to even out hours` : 'Hours are already as even as they can be.');
  } catch (error) {
    reportError(error);
  }
}

/** The month of the first slot key, else the month on screen. */
const monthOfKeys = (keys) => (keys?.length ? monthOf(parseSlotKey(keys[0]).date) : state.month.id);

/** Accept suggestions (all of a month's, or those whose slot keys are given) as one change. */
export async function acceptProposals(keys = null, monthId = monthOfKeys(keys)) {
  try {
    const month = await post('/proposals/accept', keys ? { keys } : {}, monthId);
    done(`${month.accepted} accepted${month.skipped ? ` · ${month.skipped} no longer fitted` : ''}`, monthId);
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

/** Free `personId` for `key` by giving their shift `from` to `replacement` (same day, same month). */
export async function swap(key, personId, from, replacement) {
  const monthId = monthOf(parseSlotKey(key).date);
  try {
    await post('/swap', { key, person_id: personId, from, replacement }, monthId);
    done('Exchange done', monthId);
  } catch (error) {
    reportError(error);
  }
}

// ---- One shift, one day ------------------------------------------------------

/** Highlight an open slot that should be filled when possible (or stop). */
export async function setWanted(key, on) {
  const { taskId, date, position } = parseSlotKey(key);
  try {
    await post('/highlight', { task_id: taskId, date, position, on }, monthOf(date));
    done(on ? 'Highlighted: to fill when possible' : 'Highlight removed', monthOf(date));
  } catch (error) {
    reportError(error);
  }
}

/** People needed for a task on one day; `count` null goes back to the usual number. */
export async function setHeadcount(taskId, date, count) {
  try {
    await post('/headcount', { task_id: taskId, date, count }, monthOf(date));
    done(count === 0 ? 'Not needed that day' : count === null ? 'Back to the usual number' : `${count} needed that day`, monthOf(date));
  } catch (error) {
    reportError(error);
  }
}

/** Other times for a task on one day (`times` null: back to the usual times). */
export async function setTime(taskId, date, times) {
  await post('/time', times ? { task_id: taskId, date, ...times } : { task_id: taskId, date, clear: true }, monthOf(date));
  done(times ? 'Time changed for that day' : 'Back to the usual time', monthOf(date));
}  // errors go back to the form that called it

/** A short practical note on someone's place (empty: remove it). */
export async function setNote(key, note) {
  const { taskId, date, position } = parseSlotKey(key);
  try {
    await post('/note', { task_id: taskId, date, position, note }, monthOf(date));
    done(note ? 'Note saved' : 'Note removed', monthOf(date));
  } catch (error) {
    reportError(error);
  }
}

export async function clearSlot(key) {
  const { taskId, date, position } = parseSlotKey(key);
  try {
    await post('/clear', { task_id: taskId, date, position }, monthOf(date));
    done('Shift left open', monthOf(date));
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
  const [year, month] = monthId.split('-').map(Number);
  const result = await api.post('/api/months/create', { year, month, source });
  state.boot = result.bootstrap;
  setSession({ newMonth: null });  // the "Next month" card asks again
  return result;
}

export { reportError };
