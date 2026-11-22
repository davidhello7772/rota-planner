// The rota grid: tasks down the side, days across, one chip per position.
import { clearSlot, setWanted } from '../actions.js';
import { formatHours, h } from '../dom.js';
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

/** `opens(day)`: does clicking the day open it in the Day view (a day of another month does not). */
function dayHeaders(visible, eventDays, opens = () => true) {
  return h('tr', {},
    h('th', { class: 'sticky corner', scope: 'col' }, 'Task'),
    visible.map((day) => h('th', {
      class: ['day-head', day.weekend && 'weekend weekend-col', eventDays.has(day.date) && 'event', !opens(day) && 'no-open'],
      scope: 'col', dataset: { date: day.date },
      ...(opens(day) ? {
        title: 'Open this day', tabindex: '0', role: 'button',
        onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.click(); } },
      } : { title: 'A day of another month' }),
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

/** Is the task one of the event's own, in the Event view (`mode`: the grid's `eventMode`)? */
const isOwn = (task, mode) => Boolean(mode && task.event_id === mode.event.id);

/** Rows to show for an area, after the "empty rows" and open/highlighted filters.
 *  In the Event view every task to fill is shown (or only the event's own, on request). */
function visibleTasks(area, month, visible, ui, session, mode) {
  return area.tasks.filter((task) => {
    if (mode ? mode.ownOnly && !isOwn(task, mode) : !task.on_main_rota && ui.hideEventOnly) return false;  // on its event's rota only, hidden on request
    const cells = visible.map((d) => month.cells[`${task.id}|${d.date}`]).filter(Boolean);
    if (session.openOnly) return cells.some((c) => matchesFilter(c, session.openOnly));
    return ui.showEmpty || cells.some((c) => c.slots.length || c.closed || c.adjusted);
  });
}

/** How many people the task usually needs, after its name: "1", or "1–2" when it varies by weekday.
 *  Its words (`need.title`) are in the task's hover card. */
function needMark(need) {
  if (!need?.text) return null;
  return h('span', { class: 'task-need', role: 'img', 'aria-label': need.title }, icon('users'), need.text);
}

/** Its time and hours: "10:00–12:00 · 2 h". */
export function timeAndHours(task) {
  return [task.time, task.hours ? formatHours(task.hours) : null].filter(Boolean).join(' · ');
}

/** Is `task` one more time of the task shown just before it (a task cut into several times)?
 *  Then it shows only its time, under the name shown once on the first. */
export function followsPiece(task, previous) {
  return Boolean(task.pieces && previous && previous.pieces === task.pieces);
}

/** In the Event view, the event's own task: its name opens its form; a mark of where it comes from instead of the event's tag. */
function editButton(task, text, label, cls) {
  return h('button', { class: [cls, 'task-edit'], type: 'button', 'aria-label': label,
    dataset: { editTask: task.id, focusId: `edit-task-${task.id}` } }, text);
}
/** Where an event's task comes from, after its name: a round mark for its event type's (a ring when
 *  changed for this event), a diamond for this event's only. The words come from the program. */
const originMark = (task) => {
  const origin = task.origin || {};
  const kind = origin.kind === 'type' ? ['origin-mark', 'from-type', origin.changed && 'changed'] : ['origin-mark', 'from-event'];
  return h('span', { class: kind, role: 'img', 'aria-label': origin.text || '', title: origin.text || '' });
};

/** A task's label: its name, need, event, Essential, time and hours. A task in several times
 *  starts with its time, kept when times are hidden: it is what tells its rows apart.
 *  `own`: one of the event's own tasks, in its Event view. */
function taskLabel(task, own = false) {
  const critical = task.priority === 'critical';
  const tagged = task.event && !own;
  return h('th', { class: 'sticky task-label', scope: 'row', dataset: { task: task.id } },
    h('span', { class: 'task-name' },
      own ? editButton(task, task.name, `Change ${task.name}`, 'task-name-text') : h('span', { class: 'task-name-text' }, task.name),
      own ? originMark(task) : null, needMark(task.need)),
    task.pieces
      ? h('span', { class: 'task-meta has-piece' },
        h('span', { class: 'piece-time' }, timeAndHours(task)),
        tagged || critical ? h('span', {}, tagged ? [' · ', eventTag(task, '')] : null,
          critical ? h('span', { class: 'prio' }, ' · Essential') : null) : null)
      : h('span', { class: 'task-meta' },
        tagged ? eventTag(task) : null,
        critical ? h('span', { class: 'prio' }, 'Essential · ') : null,
        timeAndHours(task)));
}

/** One more time of the task above: its time only, indented and joined to the row above. */
function pieceLabel(task, own = false) {
  const time = timeAndHours(task);
  return h('th', {
    class: 'sticky task-label piece-next', scope: 'row', dataset: { task: task.id },
    'aria-label': [task.name, time, task.need?.title].filter(Boolean).join(', '),
  },
  h('span', { class: 'task-name' },
    own ? editButton(task, time, `Change ${task.name}${time ? ` (${time})` : ''}`, 'task-name-text piece-time')
      : h('span', { class: 'task-name-text piece-time' }, time),
    needMark(task.need)));
}

/** Is this cell a day the task needs nobody (no place, not closed, not changed for the day)? */
const notNeeded = (cell) => !cell || (!cell.slots.length && !cell.closed && !cell.adjusted);

/** The quiet last row of an area in the Event view: add a task of the event there. */
function addRow(areaId, text, color, dayCount) {
  return h('tr', { class: ['add-row', color && `area-${color}`] },
    h('th', { class: 'sticky add-label', scope: 'row' },
      h('button', { class: 'add-task', type: 'button', dataset: { addTask: areaId, focusId: `add-task-${areaId}` } },
        icon('plus'), h('span', {}, text))),
    h('td', { colspan: dayCount }));
}

/** The event a task belongs to: a link to that event and its tasks. `after`: what follows it. */
export function eventTag(task, after = ' · ') {
  return h('button', { class: 'event-tag', type: 'button', dataset: { eventTasks: task.event_id },
    title: task.on_main_rota ? `A task of ${task.event}. Click to change the event’s tasks.`
      : `A task of ${task.event}: to fill here, but not printed on the main rota (only on the event’s printout). Click to change this.` },
  task.event, task.on_main_rota ? null : h('span', { class: 'event-only-note' }, ' · not on printed rota'), after);
}

function areaRows(area, month, visible, ui, session, people, mode = null) {
  const tasks = visibleTasks(area, month, visible, ui, session, mode);
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
        h('span', { class: 'area-name' }, area.name, mode && area.manager ? h('span', { class: 'area-manager' }, ` · ${area.manager}`) : null),
        h('span', { class: ['area-count', open > 0 && 'has-open'] },
          open > 0 ? `${open} open` : `${filled}/${required}`))),
    h('td', { colspan: visible.length }))];
  if (collapsed) return rows;

  let previous = null;
  let previousLabel = null;
  for (const task of tasks) {
    const own = isOwn(task, mode);
    const follows = followsPiece(task, previous);
    const label = follows ? pieceLabel(task, own) : taskLabel(task, own);
    if (follows) previousLabel.classList.add('piece-joined');  // no line between the times of one task
    previous = task;
    previousLabel = label;
    // In its own Event view, an event's task needs no "not on the printed rota" stripes: the form says it.
    const row = h('tr', { class: [`area-${area.color}`, !task.on_main_rota && !own && 'event-only'] }, label);
    visible.forEach((day, i) => {
      const cell = month.cells[`${task.id}|${day.date}`];
      const td = h('td', { class: ['cell', day.weekend && 'weekend-col', cell?.closed && 'closed', cell?.time && 'retimed',
        mode && notNeeded(cell) && 'not-needed'] });
      const chips = cellChips(cell, {
        taskId: task.id, date: day.date, week: day.week, col: i, priority: task.priority, showEmpty: ui.showEmpty,
        people: mode ? mode.peopleOf(day.date) || people : people, eligible: session.eligible, highlight: session.person,
      });
      if (chips.length) td.append(h('div', { class: 'stack' }, chips));
      // A day that needs another number than usual: no mark (its places show it), only the words on hover.
      const title = [cell?.closed, cell?.need?.title, cell?.time && `Other time this day: ${cell.time}`].filter(Boolean);
      if (!title.length && mode && notNeeded(cell)) title.push('Not needed this day');
      if (title.length) td.title = title.join('\n');
      row.append(td);
    });
    rows.push(row);
  }
  if (mode) rows.push(addRow(area.id, `Add a task to ${area.name}`, area.color, visible.length));
  return rows;
}

/**
 * `eventMode` (the Event view only): `{ event, ownOnly, currentId, peopleOf(date) }`. Days a
 * task is not needed are shaded; the event's own tasks get a dot and a name that opens their
 * form; each area ends with "+ Add a task to …"; `ownOnly` shows only the event's own tasks;
 * a day of another month (`currentId`: the month on screen) does not open the Day view;
 * `peopleOf(date)`: the people of that day's month.
 */
export function buildGrid({ month, visible, ui, session, people, width, eventMode = null }) {
  const mode = eventMode;
  const eventDays = new Set(month.days.filter((d) => d.day_type !== 'normal').map((d) => d.date));
  const opens = mode ? (day) => day.date.startsWith(mode.currentId) : undefined;
  const table = h('table', { class: ['grid', session.paint && 'painting', !ui.showTimes && 'no-times', mode && 'event-grid'], role: 'grid', 'aria-label': `Rota for ${month.title}` },
    h('colgroup', {}, h('col', { class: 'c-label' }), visible.map(() => h('col', { class: 'c-day' }))),
    h('thead', {}, bandRows(month, visible), dayHeaders(visible, eventDays, opens)));
  applyZoom(table, session.zoom, visible.length, width);

  const areas = session.area ? month.areas.filter((a) => a.id === session.area) : month.areas;
  for (const area of areas) {
    const rows = areaRows(area, month, visible, ui, session, people, mode);
    if (rows.length) table.append(h('tbody', {}, rows));
  }
  const empty = !table.querySelector('tbody');
  if (empty) {
    const text = session.openOnly ? 'No open shifts here. 🎉'
      : mode?.ownOnly ? 'This event has no task of its own on these days yet.' : 'Nothing to staff on these days.';
    table.append(h('tbody', {}, h('tr', {}, h('td', { colspan: visible.length + 1 }, h('p', { class: 'empty' }, text)))));
  }
  // After the last area: the areas with nothing on these days.
  if (mode) table.append(h('tbody', {}, addRow('', empty ? 'Add a task to this event…' : 'Add a task in another area…', null, visible.length)));
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
