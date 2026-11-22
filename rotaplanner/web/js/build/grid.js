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

/** A task's label: its name, need, event, Essential, time and hours. A task in several times
 *  starts with its time, kept when times are hidden: it is what tells its rows apart. */
function taskLabel(task) {
  const critical = task.priority === 'critical';
  return h('th', { class: 'sticky task-label', scope: 'row', dataset: { task: task.id } },
    h('span', { class: 'task-name' }, h('span', { class: 'task-name-text' }, task.name), needMark(task.need)),
    task.pieces
      ? h('span', { class: 'task-meta has-piece' },
        h('span', { class: 'piece-time' }, timeAndHours(task)),
        task.event || critical ? h('span', {}, task.event ? [' · ', eventTag(task, '')] : null,
          critical ? h('span', { class: 'prio' }, ' · Essential') : null) : null)
      : h('span', { class: 'task-meta' },
        task.event ? eventTag(task) : null,
        critical ? h('span', { class: 'prio' }, 'Essential · ') : null,
        timeAndHours(task)));
}

/** One more time of the task above: its time only, indented and joined to the row above. */
function pieceLabel(task) {
  return h('th', {
    class: 'sticky task-label piece-next', scope: 'row', dataset: { task: task.id },
    'aria-label': [task.name, timeAndHours(task), task.need?.title].filter(Boolean).join(', '),
  },
  h('span', { class: 'task-name' }, h('span', { class: 'task-name-text piece-time' }, timeAndHours(task)), needMark(task.need)));
}

/** The event a task belongs to: a link to that event and its tasks. `after`: what follows it. */
export function eventTag(task, after = ' · ') {
  return h('button', { class: 'event-tag', type: 'button', dataset: { eventTasks: task.event_id },
    title: task.on_main_rota ? `A task of ${task.event}. Click to change the event’s tasks.`
      : `A task of ${task.event}: to fill here, but not printed on the main rota (only on the event’s printout). Click to change this.` },
  task.event, task.on_main_rota ? null : h('span', { class: 'event-only-note' }, ' · not on printed rota'), after);
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

  let previous = null;
  let previousLabel = null;
  for (const task of tasks) {
    const follows = followsPiece(task, previous);
    const label = follows ? pieceLabel(task) : taskLabel(task);
    if (follows) previousLabel.classList.add('piece-joined');  // no line between the times of one task
    previous = task;
    previousLabel = label;
    const row = h('tr', { class: [`area-${area.color}`, !task.on_main_rota && 'event-only'] }, label);
    visible.forEach((day, i) => {
      const cell = month.cells[`${task.id}|${day.date}`];
      const td = h('td', { class: ['cell', day.weekend && 'weekend-col', cell?.closed && 'closed', cell?.time && 'retimed'] });
      const chips = cellChips(cell, {
        taskId: task.id, date: day.date, col: i, priority: task.priority, showEmpty: ui.showEmpty,
        people, eligible: session.eligible, highlight: session.person,
      });
      if (chips.length) td.append(h('div', { class: 'stack' }, chips));
      // A day that needs another number than usual: no mark (its places show it), only the words on hover.
      const title = [cell?.closed, cell?.need?.title, cell?.time && `Other time this day: ${cell.time}`].filter(Boolean);
      if (title.length) td.title = title.join('\n');
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
