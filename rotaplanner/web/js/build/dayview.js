// One day as a vertical list grouped by area: handy for reviewing a single
// busy day.
import { formatHours, h } from '../dom.js';
import { icon } from '../icons.js';
import { cellChips } from './chips.js';
import { eventTag, followsPiece, matchesFilter } from './grid.js';

const LONG = new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long' });

/** How many people the task needs that day, after its name: "2 needed" when that is not its usual number. */
function dayNeed(task, cell) {
  if (cell.need) {
    return h('span', { class: 'task-need own', title: cell.need.title }, icon('users'), cell.need.count ? `${cell.need.count} needed` : 'Not needed');
  }
  if (!cell.required) return null;
  return h('span', { class: 'task-need', title: task.need?.title || undefined }, icon('users'), String(cell.required));
}

export function buildDayView({ month, day, ui, session, people }) {
  const items = [
    ...month.events.filter((e) => e.start <= day.date && day.date <= e.end).map((e) => ({ kind: e.big ? 'event big' : 'event', label: e.name })),
    ...month.markers.filter((m) => m.start <= day.date && day.date <= m.end).map((m) => ({ kind: m.kind, label: m.label })),
  ];
  const areas = session.area ? month.areas.filter((a) => a.id === session.area) : month.areas;

  const cards = areas.map((area) => {
    let previous = {};  // the task shown just before, and its row
    const rows = area.tasks.map((task) => {
      const cell = month.cells[`${task.id}|${day.date}`];
      if (!cell || (!cell.slots.length && !cell.closed && !cell.adjusted)) return null;
      if (!task.on_main_rota && ui.hideEventOnly) return null;
      if (session.openOnly && !matchesFilter(cell, session.openOnly)) return null;
      // One more time of the task above (a task cut into several times): its time only, joined to it.
      const follows = followsPiece(task, previous.task);
      const time = h('span', { class: ['task-meta', cell.time && 'retimed-note', task.pieces && 'piece-time'] },
        cell.time ? `◷ ${cell.time} (other time this day)` : [task.time, task.hours ? formatHours(task.hours) : null].filter(Boolean).join(' · '));
      if (follows) previous.row.classList.add('piece-joined');
      const row = h('div', { class: ['day-task', cell.closed && 'closed', follows && 'piece-next'], title: cell.closed || undefined },
        follows
          ? h('div', {}, h('span', { class: 'visually-hidden' }, `${task.name}, `), time, dayNeed(task, cell))
          : h('div', {},
            h('span', { class: 'task-name' }, task.name, dayNeed(task, cell)),
            task.event ? [' · ', eventTag(task)] : null,
            time),
        h('div', { class: 'chips' },
          cell.closed && !cell.slots.length ? h('span', { class: 'faint' }, cell.closed) : null,
          cellChips(cell, {
            taskId: task.id, date: day.date, priority: task.priority,
            people, eligible: session.eligible, highlight: session.person,
          })));
      previous = { task, row };
      return row;
    }).filter(Boolean);
    if (!rows.length) return null;
    return h('section', { class: ['day-card', `area-${area.color}`] },
      h('div', { class: 'area-head' }, h('span', { class: 'area-name' }, area.name),
        area.manager ? h('span', { class: 'area-count' }, area.manager) : null),
      rows);
  }).filter(Boolean);

  const view = h('div', { class: ['dayview', !ui.showTimes && 'no-times'] },
    h('div', { class: 'dayview-head' },
      h('h2', {}, LONG.format(new Date(`${day.date}T12:00:00`))),
      h('span', { class: 'muted' }, day.required ? `${day.filled} of ${day.required} shifts filled` : 'Nothing to staff'),
      items.map((item) => h('span', { class: ['pill', item.kind] }, item.label))),
    cards.length ? h('div', { class: 'day-cards' }, cards) : h('p', { class: 'empty' }, 'Nothing to show for this day.'));
  view.querySelectorAll('.chip').forEach((chip) => chip.setAttribute('tabindex', '0'));  // a list: Tab goes through every shift
  return view;
}
