// The rota of one event as a grid, like the paper rota: tasks down the side,
// grouped by area, one column per day of the event. The same rows and the
// same filling as the Month grid (click a place, arrows, painting, zoom, Fit).
//
// An event can run from the end of one month into the next. Its days are
// shown in one grid, but each day's places belong to (and count in) its own
// month; a month not created yet gets a line offering to create it.
import { formatDate, h } from '../dom.js';
import { changes } from '../components.js';
import { printUrl } from '../api.js';
import { setUi } from '../state.js';
import { buildGrid } from './grid.js';
import { datesBetween, monthTitle } from './range.js';

const SHORT = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' });
const short = (iso) => SHORT.format(new Date(`${iso}T12:00:00`));
const dayOf = (iso) => String(Number(iso.slice(8)));

/** "1–2 Jan" (or "1 Jan"): days of one month. */
function daysText(days) {
  if (days.length === 1) return short(days[0]);
  return `${dayOf(days[0])}–${short(days.at(-1))}`;
}

/** Items (events, markers) of every part, once each, by id. */
function mergedById(lists) {
  const seen = new Map();
  for (const item of lists.flat()) if (!seen.has(item.id)) seen.set(item.id, item);
  return [...seen.values()];
}

/**
 * One view out of the months the event spans (those loaded): its days in order,
 * the areas merged by id (first month's order, then any area only another month
 * has), each with its tasks merged by id (first month's order; a task only in
 * another month goes after the others of its area, next to its other times when
 * it is one time of a task cut into several), and every month's cells (keys are
 * "task|date": no two months share one).
 */
export function combineParts(event, parts) {
  const loaded = parts.filter((p) => p.view);
  const areas = [];
  const byId = new Map();
  for (const part of loaded) {
    for (const area of part.view.areas) {
      const merged = byId.get(area.id);
      if (!merged) {
        const copy = { ...area, tasks: [...area.tasks] };
        byId.set(area.id, copy);
        areas.push(copy);
        continue;
      }
      const known = new Set(merged.tasks.map((t) => t.id));
      for (const task of area.tasks) {
        if (known.has(task.id)) continue;
        // One more time of a task already listed: right after its last time.
        const after = task.pieces ? merged.tasks.map((t) => t.pieces).lastIndexOf(task.pieces) : -1;
        if (after >= 0) merged.tasks.splice(after + 1, 0, task); else merged.tasks.push(task);
        known.add(task.id);
      }
    }
  }
  return {
    id: loaded[0]?.view.id,
    title: event.name,
    days: loaded.flatMap((p) => p.days),
    areas,
    cells: Object.assign({}, ...loaded.map((p) => p.view.cells)),
    events: mergedById(loaded.map((p) => p.view.events)),
    markers: mergedById(loaded.map((p) => p.view.markers)),
  };
}

/** The line above the grid for a month the event reaches that is not on screen yet. */
function partNotice(part, dates, onCreate) {
  const days = dates.filter((d) => d.startsWith(part.id));
  if (part.exists) {
    return h('p', { class: 'event-notice', role: 'status' }, `${daysText(days)}: ${monthTitle(part.id)} is loading…`);
  }
  const button = changes(h('button', { class: 'btn small primary', type: 'button', onclick: async () => {
    button.disabled = true;
    try { await onCreate(part.id); } finally { button.disabled = false; }
  } }, `Create ${monthTitle(part.id)}`));
  return h('p', { class: 'event-notice' },
    h('span', {}, `${daysText(days)} ${days.length > 1 ? 'are' : 'is'} in ${monthTitle(part.id)}, not created yet. Create it to plan these days with the rest of the event.`),
    button);
}

/** What the marks after the names of the event's tasks mean (only the marks that are used). */
function originLegend(month, event) {
  const own = month.areas.flatMap((a) => a.tasks).filter((t) => t.event_id === event.id);
  const has = (test) => own.some((t) => test(t.origin || {}));
  const item = (cls, text) => h('span', {}, h('span', { class: cls, 'aria-hidden': 'true' }), text);
  const items = [
    has((o) => o.kind === 'type' && !o.changed) ? item('origin-mark from-type', 'from its event type') : null,
    has((o) => o.kind === 'type' && o.changed) ? item('origin-mark from-type changed', 'from its type, changed for this event') : null,
    has((o) => o.kind === 'event') ? item('origin-mark from-event', 'only for this event') : null,
  ].filter(Boolean);
  if (!items.length) return null;
  return h('span', { class: 'origin-legend' }, items, h('span', {}, 'no mark: usual task'));
}

/** "164 of 197 places filled" with a thin bar. */
function filledBar(filled, required) {
  const bar = h('span', {
    class: 'progress event-progress', role: 'progressbar', 'aria-label': 'Places filled',
    'aria-valuemin': '0', 'aria-valuemax': String(required), 'aria-valuenow': String(filled),
    'aria-valuetext': `${filled} of ${required} places filled`,
  }, h('span', {}));
  bar.firstChild.style.setProperty('width', `${required ? Math.round((filled / required) * 100) : 100}%`);
  return bar;
}

/**
 * `parts`: one per month the event touches, in order:
 * { id, view (month view or null), exists, days (the event's days in that view), people }.
 * `width`: the room the grid has, in pixels.
 */
export function buildEventView({ event, parts, currentId, ui, session, onCreate, width }) {
  const dates = datesBetween(event.start, event.end);
  const shown = parts.flatMap((p) => p.days);
  const required = shown.reduce((sum, d) => sum + d.required, 0);
  const filled = shown.reduce((sum, d) => sum + d.filled, 0);
  const range = dates.length > 1 ? `${formatDate(dates[0])} – ${formatDate(dates.at(-1))}` : formatDate(dates[0]);
  const ownOnly = Boolean(ui.eventOwnOnly);
  const month = combineParts(event, parts);

  const head = h('header', { class: 'eventview-head' },
    h('div', { class: 'eventview-title' },
      h('h2', {}, event.name),
      h('span', { class: 'muted' }, `${range} · ${dates.length} day${dates.length > 1 ? 's' : ''}`),
      required ? h('span', { class: 'event-filled' }, h('span', { class: 'muted' }, `${filled} of ${required} places filled`), filledBar(filled, required)) : null,
      event.big ? h('span', { class: 'pill event big' }, 'Big event') : null,
      originLegend(month, event)),
    h('div', { class: 'eventview-actions' },
      h('button', { class: 'btn small', type: 'button', 'aria-pressed': ownOnly ? 'true' : 'false', dataset: { focusId: 'event-own-only' },
        title: 'Show only the tasks this event has of its own', onclick: () => setUi({ eventOwnOnly: !ownOnly }) }, 'Only its own tasks'),
      h('button', { class: 'btn small', type: 'button', dataset: { focusId: 'event-print' },
        title: 'The event’s own rota, ready to print (as in Publish › Event rota)',
        onclick: () => window.open(printUrl(currentId, 'event', { event: event.id }), '_blank') }, 'Print…'),
      h('button', { class: 'btn small', type: 'button', dataset: { eventTasks: event.id, focusId: 'event-settings' },
        title: 'Name, dates, type, printed choice and the list of its tasks' }, 'Event settings…')));

  const notices = parts.filter((p) => !p.view).map((part) => partNotice(part, dates, onCreate));
  const peopleOf = new Map(parts.filter((p) => p.view).map((p) => [p.id, p.people]));
  const grid = month.days.length
    ? buildGrid({
      month, visible: month.days, ui, session, width,
      people: peopleOf.get(currentId) || [...peopleOf.values()][0],
      eventMode: { event, ownOnly, currentId, peopleOf: (date) => peopleOf.get(date.slice(0, 7)) },
    })
    : null;

  const view = h('div', { class: 'eventview' }, head, notices, grid);
  // The header and the notices stay in sight when the days scroll sideways.
  view.style.setProperty('--view-w', `${width}px`);
  return view;
}
