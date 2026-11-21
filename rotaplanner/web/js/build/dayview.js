// One day as a vertical list grouped by area: the layout used for big
// events, and handy for reviewing a single busy day.
import { formatHours, h } from '../dom.js';
import { icon } from '../icons.js';
import { cellChips } from './chips.js';
import { eventTag, matchesFilter } from './grid.js';

const LONG = new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long' });

/** How many people the task needs that day, after its name: "2 needed" when that is not its usual number. */
function dayNeed(task, cell) {
  if (cell.need) {
    return h('span', { class: 'task-need own', title: cell.need.title }, icon('users'), cell.need.count ? `${cell.need.count} needed` : 'Not needed');
  }
  if (!cell.required) return null;
  return h('span', { class: 'task-need', title: task.need?.title || undefined }, icon('users'), String(cell.required));
}

/** `allTasks`: also the tasks kept on an event's own rota (true in the Event view). */
export function buildDayView({ month, day, ui, session, people, allTasks = false }) {
  const items = [
    ...month.events.filter((e) => e.start <= day.date && day.date <= e.end).map((e) => ({ kind: e.big ? 'event big' : 'event', label: e.name })),
    ...month.markers.filter((m) => m.start <= day.date && day.date <= m.end).map((m) => ({ kind: m.kind, label: m.label })),
  ];
  const areas = session.area ? month.areas.filter((a) => a.id === session.area) : month.areas;

  const cards = areas.map((area) => {
    const rows = area.tasks.map((task) => {
      const cell = month.cells[`${task.id}|${day.date}`];
      if (!cell || (!cell.slots.length && !cell.closed && !cell.adjusted)) return null;
      if (!task.on_main_rota && !allTasks && ui.hideEventOnly) return null;
      if (session.openOnly && !matchesFilter(cell, session.openOnly)) return null;
      return h('div', { class: ['day-task', cell.closed && 'closed'], title: cell.closed || undefined },
        h('div', {},
          h('span', { class: 'task-name' }, task.name, dayNeed(task, cell)),
          task.event ? [' · ', eventTag(task)] : null,
          h('span', { class: ['task-meta', cell.time && 'retimed-note'] },
            cell.time ? `◷ ${cell.time} (other time this day)` : [task.time, task.hours ? formatHours(task.hours) : null].filter(Boolean).join(' · '))),
        h('div', { class: 'chips' },
          cell.closed && !cell.slots.length ? h('span', { class: 'faint' }, cell.closed) : null,
          cellChips(cell, {
            taskId: task.id, date: day.date, priority: task.priority,
            people, eligible: session.eligible, highlight: session.person,
          })));
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
