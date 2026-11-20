// The rota grid: tasks down the side, days across, one chip per position.
import { clearSlot, setWanted } from '../actions.js';
import { h } from '../dom.js';
import { icon } from '../icons.js';
import { cellChips } from './chips.js';
import { applyZoom } from './range.js';

const WEEKDAY_INITIAL = { Mon: 'M', Tue: 'T', Wed: 'W', Thu: 'T', Fri: 'F', Sat: 'S', Sun: 'S' };

/** Spans (events and markers) intersecting the visible days, packed into lanes. */
function bandLanes(month, visible) {
  const index = new Map(visible.map((d, i) => [d.date, i]));
  const first = visible[0].date;
  const last = visible[visible.length - 1].date;
  const items = [
    ...month.events.map((e) => ({ ...e, kind: e.big ? 'event big' : 'event', label: e.name, event: e.id })),
    ...month.markers.map((m) => ({ ...m, label: m.label })),
  ]
    .filter((item) => item.end >= first && item.start <= last)
    .map((item) => ({
      ...item,
      from: index.get(item.start < first ? first : item.start),
      to: index.get(item.end > last ? last : item.end),
      before: item.start < first,  // goes on before the first day shown (maybe in the previous month)
      after: item.end > last,
    }))
    .sort((a, b) => a.from - b.from);
  const lanes = [];
  for (const item of items) {
    const lane = lanes.find((l) => l[l.length - 1].to < item.from);
    if (lane) lane.push(item); else lanes.push([item]);
  }
  return lanes;
}

const KIND_WORD = { event: 'Event', 'event big': 'Big event', closed: 'Closed', offrota: 'Off-rota', note: 'Note' };
const SHORT = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' });
const short = (iso) => SHORT.format(new Date(`${iso}T12:00:00`));

function bandRows(month, visible) {
  return bandLanes(month, visible).map((lane, laneIndex) => {
    const row = h('tr', { class: 'band' },
      h('th', { class: 'sticky lane-label', scope: 'row' }, laneIndex === 0 ? 'Events & notes' : ''));
    let col = 0;
    for (const item of lane) {
      if (item.from > col) row.append(h('td', { colspan: item.from - col }));
      const dates = item.start === item.end ? short(item.start) : `${short(item.start)} – ${short(item.end)}`;
      row.append(h('td', { colspan: item.to - item.from + 1 },
        h('span', {
          class: ['pill', item.kind, item.event && 'clickable'], dataset: item.event ? { event: item.event } : {},
          title: `${KIND_WORD[item.kind] || ''}: ${item.label} · ${dates}${item.event ? ' · click to open it in the Event view' : ''}`,
        }, item.before ? '‹ ' : '', item.label, item.after ? ' ›' : '')));
      col = item.to + 1;
    }
    if (col < visible.length) row.append(h('td', { colspan: visible.length - col }));
    return row;
  });
}

function dayHeaders(visible, eventDays) {
  return h('tr', {},
    h('th', { class: 'sticky corner', scope: 'col' }, 'Task'),
    visible.map((day) => h('th', {
      class: ['day-head', day.weekend && 'weekend weekend-col', eventDays.has(day.date) && 'event'],
      scope: 'col', dataset: { date: day.date }, title: 'Open this day', tabindex: '0', role: 'button',
      onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.click(); } },
    },
    h('span', { class: 'wd' }, h('span', { class: 'wd-long' }, day.weekday), h('span', { class: 'wd-short' }, WEEKDAY_INITIAL[day.weekday])),
    h('span', { class: 'num' }, day.day))));
}

function cellStats(cell) {
  const filled = cell.slots.filter((s, i) => !s.open && i < cell.required).length;
  return { required: cell.required, filled };
}

/** Does this cell pass the "open only" / "highlighted only" filter? */
export function matchesFilter(cell, filter) {
  if (filter === 'wanted') return cell.slots.some((s) => s.open && s.wanted);
  if (filter === 'open') return cell.slots.some((s) => s.open);
  return true;
}

/** Rows to show for an area, after the "empty rows" and open/highlighted filters. */
function visibleTasks(area, month, visible, ui, session) {
  return area.tasks.filter((task) => {
    if (!task.on_main_rota && ui.hideEventOnly) return false;  // on its event's rota only, hidden on request
    const cells = visible.map((d) => month.cells[`${task.id}|${d.date}`]).filter(Boolean);
    if (session.openOnly) return cells.some((c) => matchesFilter(c, session.openOnly));
    return ui.showEmpty || cells.some((c) => c.slots.length || c.closed || c.adjusted);
  });
}

/** The event a task belongs to: a link to that event and its tasks. */
export function eventTag(task) {
  return h('button', { class: 'event-tag', type: 'button', dataset: { eventTasks: task.event_id },
    title: task.on_main_rota ? `A task of ${task.event}. Click to change the event’s tasks.`
      : `A task of ${task.event}: to fill here, but not printed on the main rota (only on the event’s printout). Click to change this.` },
  task.event, task.on_main_rota ? null : h('span', { class: 'event-only-note' }, ' · not on printed rota'), ' · ');
}

function areaRows(area, month, visible, ui, session, people) {
  const tasks = visibleTasks(area, month, visible, ui, session);
  if (!tasks.length) return [];
  const collapsed = Boolean(ui.collapsed[area.id]);

  let required = 0;
  let filled = 0;
  for (const task of tasks) {
    for (const day of visible) {
      const cell = month.cells[`${task.id}|${day.date}`];
      if (!cell) continue;
      const stats = cellStats(cell);
      required += stats.required;
      filled += stats.filled;
    }
  }
  const open = required - filled;

  const rows = [h('tr', { class: ['area-row', `area-${area.color}`, collapsed && 'collapsed'] },
    h('th', { class: 'sticky', scope: 'rowgroup' },
      h('button', { class: 'area-head', dataset: { area: area.id }, 'aria-expanded': String(!collapsed) },
        icon('down', 'chev'),
        h('span', { class: 'area-name' }, area.name),
        h('span', { class: ['area-count', open > 0 && 'has-open'] },
          open > 0 ? `${open} open` : `${filled}/${required}`))),
    h('td', { colspan: visible.length }))];
  if (collapsed) return rows;

  for (const task of tasks) {
    const row = h('tr', { class: [`area-${area.color}`, !task.on_main_rota && 'event-only'] },
      h('th', { class: 'sticky task-label', scope: 'row', dataset: { task: task.id } },
        h('span', { class: 'task-name' }, task.name),
        h('span', { class: 'task-meta' },
          task.event ? eventTag(task) : null,
          task.priority === 'critical' ? h('span', { class: 'prio' }, 'Essential · ') : null,
          [task.time, task.hours ? `${task.hours} h` : null].filter(Boolean).join(' · '))));
    visible.forEach((day, i) => {
      const cell = month.cells[`${task.id}|${day.date}`];
      const td = h('td', { class: ['cell', day.weekend && 'weekend-col', cell?.closed && 'closed', cell?.time && 'retimed'] });
      const chips = cellChips(cell, {
        taskId: task.id, date: day.date, col: i, priority: task.priority, showEmpty: ui.showEmpty,
        people, eligible: session.eligible, highlight: session.person,
      });
      if (chips.length) td.append(h('div', { class: 'stack' }, chips));
      if (cell?.closed) td.title = cell.closed;
      if (cell?.time) td.title = `Other time this day: ${cell.time}`;
      row.append(td);
    });
    rows.push(row);
  }
  return rows;
}

export function buildGrid({ month, visible, ui, session, people, width }) {
  const eventDays = new Set(month.days.filter((d) => d.day_type !== 'normal').map((d) => d.date));
  const table = h('table', { class: ['grid', session.paint && 'painting', !ui.showTimes && 'no-times'], role: 'grid', 'aria-label': `Rota for ${month.title}` },
    h('colgroup', {}, h('col', { class: 'c-label' }), visible.map(() => h('col', { class: 'c-day' }))),
    h('thead', {}, bandRows(month, visible), dayHeaders(visible, eventDays)));
  applyZoom(table, session.zoom, visible.length, width);

  const areas = session.area ? month.areas.filter((a) => a.id === session.area) : month.areas;
  for (const area of areas) {
    const rows = areaRows(area, month, visible, ui, session, people);
    if (rows.length) table.append(h('tbody', {}, rows));
  }
  if (!table.querySelector('tbody')) {
    table.append(h('tbody', {}, h('tr', {}, h('td', { colspan: visible.length + 1 },
      h('p', { class: 'empty' }, session.openOnly ? 'No open shifts here. 🎉' : 'Nothing to staff on these days.')))));
  }
  const firstChip = table.querySelector('.chip');
  if (firstChip) firstChip.tabIndex = 0;
  return table;
}

// ---- Keyboard: arrows move between chips, Delete empties a slot. --------

function chipsInColumn(table, col) {
  return [...table.querySelectorAll(`.chip[data-col="${col}"]`)];
}

function neighbour(table, chip, key) {
  const col = Number(chip.dataset.col);
  if (key === 'ArrowUp' || key === 'ArrowDown') {
    const column = chipsInColumn(table, col);
    return column[column.indexOf(chip) + (key === 'ArrowDown' ? 1 : -1)];
  }
  const row = chip.closest('tr');
  const step = key === 'ArrowRight' ? 1 : -1;
  const cells = [...row.querySelectorAll('td.cell')];
  for (let c = col + step; c >= 0 && c < cells.length; c += step) {
    const chips = [...cells[c].querySelectorAll('.chip')];
    if (chips.length) return chips.find((x) => x.dataset.pos === chip.dataset.pos) || chips[chips.length - 1];
  }
  return null;
}

export function moveFocus(from, to) {
  if (!to) return;
  from.tabIndex = -1;
  to.tabIndex = 0;
  to.focus();
}

export function onGridKey(event, table) {
  const chip = event.target.closest?.('.chip');
  if (!chip) return;
  const isOpen = chip.classList.contains('open');
  if (event.key.startsWith('Arrow')) {
    event.preventDefault();
    moveFocus(chip, neighbour(table, chip, event.key));
  } else if ((event.key === 'Delete' || event.key === 'Backspace') && chip.dataset.person && !isOpen && !chip.classList.contains('proposed')) {
    event.preventDefault();
    clearSlot(chip.dataset.key);
  } else if (event.key.toLowerCase() === 'h' && isOpen && !event.ctrlKey && !event.metaKey) {
    event.preventDefault();
    setWanted(chip.dataset.key, !chip.classList.contains('wanted'));
  }
}
