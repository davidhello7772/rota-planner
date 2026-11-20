// Set up: create next month in one click, then mark what is different
// (events, closures, notes) on a calendar of the month.
import { createMonth, needPen } from '../actions.js';
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
async function changeMonth(path, body, message) {
  needPen();  // another computer edits: say so, send nothing
  const setup = await api.post(monthPath(state.month.id, `/${path}`), body);
  // The event may have been copied to the next month: views of other months are loaded again.
  setSession({ monthSetup: setup, otherMonths: {} });
  setMonth(await api.get(monthPath(state.month.id)));
  if (message) toast(message, { actionLabel: 'Undo', onAction: undoSetup });
  return setup;
}

async function undoSetup() {
  needPen();  // another computer edits: say so, send nothing
  setMonth(await api.post(monthPath(state.month.id, '/undo')));
  await loadMonthSetup();
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

export function editEvent(event, { justAdded = false, newTask = false } = {}) {
  const setup = state.session.monthSetup;
  const first = setup.days[0].date;
  const types = setup.event_types;
  const typeOf = (id) => types.find((t) => t.id === id);
  const name = textInput(event?.name, { autofocus: !event, placeholder: 'e.g. Weekend retreat' });
  const { min, max } = dateBounds(setup);
  const start = h('input', { class: 'input', type: 'date', value: event?.start || first, min, max });
  const end = h('input', { class: 'input', type: 'date', value: event?.end || event?.start || first, min, max });
  start.addEventListener('change', () => { if (end.value < start.value) end.value = start.value; });
  const big = toggle('Big event (shown first in the Event view; its tasks start off the printed main rota)', event?.big);
  // The type of event (Retreat, Festival…) brings the tasks such events need.
  const kind = types.length ? select([{ value: '', label: 'No type' },
    ...types.map((t) => ({ value: t.id, label: t.tasks.length ? `${t.name} · ${t.tasks.map((x) => x.name).join(', ')}` : t.name }))],
  event?.type_id || '') : null;
  kind?.addEventListener('change', () => { if (typeOf(kind.value)) big.input.checked = typeOf(kind.value).big; });
  const tasksBox = h('section', { class: 'event-tasks' });

  const save = async () => {
    try {
      const before = new Set(setup.events.map((e) => e.id));
      // The type chosen here, else the event's own (it may have been set since the dialog opened).
      const savedType = (event && state.session.monthSetup.events.find((e) => e.id === event.id)?.type_id) || '';
      const typeId = kind && kind.value !== (event?.type_id || '') ? kind.value : savedType;
      const fields = { id: event?.id, name: name.value, start: start.value, end: end.value, big: big.input.checked, type_id: typeId || null };
      // A new event starts as its type says; an existing one keeps its own choice.
      // (read again: the switch below may have changed it since the dialog opened).
      const saved = event && state.session.monthSetup.events.find((e) => e.id === event.id);
      if (saved) fields.tasks_on_main_rota = saved.tasks_on_main_rota;
      else fields.tasks_on_main_rota = typeOf(typeId) ? typeOf(typeId).tasks_on_main_rota : !big.input.checked;
      const result = await changeMonth('events/save', { event: fields }, event ? 'Event changed' : null);
      dialog.close();
      const added = result.events.find((e) => !before.has(e.id));
      // A type chosen (or changed): the event gets the type's tasks it does not have yet.
      if (typeId && typeId !== event?.type_id && typeOf(typeId).tasks.length) {
        await changeMonth('events/tasks/add-type', { event_id: added?.id || event.id, type_id: typeId });
      }
      offerMissingMonths(result.missing_months, name.value.trim());
      // A new event opens again, now with its tasks.
      if (added) editEvent(state.session.monthSetup.events.find((e) => e.id === added.id), { justAdded: true });
    } catch (error) {
      formError(dialog.panel.querySelector('.modal-body'), errorText(error));
    }
  };
  const remove = async () => {
    await changeMonth('events/delete', { id: event.id }, event.tasks.length ? 'Event and its tasks removed' : 'Event removed');
    dialog.close();
  };
  const dialog = openDialog({ changes: true,
    title: event ? event.name : 'Add an event', wide: Boolean(event),
    subtitle: justAdded ? 'Event added. Its tasks are below: adjust them, or close.' : event ? '' : 'Choose its type to give it the tasks such events need.',
    body: [
      h('div', { class: 'form-grid' }, field('Name', name), kind ? field('Event type', kind) : null),
      h('div', { class: 'form-grid' }, field('First day', start), field('Last day', end)),
      big,
      h('p', { class: 'field-hint' }, 'It can run into the month before or after: those days are planned with their own month, and the event is printed whole. During the event, usual tasks use their “Event day” numbers.'),
      event ? h('div', { class: 'block-actions' },
        h('button', { class: 'btn small primary', type: 'button', onclick: save }, 'Save name and dates')) : null,
      event ? tasksBox : null],
    actions: [event ? h('button', { class: 'btn ghost danger', type: 'button', onclick: remove }, 'Remove the event') : null,
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, event ? 'Close' : 'Cancel'),
      event ? null : h('button', { class: 'btn primary', type: 'button', onclick: save }, 'Add event')],
  });
  if (event) {
    const addTask = drawEventTasks(tasksBox, event.id);
    if (newTask) addTask();
  }
}

/** The tasks only this event has: each on one line, changed in place and saved at once.
    Returns a function opening the form for a new task. */
function drawEventTasks(box, eventId) {
  const setup = state.session.monthSetup;
  const event = setup.events.find((e) => e.id === eventId);
  if (!event) return;
  const redraw = () => drawEventTasks(box, eventId);
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
  const open = (task) => editEventTask({ ...common, task, subtitle: event.name,
    save: async (fields) => { await saveTask(fields, task ? 'Task changed' : 'Task added'); redraw(); },
    remove: task ? () => change('events/tasks/delete', { id: task.id }, 'Task removed') : null });
  const onMain = toggle('Print these tasks on the main rota too', event.tasks_on_main_rota,
    async (e) => {
      const input = e.target;
      const saved = await change('events/save', { event: eventFields(event, { tasks_on_main_rota: input.checked }) },
        input.checked ? 'Its tasks will be printed on the main rota too' : 'Its tasks will be printed on its own rota only');
      if (!saved) input.checked = !input.checked;  // as it still is
    });
  const chooseType = (anchor) => openMenu(anchor,
    types.map((t) => ({ value: t.id, label: `${t.name} — ${t.tasks.map((x) => x.name).join(', ')}` })),
    (typeId) => change('events/tasks/add-type', { event_id: eventId, type_id: typeId }, 'Tasks added'));

  fill(box,
    h('div', { class: 'event-tasks-head' },
      h('h3', {}, `Its own tasks · ${event.tasks.length}`),
      h('span', { class: 'faint' }, 'Needed only on its days. Changes here are saved at once.')),
    h('div', { class: 'main-rota-choice' }, onMain,
      h('span', { class: 'faint' }, 'They are always on the rota to fill (Build) and on the event’s own printout.')),
    event.tasks.length
      ? h('ul', { class: 'event-task-list' }, event.tasks.map((task) => eventTaskRow(task, {
        ...common,
        save: (fields) => saveTask(fields).catch(failed).finally(redraw),  // also after a refusal: shows it as it still is
        open: () => open(task),
        remove: () => change('events/tasks/delete', { id: task.id }, 'Task removed'),
      })))
      : h('p', { class: 'faint' }, 'None yet: anything this event needs that the usual tasks do not cover.'),
    h('div', { class: 'card-actions' },
      h('button', { class: 'btn small', type: 'button', onclick: () => open(null) }, icon('plus'), 'Add a task'),
      types.length ? h('button', { class: 'btn small', type: 'button', onclick: (e) => chooseType(e.currentTarget) }, 'Add the tasks of a type…') : null,
      h('span', { class: 'spacer' }),
      event.tasks.length ? h('button', { class: 'btn small ghost', type: 'button', title: 'So the next event of this type gets these tasks',
        onclick: () => saveAsType(event, ownType, redraw) },
      ownType ? `Update the ${ownType.name} type…` : 'Save as an event type…') : null));
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
        end: end.value, area_ids: areas.getValue(), keep_task_ids: keep.getValue() } }, marker ? 'Saved' : 'Added');
      if (result.missing_months?.length) {
        toast(`Also for ${result.missing_months.map((x) => x.title).join(' and ')}: it will be there when that month is created`);
      }
      dialog.close();
    } catch (error) {
      formError(dialog.panel.querySelector('.modal-body'), errorText(error));
    }
  };
  const remove = async () => {
    await changeMonth('markers/delete', { id: m.id }, 'Removed');
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
    await changeMonth('daytype', { date: day.date, day_type: kind.getValue() || null }, 'Day changed');
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
            onclick: () => changeMonth('regulars/apply', {}, 'Regular week applied') }, icon('repeat'), 'Apply the regular week')),
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
              setup.event_types.find((t) => t.id === e.type_id) ? badge(setup.event_types.find((t) => t.id === e.type_id).name, 'info') : null,
              e.tasks.length ? h('span', { class: 'faint' }, `${e.tasks.length} task${e.tasks.length > 1 ? 's' : ''} of its own`) : null)),
            'Add', () => editEvent(null)),
          listSection('Closures and notes', setup.markers, 'Nothing closed, no note.',
            (m) => h('li', {}, h('button', { class: 'item', type: 'button', onclick: () => editMarker(m) },
              h('strong', {}, m.label), h('span', { class: 'muted' }, range(m)), badge(KINDS.find((k) => k.value === m.kind).label))),
            'Add', () => editMarker(null)))) : null,
      !setup && !info ? h('p', { class: 'empty' }, 'Loading…') : null);
  }

  return { render };
}
