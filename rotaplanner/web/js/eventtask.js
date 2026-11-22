// A task of an event, or of an event type: the line with its quick controls
// (people, which days, printed on the main rota) and the drawer with all its
// details. Used by the event dialog (Set up) and by Tasks & Areas › Event types.
//
// Two rotas, two words: the rota to FILL (Build) always has every task; the
// PRINTED main rota has an event's tasks only if the event says so, and a
// task can answer differently from the rest of its event. The event's own
// printout always has them all.
import { changes, field, formError, numberInput, openDrawer, segmented, select, textInput } from './components.js';
import { fill, h } from './dom.js';
import { icon } from './icons.js';
import { errorText } from './setup.js';

/** Is the task printed on the main rota: like the rest of its event, or yes / no for itself. */
const ownChoice = (task) => (task.on_month_rota === true ? 'yes' : task.on_month_rota === false ? 'no' : 'follow');
const OWN_VALUE = { follow: null, yes: true, no: false };

function printOptions(followsYes) {
  return [
    { value: 'follow', label: `Like the event (${followsYes ? 'yes' : 'no'})` },
    { value: 'yes', label: 'Yes' },
    { value: 'no', label: 'No' },
  ];
}

const PRINT_HELP = 'Every task is on the rota to fill (Build) and on the event’s own printout. This only decides the printed main rota.';

/** A control with its name written small above it, so a line of controls reads by itself. */
const captioned = (caption, control) => h('span', { class: 'mini-field' }, h('span', { class: 'mini-label' }, caption), control);

/** Is the task cut into several times (each filled on its own). */
const isCut = (task) => (task.times?.length || 1) > 1;

/** "14:00–17:00 (2) · 17:00–19:00 (1)": the times of a task in several, each with its people. */
const timesText = (task) => task.times.map((t) => `${t.time || 'any time'} (${t.people})`).join(' · ');

/** What the server needs to save a task, from the task as shown plus what changed.
    A task in several times sends them all, so that every time is kept. */
export function taskFields(task, patch = {}) {
  const t = { ...task, ...patch };
  const fields = { id: t.id, name: t.name, area_id: t.area_id, start: t.start || '', end: t.end || '', hours: t.hours ?? '',
    skill_id: t.skill_id || null, people: t.people, event_days: t.event_days, on_month_rota: t.on_month_rota ?? null };
  if (isCut(task)) fields.times = task.times.map(({ id, start, end, people }) => ({ id, start, end, people }));
  return fields;
}

/**
 * One task on one line. `save(fields)` stores it (and redraws); `open()` shows the drawer.
 * `followsYes`: what "as the event" currently means; `oneDay`: the event lasts a single day.
 */
export function eventTaskRow(task, { eventDays, followsYes, oneDay = false, save, open, remove, move = null }) {
  const change = (patch) => save(taskFields(task, patch));
  const cut = isCut(task);
  // People are per time: a task in several times changes them in its form (click its name).
  const people = cut ? null : changes(h('span', { class: 'count-stepper', role: 'group', 'aria-label': `People for ${task.name}` },
    h('button', { class: 'btn ghost icon-only small', type: 'button', 'aria-label': 'One fewer', disabled: task.people <= 1,
      onclick: () => change({ people: task.people - 1 }) }, icon('minus')),
    h('span', { class: 'stepper-value', title: 'People needed each day' }, String(task.people)),
    h('button', { class: 'btn ghost icon-only small', type: 'button', 'aria-label': 'One more', disabled: task.people >= 30,
      onclick: () => change({ people: task.people + 1 }) }, icon('plus'))));
  const days = oneDay ? null : select(Object.entries(eventDays).map(([value, label]) => ({ value, label })), task.event_days,
    { class: 'select small', 'aria-label': `Which days for ${task.name}`, onchange: (e) => change({ event_days: e.target.value }) });
  if (days) changes(days);
  const choice = ownChoice(task);
  const printed = select(printOptions(followsYes), choice, {
    class: ['select small', choice !== 'follow' && 'exception'].filter(Boolean).join(' '),
    'aria-label': `${task.name}: printed on the main rota?`, title: PRINT_HELP,
    onchange: (e) => change({ on_month_rota: OWN_VALUE[e.target.value] }) });
  changes(printed);
  return h('li', { class: ['event-task-row', `area-${task.color}`] },
    h('button', { class: 'event-task-name', type: 'button', title: 'Change its details', onclick: open },
      h('strong', {}, task.name),
      h('span', { class: 'muted' }, [task.area, cut ? timesText(task) : task.time || 'any time',
        task.skill !== 'Anyone' ? task.skill : null, task.changed ? 'changed for this event' : null].filter(Boolean).join(' · '))),
    // The order of the tasks (where the list can be ordered): `move.up` / `move.down`, absent at the ends.
    // Next to the name, so the line of controls below stays as wide as it was.
    move ? changes(h('span', { class: 'move-pair' },
      h('button', { class: 'btn ghost icon-only small', type: 'button', disabled: !move.up, dataset: { focusId: `task-up-${task.id}` },
        'aria-label': `Move ${task.name} up`, title: 'Move up', onclick: move.up }, icon('up')),
      h('button', { class: 'btn ghost icon-only small', type: 'button', disabled: !move.down, dataset: { focusId: `task-down-${task.id}` },
        'aria-label': `Move ${task.name} down`, title: 'Move down', onclick: move.down }, icon('down')))) : null,
    h('span', { class: 'event-task-controls' },
      people ? captioned('People each day', people) : null, days ? captioned('Days', days) : null, captioned('On printed main rota', printed),
      changes(h('button', { class: 'btn ghost icon-only small', type: 'button', 'aria-label': `Remove ${task.name}`, title: 'Remove', onclick: remove }, icon('close')))));
}

/** "2 people are placed on 19:00–20:00: …": what saving does to a time taken away that has people on it. */
function removedText(time) {
  const who = time.placed === 1 ? '1 person is' : `${time.placed} people are`;
  return `${who} placed on ${time.time || 'this time'}: saving takes them off (Undo puts them back).`;
}

/**
 * The drawer with every detail of one task (or a new one), its times and
 * people included: one line per time, "+ Add another time" cuts it further.
 * `areaId`: the area a new task starts with (else the first one).
 * `save(fields)` and `remove()` return promises; a failed save shows its message in the form.
 */
export function editEventTask({ task, subtitle, areas, skills, eventDays, followsYes, oneDay = false, areaId = null, save, remove }) {
  const firstArea = areas.some((a) => a.id === areaId) ? areaId : areas[0]?.id;
  const t = task || { name: '', area_id: firstArea, start: '', end: '', skill_id: null, people: 1, event_days: 'all', on_month_rota: null };
  const name = textInput(t.name, { autofocus: true, placeholder: 'e.g. Hot chocolate' });
  const area = select(areas.map((a) => ({ value: a.id, label: a.name })), t.area_id);
  // Hours are worked out from the times; a task in one time without times can still count some.
  const hours = numberInput(t.start && t.end ? '' : (t.hours || ''), { min: 0, max: 24, step: 0.25, placeholder: 'From the times' });
  const hoursField = field('Hours counted', hours, 'Empty: from the times');
  const skill = select([{ value: '', label: 'Anyone' }, ...skills.map((s) => ({ value: s.id, label: s.name }))], t.skill_id || '');
  const days = segmented(Object.entries(eventDays).map(([value, label]) => ({ value, label })), t.event_days, { label: 'Which days' });
  const printed = segmented(printOptions(followsYes), ownChoice(t), { label: 'Printed on the main rota' });

  // Times and people: each line is a time as the server sent it (`placed`: people on it), or a new one.
  const makeRow = (time) => {
    const row = { time,
      start: h('input', { class: 'input', type: 'time', value: time.start || '' }),
      end: h('input', { class: 'input', type: 'time', value: time.end || '' }),
      people: numberInput(time.people ?? 1, { min: 1, max: 30, step: 1 }),
      remove: h('button', { class: 'btn ghost icon-only small', type: 'button', title: 'Remove this time' }, icon('close')) };
    row.remove.addEventListener('click', () => removeRow(row));
    return row;
  };
  const given = task?.times?.length ? task.times
    : [{ id: task?.id, start: t.start, end: t.end, people: t.people, time: t.time, placed: 0 }];
  const rows = given.map(makeRow);
  const removed = [];  // times the server sent, taken away in this form
  const timesList = h('div', { class: 'times-list' });
  const removedNote = h('div', { class: 'times-removed', 'aria-live': 'polite' });
  const addTime = h('button', { class: 'btn small', type: 'button' }, icon('plus'), 'Add another time');
  const drawTimes = () => {
    rows.forEach((row, index) => {
      const n = index + 1;
      row.start.setAttribute('aria-label', `Time ${n} starts`);
      row.end.setAttribute('aria-label', `Time ${n} ends`);
      row.people.setAttribute('aria-label', `People for time ${n}`);
      row.remove.setAttribute('aria-label', `Remove time ${n}`);
    });
    fill(timesList,
      h('div', { class: 'time-row time-head', 'aria-hidden': 'true' },
        h('span', {}, 'Starts'), h('span', {}), h('span', {}, 'Ends'), h('span', {}, 'People'), h('span', {})),
      rows.map((row, index) => h('div', { class: 'time-row', role: 'group', 'aria-label': `Time ${index + 1}` },
        row.start, h('span', { class: 'time-dash', 'aria-hidden': 'true' }, '–'), row.end, row.people,
        rows.length > 1 ? row.remove : h('span', {}))));
    hoursField.hidden = rows.length !== 1;
    fill(removedNote, removed.filter((row) => row.time.placed > 0)
      .map((row) => h('p', { class: 'field-hint times-warning' }, removedText(row.time))));
  };
  const removeRow = (row) => {
    const at = rows.indexOf(row);
    rows.splice(at, 1);
    if (row.time.id) removed.push(row);
    drawTimes();
    (rows.length > 1 ? rows[Math.min(at, rows.length - 1)].remove : addTime).focus();
  };
  addTime.addEventListener('click', () => {
    const last = rows.at(-1);
    const row = makeRow({ start: last?.end.value || '', end: '', people: last ? last.people.value : 1 });
    rows.push(row);
    drawTimes();
    row.end.focus();
  });
  drawTimes();

  const submit = async () => {
    try {
      await save({ id: t.id, name: name.value, area_id: area.value, hours: rows.length === 1 ? hours.value : '',
        skill_id: skill.value || null, event_days: oneDay ? 'all' : days.getValue(),
        on_month_rota: OWN_VALUE[printed.getValue()],
        times: rows.map((row) => ({ id: row.time.id, start: row.start.value, end: row.end.value, people: row.people.value })) });
      drawer.close();
    } catch (error) {
      formError(drawer.panel.querySelector('.drawer-body'), errorText(error));
    }
  };
  const drawer = openDrawer({ changes: true,
    title: task ? task.name : 'New task', subtitle,
    body: [field('Name', name), field('Area', area),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Times and people'), timesList,
        h('div', {}, addTime),
        h('span', { class: 'field-hint' }, 'Cut a long task into shorter shifts. Each time is filled on its own; the rota shows the name once.'),
        removedNote),
      hoursField,
      field('Skill needed', skill),
      oneDay ? null : h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Which days of the event'), days),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Printed on the main rota?'), printed,
        h('span', { class: 'field-hint' }, PRINT_HELP))],
    actions: [task && remove ? h('button', { class: 'btn ghost danger', type: 'button', onclick: async () => { if (await remove() !== false) drawer.close(); } }, 'Remove') : null,
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => drawer.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: submit }, task ? 'Save' : 'Add task')],
  });
}
