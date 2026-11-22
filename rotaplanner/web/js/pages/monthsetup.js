// Set up: create next month in one click, then mark what is different
// (events, closures, notes) on a calendar of the month.
import { changed, createMonth, needPen } from '../actions.js';
import { api, monthPath } from '../api.js';
import { badge, changes, chipChoice, field, formError, openDialog, segmented, select, textInput, toggle } from '../components.js';
import { fill, formatDate, formatHours, h } from '../dom.js';
import { addDays } from '../build/range.js';
import { editEventTask, eventTaskRow } from '../eventtask.js';
import { hint } from '../help.js';
import { icon } from '../icons.js';
import { errorText, failed } from '../setup.js';
import { setMonth, setSession, setUi, state } from '../state.js';
import { openMenu, toast } from '../ui.js';

const KINDS = [
  { value: 'closed', label: 'Closed' },
  { value: 'offrota', label: 'Off-rota event' },
  { value: 'note', label: 'Note' },
];
const KIND_HELP = {
  closed: 'Nobody is needed: for the whole centre, or only the areas you choose. Some tasks can be kept (security…).',
  offrota: 'Something happening outside the rota, shown on it for information.',
  note: 'A reminder shown on the rota and on what is printed.',
};

let loading = null;  // month id being fetched, so a render does not ask twice

async function loadMonthSetup() {
  if (!state.month || loading === state.month.id) return;
  loading = state.month.id;
  try {
    setSession({ monthSetup: await api.get(monthPath(loading, '/setup')) });
  } finally {
    loading = null;
  }
}

/** Send a change to the month; refresh the setup view and the rota. */
/** Send a change to the month; refresh the setup view and the rota. `message`: shown, with Undo when a step was made. */
async function changeMonth(path, body, message = null) {
  needPen();  // another computer edits: say so, send nothing
  const monthId = state.month.id;
  const setup = await api.post(monthPath(monthId, `/${path}`), body);
  // The event may have been copied to the next month: views of other months are loaded again.
  setSession({ monthSetup: setup, otherMonths: {} });
  setMonth(await api.get(monthPath(monthId)));
  changed(setup.history, monthId, message);
  return setup;
}

// ---- Creating a month -----------------------------------------------------

let loadingNew = false;

async function loadNewMonth() {
  if (loadingNew) return;
  loadingNew = true;
  try {
    setSession({ newMonth: await api.get('/api/new-month') });
  } finally {
    loadingNew = false;
  }
}

async function createAndOpen(choice, source) {
  const result = await createMonth(choice.id, source);
  setMonth(await api.get(monthPath(result.id)));
  setSession({ monthSetup: null, anchor: null });
  await loadMonthSetup();
  toast(`${choice.title} created · ${result.placed} shifts filled from the regular week`);
}

function createCard(info) {
  const free = info.choices.filter((c) => !c.exists);
  if (!free.length) return null;
  let choice = free[0];
  const which = segmented(free.map((c) => ({ value: c.id, label: c.title })), choice.id, {
    label: 'Month', onchange: (id) => { choice = free.find((c) => c.id === id); button.textContent = `Create ${choice.title}`; },
  });
  const source = h('select', { class: 'select', 'aria-label': 'Tasks and areas from' },
    info.sources.map((s) => h('option', { value: s.id, selected: s.id === info.default_source },
      `${s.label} · ${s.tasks} tasks${s.id === info.default_source ? ' (same as last month)' : ''}`)));
  const button = changes(h('button', { class: 'btn primary big', type: 'button', onclick: async () => {
    button.disabled = true;
    try {
      await createAndOpen(choice, source.value);
    } catch (error) {
      button.disabled = false;
      failed(error);
    }
  } }, `Create ${choice.title}`));

  return h('section', { class: 'card create-card' },
    h('div', { class: 'create-main' },
      h('h2', {}, 'Next month'),
      free.length > 1 ? which : null,
      h('p', { class: 'muted' }, `Same tasks and areas as the month you choose below, and the regular week already filled in (${info.regulars} commitments). Then mark what is different: events, closures, availability.`),
      field('Tasks and areas from', source)),
    h('div', { class: 'create-action' }, button));
}

// ---- Events, closures and notes -------------------------------------------

/** Dates an event or closure may use: this month, and running into the months around it. */
function dateBounds(setup) {
  return { min: addDays(setup.days[0].date, -31), max: addDays(setup.days.at(-1).date, 31) };
}

/**
 * An event runs into months not created yet: offer to create them now, so the
 * whole event can be planned (and printed) before that month is prepared.
 */
function offerMissingMonths(missing, itemName) {
  if (!missing?.length) return;
  const names = missing.map((m) => m.title).join(' and ');
  const dialog = openDialog({ changes: true,
    title: `Create ${names} now?`,
    subtitle: `${itemName} runs into ${names}, which is not created yet.`,
    body: [h('p', {}, `Create it now to plan the whole event: it starts with the same tasks as ${state.month.title}, the regular week, and this event. Its days count in that month's hours.`),
      h('p', { class: 'muted' }, 'Later is fine too: the event will be there when the month is created.')],
    actions: [h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Later'),
      h('button', { class: 'btn primary', type: 'button', onclick: async () => {
        try {
          for (const m of missing) await createMonth(m.id, `month:${state.month.id}`);
          dialog.close();
          toast(`${names} created · open the Event view to plan the whole event`);
        } catch (error) {
          formError(dialog.panel.querySelector('.modal-body'), errorText(error));
        }
      } }, `Create ${names}`)],
  });
}

/** What the server needs to save an event, from the event as saved plus what changed. */
const eventFields = (event, patch = {}) => ({ id: event.id, name: event.name, start: event.start, end: event.end,
  big: event.big, type_id: event.type_id || null, tasks_on_main_rota: event.tasks_on_main_rota, ...patch });

/** From the rota: open an event with its tasks (`newTask`: straight to adding one). */
export async function openEvent(eventId, { newTask = false } = {}) {
  try {
    if (state.session.monthSetup?.id !== state.month.id) {
      setSession({ monthSetup: await api.get(monthPath(state.month.id, '/setup')) });
    }
  } catch (error) {
    failed(error);
    return;
  }
  const event = state.session.monthSetup.events.find((e) => e.id === eventId);
  if (event) editEvent(event, { newTask });
}

/** What choosing a type brings, from the type's tasks as the server sent them (`added`: the event exists already). */
function bringsText(type, added) {
  const n = type.tasks.length;
  if (!n) return 'This type has no task yet.';
  const names = type.tasks.map((t) => t.name).join(', ');
  const what = n === 1 ? `Brings its task: ${names}.` : `Brings its ${n} tasks: ${names}.`;
  if (added) return `${what} ${n === 1 ? 'It is' : 'They are'} added when you save.`;
  return `${what} Once the event is added you can change ${n === 1 ? 'it, remove it' : 'them, remove some'} or add others.`;
}

export function editEvent(event, { justAdded = false, newTask = false } = {}) {
  const setup = state.session.monthSetup;
  const first = setup.days[0].date;
  const types = setup.event_types;
  const typeOf = (id) => types.find((t) => t.id === id);
  // The event as saved now (a type may have been given to it since the dialog opened).
  const current = () => (event ? state.session.monthSetup.events.find((e) => e.id === event.id) : null);
  const name = textInput(event?.name, { autofocus: !event, placeholder: 'e.g. Weekend retreat' });
  const { min, max } = dateBounds(setup);
  const start = h('input', { class: 'input', type: 'date', value: event?.start || first, min, max });
  const end = h('input', { class: 'input', type: 'date', value: event?.end || event?.start || first, min, max });
  start.addEventListener('change', () => { if (end.value < start.value) end.value = start.value; });
  const big = toggle('Big event (shown first in the Event view; its tasks start off the printed main rota)', event?.big);
  // The type of event (Retreat, Festival…) brings the tasks such events need.
  const kind = types.length ? select([{ value: '', label: 'No type' }, ...types.map((t) => ({ value: t.id, label: t.name }))],
    event?.type_id || '', { 'aria-describedby': 'event-type-hint' }) : null;
  const typeHint = h('p', { class: 'field-hint', id: 'event-type-hint', 'aria-live': 'polite' });
  let savedType = event?.type_id || '';  // the event's type when the list was last set to it…
  let listed = kind?.value ?? '';        // …and what the list showed then
  const describeType = () => {
    if (!kind) return;
    const chosen = typeOf(kind.value);
    const hasTasks = Boolean(current()?.tasks.length);
    let text = '';
    if (!event) text = chosen ? bringsText(chosen, false) : 'You add its tasks yourself once it is added.';
    else if (kind.value === listed) text = '';
    else if (!chosen) text = 'Its tasks stay.';
    else if (!hasTasks) text = bringsText(chosen, true);
    else if (!chosen.tasks.length) text = 'Its tasks stay. This type has no task yet.';
    else text = `Its tasks stay; the ${chosen.name} tasks it does not have yet are added when you save.`;
    typeHint.textContent = text;  // never hidden: a live region that appears is often not read out
  };
  /** After each change below: the list shows the event's type if that changed (e.g. "Add the tasks of a type…"). */
  const follow = (fresh) => {
    if (kind && (fresh.type_id || '') !== savedType) {
      savedType = fresh.type_id || '';
      kind.value = savedType;
      listed = kind.value;
    }
    describeType();
  };
  kind?.addEventListener('change', () => {
    if (typeOf(kind.value)) big.input.checked = typeOf(kind.value).big;
    describeType();
  });
  const typeField = kind ? field('Event type', kind) : null;
  typeField?.append(typeHint);
  describeType();
  // No type to choose yet: say what a type is, and where to make one.
  const noTypes = kind ? null : h('div', { class: 'field' },
    h('span', { class: 'field-label' }, 'Event type'),
    h('p', { class: 'field-hint' }, 'No event type yet. A type (Retreat, Festival…) keeps the tasks such events need, so each new one gets them at once.'),
    h('div', {}, h('button', { class: 'btn small', type: 'button', onclick: () => {
      dialog.close();
      setSession({ tasksTab: 'types' });
      window.location.hash = '#/tasks';
    } }, 'Set up event types')),
    h('p', { class: 'field-hint' }, event ? 'Or give this event its tasks below, then choose ' : 'Or add this event, give it its tasks, then choose ',
      h('strong', {}, 'Save as an event type'), '.'));
  const tasksBox = h('section', { class: 'event-tasks' });

  const save = async () => {
    try {
      const before = new Set(setup.events.map((e) => e.id));
      const saved = current();
      // The type chosen in the list; the list left as it was, or no list: the event keeps its own
      // (also a type deleted since, which the list cannot show).
      const typeId = kind && kind.value !== listed ? kind.value : saved?.type_id || '';
      const fields = { id: event?.id, name: name.value, start: start.value, end: end.value, big: big.input.checked, type_id: typeId || null };
      // A new event starts as its type says; an existing one keeps its own choice
      // (read again: the switch below may have changed it since the dialog opened).
      if (saved) fields.tasks_on_main_rota = saved.tasks_on_main_rota;
      else fields.tasks_on_main_rota = typeOf(typeId) ? typeOf(typeId).tasks_on_main_rota : !big.input.checked;
      // A type chosen (or changed): the program gives the event the type's tasks
      // it does not have yet, in the same step (one Undo takes back both).
      const result = await changeMonth('events/save', { event: fields }, event ? undefined : null);
      dialog.close();
      const added = result.events.find((e) => !before.has(e.id));
      offerMissingMonths(result.missing_months, name.value.trim());
      // A new event opens again, now with its tasks.
      if (added) editEvent(state.session.monthSetup.events.find((e) => e.id === added.id), { justAdded: true });
    } catch (error) {
      formError(dialog.panel.querySelector('.modal-body'), errorText(error));
    }
  };
  const remove = async () => {
    await changeMonth('events/delete', { id: event.id }, event.tasks.length ? `Event and its tasks removed: ${event.name}` : undefined);
    dialog.close();
  };
  const dialog = openDialog({ changes: true,
    title: event ? event.name : 'Add an event', wide: Boolean(event),
    subtitle: justAdded ? 'Event added. Its tasks are below: adjust them, or close.'
      : event || !kind ? '' : 'Choose its type to give it the tasks such events need.',
    body: [
      kind ? h('div', { class: 'form-grid' }, field('Name', name), typeField) : [field('Name', name), noTypes],
      h('div', { class: 'form-grid' }, field('First day', start), field('Last day', end)),
      big,
      h('p', { class: 'field-hint' }, 'It can run into the month before or after: those days are planned with their own month, and the event is printed whole. During the event, usual tasks use their “Event day” numbers.'),
      event ? h('div', { class: 'block-actions' },
        h('button', { class: 'btn small primary', type: 'button', onclick: save }, 'Save')) : null,
      event ? tasksBox : null],
    actions: [event ? h('button', { class: 'btn ghost danger', type: 'button', onclick: remove }, 'Remove the event') : null,
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, event ? 'Close' : 'Cancel'),
      event ? null : h('button', { class: 'btn primary', type: 'button', onclick: save }, 'Add event')],
  });
  if (event) {
    const addTask = drawEventTasks(tasksBox, event.id, follow);
    if (newTask) addTask();
  }
}

/** An event's tasks: each on one line, changed in place and saved at once; with a type,
    those that came from it apart from those only this event has. `onDraw(event)` follows
    each drawing. Returns a function opening the form for a new task. */
function drawEventTasks(box, eventId, onDraw) {
  const setup = state.session.monthSetup;
  const event = setup.events.find((e) => e.id === eventId);
  if (!event) return;
  const redraw = () => drawEventTasks(box, eventId, onDraw);
  const change = async (path, body, message) => {  // true when it was saved (otherwise it said why)
    try {
      await changeMonth(path, body, message);
      redraw();
      return true;
    } catch (error) {
      failed(error);
      return false;
    }
  };
  const types = setup.event_types.filter((t) => t.tasks.length);
  const ownType = setup.event_types.find((t) => t.id === event.type_id);
  const common = { areas: setup.areas, skills: setup.skills, eventDays: setup.event_days,
    followsYes: event.tasks_on_main_rota, oneDay: event.start === event.end };
  const saveTask = (fields, message) => changeMonth('events/tasks/save', { task: { ...fields, event_id: eventId } }, message);
  // A new task starts in the area of the event's last task (tasks of one area are usually added together).
  const open = (task) => editEventTask({ ...common, task, subtitle: event.name, areaId: event.tasks.at(-1)?.area_id,
    save: async (fields) => { await saveTask(fields, undefined); redraw(); },
    remove: task ? () => change('events/tasks/delete', { id: task.id }, undefined) : null });
  const onMain = toggle('Print these tasks on the main rota too', event.tasks_on_main_rota,
    async (e) => {
      const input = e.target;
      const saved = await change('events/save', { event: eventFields(event, { tasks_on_main_rota: input.checked }) },
        input.checked ? 'Its tasks will be printed on the main rota too' : 'Its tasks will be printed on its own rota only');
      if (!saved) input.checked = !input.checked;  // as it still is
    });
  const chooseType = (anchor) => openMenu(anchor,
    types.map((t) => ({ value: t.id, label: `${t.name} — ${t.tasks.map((x) => x.name).join(', ')}` })),
    (typeId) => change('events/tasks/add-type', { event_id: eventId, type_id: typeId }, undefined));
  const list = (tasks, empty) => (tasks.length
    ? h('ul', { class: 'event-task-list' }, tasks.map((task) => eventTaskRow(task, {
      ...common,
      save: (fields) => saveTask(fields, null).catch(failed).finally(redraw),  // also after a refusal: shows it as it still is
      open: () => open(task),
      remove: () => change('events/tasks/delete', { id: task.id }, undefined),
    })))
    : h('p', { class: 'faint' }, empty));
  // Which tasks came from the event's type is said by the program (`from_type`).
  const fromType = event.tasks.filter((t) => t.from_type);
  const onlyHere = event.tasks.filter((t) => !t.from_type);
  const typeName = event.type_name;

  fill(box,
    h('div', { class: 'event-tasks-head' },
      h('h3', {}, `${typeName ? 'Its tasks' : 'Its own tasks'} · ${event.tasks.length}`),
      h('span', { class: 'faint' }, 'Needed only on its days. Changes here are saved at once.')),
    h('div', { class: 'main-rota-choice' }, onMain,
      h('span', { class: 'faint' }, 'They are always on the rota to fill (Build) and on the event’s own printout.')),
    typeName
      ? [fromType.length ? [h('h4', { class: 'event-task-group' }, `From the ${typeName} type · ${fromType.length}`), list(fromType, '')] : null,
        h('h4', { class: 'event-task-group' }, `Only for this event · ${onlyHere.length}`),
        list(onlyHere, 'None: add a task this event needs that the type doesn’t have.')]
      : list(event.tasks, 'None yet: anything this event needs that the usual tasks do not cover.'),
    h('div', { class: 'card-actions' },
      h('button', { class: 'btn small', type: 'button', onclick: () => open(null) }, icon('plus'), 'Add a task'),
      types.length ? h('button', { class: 'btn small', type: 'button', onclick: (e) => chooseType(e.currentTarget) }, 'Add the tasks of a type…') : null,
      h('span', { class: 'spacer' }),
      typeName && onlyHere.length ? h('span', { class: 'faint' }, onlyHere.length === 1
        ? `1 task is only on this event: update the type to give it to every new ${typeName}.`
        : `${onlyHere.length} tasks are only on this event: update the type to give them to every new ${typeName}.`) : null,
      event.tasks.length ? h('button', { class: 'btn small ghost', type: 'button', title: 'So the next event of this type gets these tasks',
        onclick: () => saveAsType(event, ownType, redraw) },
      ownType ? `Update the ${ownType.name} type…` : 'Save as an event type…') : null));
  onDraw?.(event);
  return () => open(null);
}

/** Keep an event's tasks as those of its type, so the next event of that type gets them. */
function saveAsType(event, ownType, onDone) {
  const setup = state.session.monthSetup;
  const name = textInput(ownType?.name || '', { autofocus: true, placeholder: 'e.g. Retreat, Festival', list: 'event-type-names' });
  const names = h('datalist', { id: 'event-type-names' }, setup.event_types.map((t) => h('option', { value: t.name })));
  const save = async () => {
    try {
      await changeMonth('events/save-as-type', { event_id: event.id, name: name.value }, `${name.value.trim()}: tasks saved for next time`);
      dialog.close();
      onDone();
    } catch (error) {
      formError(dialog.panel.querySelector('.modal-body'), errorText(error));
    }
  };
  const dialog = openDialog({ changes: true,
    title: ownType ? `Update the ${ownType.name} type` : 'Save as an event type',
    subtitle: `${event.tasks.map((t) => t.name).join(', ')}.`,
    body: [field('Event type', name, 'The name of an existing type updates it.'), names,
      h('p', { class: 'muted' }, 'The next event of this type starts with these tasks. Events already planned keep theirs. Types are also edited in Tasks & Areas › Event types.')],
    actions: [h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: save }, 'Save')],
  });
}

function editMarker(marker, defaults = {}) {
  const setup = state.session.monthSetup;
  const first = setup.days[0].date;
  const m = marker || { kind: defaults.kind || 'closed', label: '', start: defaults.date || first, end: defaults.date || first, area_ids: [], keep_task_ids: [] };
  const help = h('p', { class: 'field-hint' }, KIND_HELP[m.kind]);
  const keepBox = h('div', {});
  const kind = segmented(KINDS, m.kind, { label: 'Kind', onchange: (value) => {
    help.textContent = KIND_HELP[value];
    keepBox.hidden = value !== 'closed';
  } });
  const label = textInput(m.label, { autofocus: true, placeholder: 'e.g. Centre closed for Christmas' });
  const { min, max } = dateBounds(setup);
  const start = h('input', { class: 'input', type: 'date', value: m.start, min, max });
  const end = h('input', { class: 'input', type: 'date', value: m.end, min, max });
  start.addEventListener('change', () => { if (end.value < start.value) end.value = start.value; });
  const areas = chipChoice(setup.areas.map((a) => ({ value: a.id, label: a.name })), m.area_ids, { label: 'Areas' });
  const keep = chipChoice(setup.tasks.map((t) => ({ value: t.id, label: t.name })), m.keep_task_ids, { label: 'Tasks kept' });
  keepBox.append(h('h3', { class: 'form-section' }, 'Tasks still needed'), keep);
  keepBox.hidden = m.kind !== 'closed';
  const save = async () => {
    try {
      const result = await changeMonth('markers/save', { marker: { id: m.id, kind: kind.getValue(), label: label.value, start: start.value,
        end: end.value, area_ids: areas.getValue(), keep_task_ids: keep.getValue() } }, undefined);
      if (result.missing_months?.length) {
        toast(`Also for ${result.missing_months.map((x) => x.title).join(' and ')}: it will be there when that month is created`);
      }
      dialog.close();
    } catch (error) {
      formError(dialog.panel.querySelector('.modal-body'), errorText(error));
    }
  };
  const remove = async () => {
    await changeMonth('markers/delete', { id: m.id }, undefined);
    dialog.close();
  };
  const dialog = openDialog({ changes: true,
    title: marker ? marker.label : 'Closure, off-rota event or note', wide: true,
    body: [kind, help, field('Label', label), h('div', { class: 'form-grid' }, field('First day', start), field('Last day', end)),
      h('h3', { class: 'form-section' }, 'Areas concerned'), h('p', { class: 'field-hint' }, 'None ticked: the whole centre.'), areas, keepBox],
    actions: [marker ? h('button', { class: 'btn ghost danger', type: 'button', onclick: remove }, 'Remove') : null,
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: save }, marker ? 'Save' : 'Add')],
  });
}

function editDay(day) {
  const setup = state.session.monthSetup;
  const covering = (item) => item.start <= day.date && day.date <= item.end;
  const kind = segmented([{ value: '', label: 'Automatic' }, { value: 'normal', label: 'Usual day' }, { value: 'event', label: 'Event day' }],
    day.set_by_hand ? day.day_type : '', { label: 'Kind of day' });
  const save = async () => {
    await changeMonth('daytype', { date: day.date, day_type: kind.getValue() || null }, undefined);
    dialog.close();
  };
  const dialog = openDialog({ changes: true,
    title: formatDate(day.date),
    subtitle: `${day.shifts} shifts · ${formatHours(day.hours)} of work · ${day.filled} filled`,
    body: [
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'This day is'), kind,
        h('span', { class: 'field-hint' }, 'Automatic: an event day when an event covers it.')),
      h('h3', { class: 'form-section' }, 'On this day'),
      h('ul', { class: 'day-items' },
        setup.events.filter(covering).map((e) => h('li', {}, h('button', { class: 'link', type: 'button', onclick: () => { dialog.close(); editEvent(e); } }, `Event: ${e.name}`))),
        setup.markers.filter(covering).map((m) => h('li', {}, h('button', { class: 'link', type: 'button', onclick: () => { dialog.close(); editMarker(m); } }, `${KINDS.find((k) => k.value === m.kind).label}: ${m.label}`)))),
      h('div', { class: 'card-actions' },
        h('button', { class: 'btn small', type: 'button', onclick: () => { dialog.close(); editMarker(null, { kind: 'closed', date: day.date }); } }, 'Close this day…'),
        h('button', { class: 'btn small', type: 'button', onclick: () => { dialog.close(); editMarker(null, { kind: 'note', date: day.date }); } }, 'Add a note…'),
        h('button', { class: 'btn small', type: 'button', onclick: () => {
          dialog.close();
          setUi({ view: 'day' });
          setSession({ anchor: day.date });
          window.location.hash = '#/build';
        } }, 'See this day on the rota')),
    ],
    actions: [h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: save }, 'Save')],
  });
}

// ---- The month at a glance ------------------------------------------------

function calendar(setup) {
  const offset = (new Date(`${setup.days[0].date}T12:00:00`).getDay() + 6) % 7;
  const maxHours = Math.max(1, ...setup.days.map((d) => d.hours));
  const itemsOn = (iso) => [
    ...setup.events.filter((e) => e.start <= iso && iso <= e.end).map((e) => h('span', { class: ['pill', e.big ? 'event big' : 'event'] }, e.name)),
    ...setup.markers.filter((m) => m.start <= iso && iso <= m.end).map((m) => h('span', { class: ['pill', m.kind] }, m.label)),
  ];
  return h('div', { class: 'month-calendar', role: 'grid', 'aria-label': `Days of ${setup.title}` },
    ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => h('div', { class: 'cal-head', role: 'columnheader' }, d)),
    Array.from({ length: offset }, () => h('div', { class: 'cal-blank' })),
    setup.days.map((day) => h('button', {
      type: 'button', class: ['cal-day', day.weekend && 'weekend', day.day_type === 'event' && 'event-day',
        setup.markers.some((m) => m.kind === 'closed' && !m.area_ids.length && m.start <= day.date && day.date <= m.end) && 'closed'],
      onclick: () => editDay(day), 'aria-label': `${formatDate(day.date)}, ${day.shifts} shifts`,
    },
    h('span', { class: 'cal-num' }, day.day, day.set_by_hand ? h('span', { class: 'faint', title: 'Kind of day set by hand' }, ' ✎') : null),
    h('span', { class: 'cal-items' }, itemsOn(day.date)),
    h('span', { class: 'cal-load' },
      h('span', { class: 'cal-bar' }, h('span', { style: { width: `${Math.round((day.hours / maxHours) * 100)}%` } })),
      h('span', {}, `${day.shifts} · ${Math.round(day.hours)} h`)))));
}

function balance(setup) {
  // Whether the people can give what the month needs is judged by the program (totals.balance).
  const { hours_needed: needed, hours_available: available, balance: tone, hours_short: short, people, replied } = setup.totals;
  return h('div', { class: 'tiles setup-tiles' },
    h('div', { class: 'tile' }, h('span', { class: 'tile-value' }, `${Math.round(needed)} h`), h('span', { class: 'tile-label' }, 'of work to cover')),
    h('div', { class: ['tile', tone] },
      h('span', { class: 'tile-value' }, `${Math.round(available)} h`),
      h('span', { class: 'tile-label' }, `people can give${short ? ` — ${short} h short` : ''}`, hint('target'))),
    h('a', { class: 'tile', href: '#/availability' }, h('span', { class: 'tile-value' }, `${replied}/${people}`), h('span', { class: 'tile-label' }, 'availability replies')));
}

function listSection(title, items, empty, render, addLabel, onAdd) {
  return h('section', { class: 'card' },
    h('div', { class: 'card-title' }, h('h2', {}, title), changes(h('button', { class: 'btn small', type: 'button', onclick: onAdd }, icon('plus'), addLabel))),
    items.length ? h('ul', { class: 'item-list' }, items.map(render)) : h('p', { class: 'faint' }, empty));
}

export function mountMonthSetup(container) {
  const root = h('div', { class: 'page setup-page' });
  container.replaceChildren(root);
  setSession({ monthSetup: null });  // other screens may have changed people, tasks or availability

  function render() {
    const info = state.session.newMonth;
    if (!info) loadNewMonth().catch(() => {});  // first time, or after a month was created
    let setup = state.session.monthSetup;
    if (state.month && setup?.id !== state.month.id) {
      loadMonthSetup().catch((error) => failed(error));
      setup = null;
    }
    const range = (item) => (item.start === item.end ? formatDate(item.start) : `${formatDate(item.start)} – ${formatDate(item.end)}`);
    fill(root, 
      h('header', { class: 'page-head' },
        h('div', {}, h('h1', {}, setup ? `Set up ${setup.title}` : 'Set up'),
          h('p', { class: 'muted' }, 'Usually prepared around the 10th for the month after. When nothing changes, creating the month is enough.')),
        setup ? h('div', { class: 'page-actions' },
          h('a', { class: 'btn', href: '#/tasks' }, icon('layers'), 'Tasks of this month'),
          changes(h('button', { class: 'btn', type: 'button', title: 'Fill empty shifts from the regular week',
            onclick: () => changeMonth('regulars/apply', {}, undefined) }, icon('repeat'), 'Apply the regular week')),
          h('a', { class: 'btn primary', href: '#/availability' }, 'Next: availability', icon('right'))) : null),
      info ? createCard(info) : null,
      setup ? balance(setup) : null,
      setup ? h('div', { class: 'setup-grid' },
        h('section', { class: 'card calendar-card' }, h('div', { class: 'card-title' }, h('h2', {}, setup.title),
          h('span', { class: 'faint' }, 'Click a day to change it')), calendar(setup)),
        h('div', { class: 'setup-side' },
          listSection('Events and retreats', setup.events, 'No event this month.',
            (e) => h('li', {}, h('button', { class: 'item', type: 'button', onclick: () => editEvent(e) },
              h('strong', {}, e.name), h('span', { class: 'muted' }, range(e)), e.big ? badge('Big event', 'warn') : null,
              e.type_name ? badge(e.type_name, 'info') : null,
              e.tasks.length ? h('span', { class: 'faint' }, `${e.tasks.length} task${e.tasks.length > 1 ? 's' : ''}`) : null)),
            'Add an event', () => editEvent(null)),
          listSection('Closures and notes', setup.markers, 'Nothing closed, no note.',
            (m) => h('li', {}, h('button', { class: 'item', type: 'button', onclick: () => editMarker(m) },
              h('strong', {}, m.label), h('span', { class: 'muted' }, range(m)), badge(KINDS.find((k) => k.value === m.kind).label))),
            'Add', () => editMarker(null)))) : null,
      !setup && !info ? h('p', { class: 'empty' }, 'Loading…') : null);
  }

  return { render };
}
