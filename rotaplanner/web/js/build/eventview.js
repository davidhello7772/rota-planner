// The rota of one event, day after day, each day grouped by area: the
// vertical layout used for big events.
//
// An event can run from the end of one month into the next. Its days are
// shown together, but each day's shifts belong to (and count in) its own
// month; a month not created yet gets a card offering to create it.
import { formatDate, h } from '../dom.js';
import { icon } from '../icons.js';
import { buildDayView } from './dayview.js';
import { datesBetween, monthTitle } from './range.js';
import { changes } from '../components.js';

const SHORT = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' });
const short = (iso) => SHORT.format(new Date(`${iso}T12:00:00`));

/**
 * `parts`: one per month the event touches, in order:
 * { id, view (month view or null), exists, days (the event's days in that view) }.
 */
export function buildEventView({ event, parts, currentId, ui, session, onCreate }) {
  const dates = datesBetween(event.start, event.end);
  const shown = parts.flatMap((p) => p.days);
  const required = shown.reduce((sum, d) => sum + d.required, 0);
  const filled = shown.reduce((sum, d) => sum + d.filled, 0);
  const range = dates.length > 1 ? `${formatDate(dates[0])} – ${formatDate(dates.at(-1))}` : formatDate(dates[0]);
  const overMonths = parts.length > 1;

  return h('div', { class: 'eventview' },
    h('header', { class: 'eventview-head' },
      h('h2', {}, event.name),
      h('span', { class: 'muted' }, `${range} · ${dates.length} day${dates.length > 1 ? 's' : ''} · ${filled} of ${required} shifts filled`),
      event.big ? h('span', { class: 'pill event big' }, 'Big event') : null,
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn small', type: 'button', dataset: { eventTasks: event.id } }, 'Event & its tasks…'),
      h('button', { class: 'btn small', type: 'button', dataset: { eventTasks: event.id, newTask: '1' } }, icon('plus'), 'Task for this event'),
      overMonths ? h('span', { class: 'faint' }, `Over ${parts.map((p) => monthTitle(p.id)).join(' and ')}: each day counts in its own month's hours`) : null),
    parts.map((part) => [
      overMonths ? h('div', { class: 'month-divider' },
        h('span', {}, monthTitle(part.id)),
        part.id !== currentId && part.view ? h('span', { class: 'faint' }, 'changes here are saved in that month') : null) : null,
      part.view
        ? part.days.map((day) => buildDayView({ month: part.view, day, ui, session, people: part.people, allTasks: true }))
        : part.exists ? h('p', { class: 'empty' }, 'Loading…') : missingMonth(part, dates, onCreate),
    ]));
}

function missingMonth(part, dates, onCreate) {
  const days = dates.filter((d) => d.startsWith(part.id));
  const which = days.length > 1 ? `${short(days[0])} – ${short(days.at(-1))}` : short(days[0]);
  const button = changes(h('button', { class: 'btn primary', type: 'button', onclick: async () => {
    button.disabled = true;
    try { await onCreate(part.id); } finally { button.disabled = false; }
  } }, `Create ${monthTitle(part.id)}`));
  return h('section', { class: 'card missing-month' },
    h('h3', {}, `${which}: ${monthTitle(part.id)} is not created yet`),
    h('p', { class: 'muted' }, 'Create it now to plan these days with the rest of the event. It starts with the same tasks as this month and the regular week; availability can be added later.'),
    button);
}
