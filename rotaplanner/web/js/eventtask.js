// A task of an event, or of an event type: the line with its quick controls
// (people, which days, printed on the main rota) and the drawer with all its
// details. Used by the event dialog (Set up) and by Tasks & Areas › Event types.
//
// Two rotas, two words: the rota to FILL (Build) always has every task; the
// PRINTED main rota has an event's tasks only if the event says so, and a
// task can answer differently from the rest of its event. The event's own
// printout always has them all.
import { changes, field, formError, numberInput, openDrawer, segmented, select, textInput } from './components.js';
import { h } from './dom.js';
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

/** What the server needs to save a task, from the task as shown plus what changed. */
export function taskFields(task, patch = {}) {
  const t = { ...task, ...patch };
  return { id: t.id, name: t.name, area_id: t.area_id, start: t.start || '', end: t.end || '', hours: t.hours ?? '',
    skill_id: t.skill_id || null, people: t.people, event_days: t.event_days, on_month_rota: t.on_month_rota ?? null };
}

/**
 * One task on one line. `save(fields)` stores it (and redraws); `open()` shows the drawer.
 * `followsYes`: what "as the event" currently means; `oneDay`: the event lasts a single day.
 */
export function eventTaskRow(task, { eventDays, followsYes, oneDay = false, save, open, remove, move = null }) {
  const change = (patch) => save(taskFields(task, patch));
  const people = changes(h('span', { class: 'count-stepper', role: 'group', 'aria-label': `People for ${task.name}` },
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
      h('span', { class: 'muted' }, [task.area, task.time || 'any time', task.skill !== 'Anyone' ? task.skill : null].filter(Boolean).join(' · '))),
    // The order of the tasks (where the list can be ordered): `move.up` / `move.down`, absent at the ends.
    // Next to the name, so the line of controls below stays as wide as it was.
    move ? changes(h('span', { class: 'move-pair' },
      h('button', { class: 'btn ghost icon-only small', type: 'button', disabled: !move.up, dataset: { focusId: `task-up-${task.id}` },
        'aria-label': `Move ${task.name} up`, title: 'Move up', onclick: move.up }, icon('up')),
      h('button', { class: 'btn ghost icon-only small', type: 'button', disabled: !move.down, dataset: { focusId: `task-down-${task.id}` },
        'aria-label': `Move ${task.name} down`, title: 'Move down', onclick: move.down }, icon('down')))) : null,
    h('span', { class: 'event-task-controls' },
      captioned('People each day', people), days ? captioned('Days', days) : null, captioned('On printed main rota', printed),
      changes(h('button', { class: 'btn ghost icon-only small', type: 'button', 'aria-label': `Remove ${task.name}`, title: 'Remove', onclick: remove }, icon('close')))));
}

/**
 * The drawer with every detail of one task (or a new one).
 * `save(fields)` and `remove()` return promises; a failed save shows its message in the form.
 */
export function editEventTask({ task, subtitle, areas, skills, eventDays, followsYes, oneDay = false, save, remove }) {
  const t = task || { name: '', area_id: areas[0]?.id, start: '', end: '', skill_id: null, people: 1, event_days: 'all', on_month_rota: null };
  const name = textInput(t.name, { autofocus: true, placeholder: 'e.g. Hot chocolate' });
  const area = select(areas.map((a) => ({ value: a.id, label: a.name })), t.area_id);
  const start = h('input', { class: 'input', type: 'time', value: t.start || '' });
  const end = h('input', { class: 'input', type: 'time', value: t.end || '' });
  // Hours are worked out from the times; a task without times can still count some.
  const hours = numberInput(t.start && t.end ? '' : (t.hours || ''), { min: 0, max: 24, step: 0.25, placeholder: 'From the times' });
  const skill = select([{ value: '', label: 'Anyone' }, ...skills.map((s) => ({ value: s.id, label: s.name }))], t.skill_id || '');
  const people = numberInput(t.people, { min: 1, max: 30, step: 1 });
  const days = segmented(Object.entries(eventDays).map(([value, label]) => ({ value, label })), t.event_days, { label: 'Which days' });
  const printed = segmented(printOptions(followsYes), ownChoice(t), { label: 'Printed on the main rota' });
  const submit = async () => {
    try {
      await save({ id: t.id, name: name.value, area_id: area.value, start: start.value, end: end.value, hours: hours.value,
        skill_id: skill.value || null, people: people.value, event_days: oneDay ? 'all' : days.getValue(),
        on_month_rota: OWN_VALUE[printed.getValue()] });
      drawer.close();
    } catch (error) {
      formError(drawer.panel.querySelector('.drawer-body'), errorText(error));
    }
  };
  const drawer = openDrawer({ changes: true,
    title: task ? task.name : 'New task', subtitle,
    body: [field('Name', name), field('Area', area),
      h('div', { class: 'form-grid' }, field('Starts', start), field('Ends', end)),
      h('div', { class: 'form-grid' }, field('Hours counted', hours, 'Empty: from the times'), field('People each day', people)),
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
