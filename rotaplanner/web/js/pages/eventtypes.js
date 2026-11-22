// Tasks & Areas › Event types: the kinds of event (Retreat, Festival…) and
// the tasks such events need on top of the usual ones. An event of a type
// starts with copies of these tasks, which can then be changed for it alone.
import { badge, changes, field, formError, openDialog, textInput, toggle } from '../components.js';
import { h } from '../dom.js';
import { editEventTask, eventTaskRow } from '../eventtask.js';
import { icon } from '../icons.js';
import { errorText, failed, saveSetup } from '../setup.js';
import { setSession, state } from '../state.js';
import { toast } from '../ui.js';

const STARTERS = [
  { name: 'Retreat', big: false, tasks_on_main_rota: true, hint: 'A few extra tasks, printed on the main rota too (hot chocolate on the first day…)' },
  { name: 'Festival', big: true, tasks_on_main_rota: false, hint: 'A big event with many tasks of its own, printed on its own rota' },
];

/** Resolves true when it was saved (otherwise it said why). */
async function send(path, body, message) {
  try {
    await saveSetup(path, body, { message });
    return true;
  } catch (error) {
    failed(error);
    return false;
  }
}

function editType(type) {
  const name = textInput(type?.name, { autofocus: true, placeholder: 'e.g. Retreat, Festival, Course' });
  const big = toggle('Big event (shown first in the Event view; its tasks start off the printed main rota)', type?.big);
  const onMain = toggle('Print its tasks on the main rota too', type ? type.tasks_on_main_rota : true);
  // Until chosen by hand, big events keep their tasks on their own rota.
  let touched = Boolean(type);
  onMain.input.addEventListener('change', () => { touched = true; });
  big.input.addEventListener('change', () => { if (!touched) onMain.input.checked = !big.input.checked; });
  const save = async () => {
    try {
      await saveSetup('event-types/save', { type: { id: type?.id, name: name.value, big: big.input.checked,
        tasks_on_main_rota: onMain.input.checked } }, { message: type ? 'Type changed' : 'Type added' });
      dialog.close();
    } catch (error) {
      formError(dialog.panel.querySelector('.modal-body'), errorText(error));
    }
  };
  const dialog = openDialog({ changes: true,
    title: type ? type.name : 'New event type',
    subtitle: 'Events of this type start with its tasks; each event can then differ.',
    body: [field('Name', name), big, onMain,
      h('p', { class: 'field-hint' }, 'The tasks are always on the rota to fill (Build) and on the event’s own printout. This only decides the printed main rota; each event can still decide for itself.')],
    actions: [type ? h('button', { class: 'btn ghost danger', type: 'button', onclick: async () => {
      if (await send('event-types/delete', { id: type.id }, `${type.name} removed · events already planned keep their tasks`)) dialog.close();
    } }, 'Remove') : null,
    h('span', { class: 'spacer' }),
    h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
    h('button', { class: 'btn primary', type: 'button', onclick: save }, type ? 'Save' : 'Add type')],
  });
}

function typeCard(type, setup) {
  const areas = setup.catalog.areas.filter((a) => !a.archived);
  const common = { areas, skills: setup.catalog.skills, eventDays: setup.event_days, followsYes: type.tasks_on_main_rota };
  const saveTask = (fields, message) => saveSetup('event-types/tasks/save', { task: { ...fields, type_id: type.id } }, { message });
  // A new task starts in the area of the type's last task.
  const open = (task) => editEventTask({ ...common, task, subtitle: `For every ${type.name}`, areaId: type.tasks.at(-1)?.area_id,
    save: (fields) => saveTask(fields, task ? 'Task changed' : 'Task added'),
    remove: task ? () => send('event-types/tasks/delete', { type_id: type.id, id: task.id }, 'Task removed') : null });
  const moveTask = (index, step) => {
    const ids = type.tasks.map((t) => t.id);
    ids.splice(index + step, 0, ...ids.splice(index, 1));
    return send('event-types/tasks/order', { type_id: type.id, ids });
  };
  return h('section', { class: 'card type-card' },
    h('div', { class: 'card-title' },
      h('h2', {}, type.name),
      type.big ? badge('Big event', 'warn') : null,
      badge(type.tasks_on_main_rota ? 'Printed on the main rota too' : 'Printed on its own rota only', type.tasks_on_main_rota ? '' : 'info'),
      h('button', { class: 'btn ghost icon-only small', type: 'button', 'aria-label': `Change ${type.name}`, title: 'Name and options',
        onclick: () => editType(type) }, icon('sliders'))),
    type.tasks.length
      ? h('ul', { class: 'event-task-list' }, type.tasks.map((task, index) => eventTaskRow(task, {
        ...common,
        move: { up: index > 0 ? () => moveTask(index, -1) : null, down: index < type.tasks.length - 1 ? () => moveTask(index, 1) : null },
        // Refused: drawn again (an empty change still redraws), so the row shows what is really saved.
        save: (fields) => saveTask(fields).catch((error) => { failed(error); setSession({}); }),
        open: () => open(task),
        remove: () => send('event-types/tasks/delete', { type_id: type.id, id: task.id }, 'Task removed'),
      })))
      : h('p', { class: 'faint' }, 'No task yet: what does every event of this type need that the usual tasks do not cover?'),
    h('div', { class: 'card-actions' },
      changes(h('button', { class: 'btn small', type: 'button', onclick: () => open(null) }, icon('plus'), 'Add a task'))));
}

export function eventTypesBoard() {
  const setup = state.session.setup;
  const types = setup.event_types;
  if (!types.length) {
    return h('div', { class: 'empty-start' },
      h('h2', {}, 'No event type yet'),
      h('p', { class: 'muted' }, 'A type keeps the tasks that events of that kind need, so a new retreat or festival gets them in one click. Start from one of these, or from scratch:'),
      h('div', { class: 'starter-row' },
        STARTERS.map((starter) => changes(h('button', { class: 'starter', type: 'button',
          onclick: () => send('event-types/save', { type: starter }, `${starter.name} added: now give it its tasks`) },
        h('strong', {}, starter.name), h('span', { class: 'muted' }, starter.hint)))),
        changes(h('button', { class: 'starter', type: 'button', onclick: () => editType(null) },
          h('strong', {}, 'Another type…'), h('span', { class: 'muted' }, 'Course, open day, working weekend…')))));
  }
  return h('div', { class: 'type-cards' },
    types.map((type) => typeCard(type, setup)),
    changes(h('button', { class: 'card type-card add-type', type: 'button', onclick: () => editType(null) }, icon('plus'), 'New event type')));
}
