// Tasks & Areas: areas in columns, tasks as cards; one form per task.
import { changes, chipChoice, field, formError, numberInput, openDialog, openDrawer, segmented, select, textInput } from '../components.js';
import { h, replaceKeepingFocus } from '../dom.js';
import { icon } from '../icons.js';
import { errorText, failed, headcountSummary, partsText, PERIOD_LABELS, PRIORITY_LABELS, saveSetup, skillName, timeText, WEEKDAYS } from '../setup.js';
import { api, monthPath } from '../api.js';
import { readOnly, setMonth, setSession, state } from '../state.js';
import { hint } from '../help.js';
import { eventTypesBoard } from './eventtypes.js';
import { changed, needPen } from '../actions.js';

const NEW_SKILL = '__new__';
const TYPICAL = 'typical';

/** The configuration being edited: a month's own tasks, or the typical month. */
async function loadConfig(scope) {
  setSession({ config: await api.get(`/api/config?scope=${encodeURIComponent(scope)}`) });
}

/**
 * Save a change to the configuration on screen. A month's can be undone: `message`
 * (words, or words made from the answer) then comes with Undo; null: nothing said.
 */
async function saveConfig(path, body, message = null) {
  const scope = state.session.config.scope;
  needPen();  // another computer edits: say so, send nothing
  const config = await api.post(`/api/${path}`, { ...body, scope });
  setSession({ config });
  if (state.month && state.month.id === scope) setMonth(await api.get(monthPath(scope)));
  changed(config.history, scope, typeof message === 'function' ? message(config) : message);
  return config;
}

const configOf = () => state.session.config;

// ---- A new skill, with "who can do this?" ---------------------------------

function newSkill(onCreated) {
  const setup = state.session.setup;
  const name = textInput('', { autofocus: true, placeholder: 'e.g. First aid' });
  const who = chipChoice(setup.people.filter((p) => p.active && !p.archived)
    .sort((a, b) => a.name.localeCompare(b.name)).map((p) => ({ value: p.id, label: p.name })), [], { label: 'Who can do this?' });
  const save = async () => {
    try {
      const result = await saveSetup('skills/save', { skill: { name: name.value }, people: who.getValue() }, { message: 'Skill added' });
      dialog.close();
      onCreated(result.catalog.skills.find((s) => s.name.toLowerCase() === name.value.trim().toLowerCase()));
    } catch (error) {
      formError(dialog.panel.querySelector('.modal-body'), errorText(error));
    }
  };
  const dialog = openDialog({ changes: true,
    title: 'New skill', wide: true,
    body: [field('Name', name), h('h3', { class: 'form-section' }, 'Who can do this?'), who],
    actions: [h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: save }, 'Add skill')],
  });
}

// ---- One task -------------------------------------------------------------

export function editTask(task, areaId) {
  const setup = state.session.setup;
  const t = task || { name: '', area_id: areaId, skill_id: null, start: '', end: '', time_label: '',
    hours: 0, headcount: { default: 1 }, priority: 'normal' };
  const name = textInput(t.name, { autofocus: true });
  const area = select(configOf().areas.filter((a) => !a.archived || a.id === t.area_id).map((a) => ({ value: a.id, label: a.name })), t.area_id);
  const start = h('input', { class: 'input', type: 'time', value: t.start || '' });
  const end = h('input', { class: 'input', type: 'time', value: t.end || '' });
  const hours = numberInput(t.hours || '', { min: 0, max: 24, step: 0.25 });
  const label = textInput(t.time_label, { placeholder: 'e.g. 10:00–15:00 at weekends, or Done by 9 am' });
  // Parts of the day: several can be ticked; "Any time" stands alone.
  let partsTouched = false;
  const parts = chipChoice(Object.entries(PERIOD_LABELS).map(([value, text]) => ({ value, label: text })), t.periods || ['morning'], {
    label: 'Parts of the day',
    onchange: (chosen, clicked) => {
      partsTouched = true;
      if (clicked === 'anytime' && chosen.includes('anytime')) parts.setValue(['anytime']);
      else if (clicked !== 'anytime') parts.setValue(chosen.filter((v) => v !== 'anytime'));
    },
  });
  const skillOptions = () => [{ value: '', label: 'Anyone' }, ...state.session.setup.catalog.skills.map((s) => ({ value: s.id, label: s.name })),
    { value: NEW_SKILL, label: 'New skill…' }];
  const skill = select(skillOptions(), t.skill_id || '');
  skill.addEventListener('change', () => {
    if (skill.value !== NEW_SKILL) return;
    skill.value = t.skill_id || '';
    newSkill((created) => {
      if (!created) return;
      skill.replaceChildren(...select(skillOptions(), created.id).children);
      skill.value = created.id;
    });
  });
  const priority = segmented(Object.entries(PRIORITY_LABELS).map(([value, text]) => ({ value, label: text })), t.priority, { label: 'Priority' });

  // How many people, per kind of day.
  const counts = {};
  const countInput = (key, value) => {
    counts[key] = numberInput(value || '', { min: 0, max: 30, step: 1, placeholder: '0', 'aria-label': key });
    return counts[key];
  };
  const usual = (key) => t.headcount[key] ?? t.headcount.default ?? 0;
  const everyDay = numberInput('', { min: 0, max: 30, step: 1, placeholder: '–', 'aria-label': 'Same every day' });
  everyDay.addEventListener('input', () => WEEKDAYS.forEach(([key]) => { counts[key].value = everyDay.value; }));
  const table = h('table', { class: 'count-table' },
    h('thead', {}, h('tr', {}, WEEKDAYS.map(([, text]) => h('th', {}, text)), h('th', { class: 'event-col' }, 'Event day', hint('eventDay')), h('th', {}, 'All days'))),
    h('tbody', {}, h('tr', {},
      WEEKDAYS.map(([key]) => h('td', {}, countInput(key, usual(key) || ''))),
      h('td', { class: 'event-col' }, countInput('event', t.headcount.event)),
      h('td', {}, everyDay))));

  // The program says how long the times make, and which parts of the day they cover.
  let asked = 0;
  const syncHours = async () => {
    const mine = (asked += 1);
    let answer;
    try {
      answer = await api.post('/api/tasks/times', { start: start.value, end: end.value });
    } catch { return; }  // the hours and parts stay as they are; saving says what is wrong, if anything
    if (mine !== asked) return;  // the times changed again meanwhile
    if (answer.hours !== null) hours.value = answer.hours;
    // A new task: tick the parts of the day its times cover, until ticked by hand.
    if (!task && !partsTouched && answer.periods) parts.setValue(answer.periods);
  };
  start.addEventListener('change', syncHours);
  end.addEventListener('change', syncHours);

  const payload = (extra = {}) => {
    const headcount = Object.fromEntries(Object.entries(counts).map(([key, input]) => [key, input.value]).filter(([, v]) => v !== ''));
    return {
      task: { id: t.id, name: name.value, area_id: area.value, start: start.value, end: end.value, hours: hours.value,
        time_label: label.value, periods: parts.getValue(), skill_id: skill.value || null, priority: priority.getValue(),
        headcount, archived: t.archived || false, ...extra },
    };
  };
  const send = async (extra, message) => {
    try {
      await saveConfig('tasks/save', payload(extra), message);
      drawer.close();
    } catch (error) {
      formError(drawer.panel.querySelector('.drawer-body'), errorText(error));
    }
  };
  const duplicate = async () => {
    try {
      await saveConfig('tasks/duplicate', { id: t.id }, 'Task duplicated');
      drawer.close();
    } catch (error) {
      formError(drawer.panel.querySelector('.drawer-body'), errorText(error));
    }
  };

  const drawer = openDrawer({ changes: true,
    title: task ? task.name : 'New task',
    subtitle: `For ${configOf().title}`,
    body: [
      field('Name', name),
      field('Area', area),
      h('h3', { class: 'form-section' }, 'When'),
      h('div', { class: 'form-grid three' }, field('Starts', start), field('Ends', end), field('Hours counted', hours, 'From the times; change it if needed')),
      field('Other times or note', label),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Parts of the day'), parts,
        h('span', { class: 'field-hint' }, 'Morning until 14:00, afternoon 14:00–17:00, evening after 17:00. Someone must be free for every part ticked. "Any time": only a whole day away blocks someone.')),
      h('h3', { class: 'form-section' }, 'Who'),
      field('Skill needed', skill),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Priority'), priority,
        h('span', { class: 'field-hint' }, 'When people are short, essential tasks are filled first and optional ones left open.')),
      h('h3', { class: 'form-section' }, 'How many people?'),
      table,
      h('p', { class: 'field-hint' }, 'On an event day, the larger of the weekday number and the event number applies. Single days can be changed on the rota itself.'),
    ],
    actions: [
      task ? h('button', { class: 'btn ghost', type: 'button', onclick: duplicate }, 'Duplicate') : null,
      task ? h('button', { class: 'btn ghost', type: 'button', onclick: () => send({ archived: !t.archived }, t.archived ? 'Task restored' : 'Task archived') },
        t.archived ? 'Restore' : 'Archive') : null,
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => drawer.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: () => send({}, 'Task saved') }, task ? 'Save' : 'Add task'),
    ],
  });
}

// ---- One area -------------------------------------------------------------

function editArea(area) {
  const setup = state.session.setup;
  const a = area || { name: '', manager: '', color: setup.colours[configOf().areas.length % setup.colours.length] };
  const name = textInput(a.name, { autofocus: true });
  const manager = textInput(a.manager);
  const colour = segmented(setup.colours.map((c) => ({ value: c, label: h('span', { class: ['swatch', `area-${c}`], title: c }) })), a.color, { label: 'Colour' });
  colour.classList.add('swatches');
  const send = async (extra = {}) => {
    try {
      await saveConfig('areas/save', { area: { id: a.id, name: name.value, manager: manager.value, color: colour.getValue(),
        archived: a.archived || false, ...extra } }, 'Area saved');
      dialog.close();
    } catch (error) {
      formError(dialog.panel.querySelector('.modal-body'), errorText(error));
    }
  };
  const dialog = openDialog({ changes: true,
    title: area ? area.name : 'New area', subtitle: `For ${configOf().title}`,
    body: [field('Name', name), field('Manager', manager), h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Colour'), colour)],
    actions: [
      area ? h('button', { class: 'btn ghost', type: 'button', onclick: () => send({ archived: !a.archived }) }, a.archived ? 'Restore' : 'Archive') : null,
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: () => send() }, area ? 'Save' : 'Add area'),
    ],
  });
}

/** Move an area one place among those shown (`step` -1: earlier, 1: later). */
async function moveArea(area, step, shown) {
  const other = shown[shown.findIndex((a) => a.id === area.id) + step];
  if (!other) return;
  const ids = configOf().areas.map((a) => a.id);  // every area, the archived ones too
  const to = ids.indexOf(other.id);
  ids.splice(ids.indexOf(area.id), 1);
  ids.splice(to, 0, area.id);
  try {
    await saveConfig('areas/order', { ids });
  } catch (error) {
    failed(error);
  }
}

// ---- Drag and drop: reorder tasks, or move them to another area -----------

let dragged = null;

async function dropTask(targetArea, beforeTaskId) {
  const task = configOf().tasks.find((t) => t.id === dragged);
  dragged = null;
  if (!task || task.id === beforeTaskId) return;
  try {
    if (task.area_id !== targetArea) await saveConfig('tasks/save', { task: { ...task, area_id: targetArea } });
    const ids = configOf().tasks.map((t) => t.id).filter((id) => id !== task.id);
    const at = beforeTaskId ? ids.indexOf(beforeTaskId) : ids.length;
    ids.splice(at < 0 ? ids.length : at, 0, task.id);
    await saveConfig('tasks/order', { ids });
  } catch (error) {
    failed(error);
  }
}

function taskCard(task, setup) {
  const card = h('button', {
    // Dragged to reorder, unless another computer edits (a click still shows its details).
    type: 'button', class: ['task-card', task.archived && 'archived'], draggable: readOnly() ? 'false' : 'true', dataset: { task: task.id },
    onclick: () => editTask(task),
    ondragstart: (e) => { dragged = task.id; e.dataTransfer.effectAllowed = 'move'; card.classList.add('dragging'); },
    ondragend: () => { dragged = null; card.classList.remove('dragging'); },
    ondragover: (e) => { if (dragged) { e.preventDefault(); card.classList.add('drop-before'); } },
    ondragleave: () => card.classList.remove('drop-before'),
    ondrop: (e) => { e.preventDefault(); e.stopPropagation(); card.classList.remove('drop-before'); dropTask(task.area_id, task.id); },
  },
  h('span', { class: 'task-card-name' }, task.name,
    task.priority !== 'normal' ? h('span', { class: ['prio-tag', task.priority] }, PRIORITY_LABELS[task.priority]) : null),
  h('span', { class: 'task-card-line' }, [timeText(task), task.hours ? `${task.hours} h` : 'not counted', task.periods.includes('anytime') ? null : partsText(task)].filter(Boolean).join(' · ')),
  h('span', { class: 'task-card-line' }, `${skillName(setup, task.skill_id)} · ${headcountSummary(task)}`));
  return card;
}

// ---- Reusing configurations ---------------------------------------------

function useConfiguration() {
  const config = configOf();
  const sources = [
    ...config.scopes.filter((s) => s.id !== config.scope && s.id !== TYPICAL).map((s) => ({ value: `month:${s.id}`, label: s.title })),
    ...config.templates.map((t) => ({ value: `template:${t.id}`, label: `Template: ${t.name}` })),
    { value: TYPICAL, label: 'Typical month' },
  ];
  const source = select(sources, sources[0]?.value, { autofocus: true });
  const go = async () => {
    try {
      const chosen = sources.find((s) => s.value === source.value).label;
      await saveConfig('config/use', { source: source.value }, (result) => `${config.title} now uses the configuration of ${chosen}`
        + (result.removed ? ` · ${result.removed} placement(s) on removed tasks taken off` : ''));
      dialog.close();
    } catch (error) {
      formError(dialog.panel.querySelector('.modal-body'), errorText(error));
    }
  };
  const dialog = openDialog({ changes: true,
    title: `Configuration of ${config.title}`,
    subtitle: 'Take the areas and tasks of another month, a template or the typical month.',
    body: [field('Use the configuration of', source),
      h('p', { class: 'warn-text' }, `This replaces the areas and tasks of ${config.title}. People already placed on tasks that also exist there stay; placements on other tasks are taken off. You can undo it.`)],
    actions: [h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: go }, 'Use it')],
  });
}

function saveAsTemplate() {
  const config = configOf();
  const name = textInput('', { autofocus: true, placeholder: 'e.g. Festival month, Quiet summer…' });
  const remove = async (id) => {
    try {
      await saveConfig('templates/delete', { id }, 'Template deleted');
      dialog.close();
      saveAsTemplate();
    } catch (error) {
      failed(error);
    }
  };
  const save = async () => {
    try {
      await saveConfig('templates/save', { name: name.value }, 'Template saved');
      dialog.close();
    } catch (error) {
      formError(dialog.panel.querySelector('.modal-body'), errorText(error));
    }
  };
  const dialog = openDialog({ changes: true,
    title: 'Save as a task template', subtitle: `Keeps the areas and tasks of ${config.title}, to give them to a month later.`,
    body: [field('Template name', name),
      config.templates.length ? h('div', {}, h('h3', { class: 'form-section' }, 'Templates already saved'),
        h('ul', { class: 'config-template-list' }, config.templates.map((t) => h('li', {},
          h('span', {}, t.name, h('span', { class: 'faint' }, ` · ${t.tasks} tasks · ${t.created}`)),
          h('button', { class: 'btn ghost small danger', type: 'button', onclick: () => remove(t.id) }, 'Delete'))))) : null],
    actions: [h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: save }, 'Save template')],
  });
}

// ---- The screen -----------------------------------------------------------

export function mountTasks(container) {
  const root = h('div', { class: 'page setup-page' });
  container.replaceChildren(root);
  // Open on the month being prepared, unless another one was chosen earlier.
  const firstScope = (state.session.config?.scope === TYPICAL ? TYPICAL : state.month?.id) || state.boot?.current || TYPICAL;
  loadConfig(firstScope).catch(() => loadConfig(TYPICAL));

  function render() {
    const setup = state.session.setup;
    const config = state.session.config;
    if (!setup || !config) return;
    const tab = state.session.tasksTab;
    const tabs = h('div', { class: 'segmented page-tabs', role: 'tablist', 'aria-label': 'What to set up' },
      h('button', { type: 'button', role: 'tab', 'aria-selected': String(tab !== 'types'), 'aria-pressed': String(tab !== 'types'), onclick: () => setSession({ tasksTab: 'tasks' }) }, 'Usual tasks'),
      h('button', { type: 'button', role: 'tab', 'aria-selected': String(tab === 'types'), 'aria-pressed': String(tab === 'types'), onclick: () => setSession({ tasksTab: 'types' }) },
        'Event types', h('span', { class: 'count' }, setup.event_types.length)));
    if (tab === 'types') {
      replaceKeepingFocus(root,
        h('header', { class: 'page-head' },
          h('div', {}, h('h1', {}, 'Tasks & Areas'),
            h('p', { class: 'muted' }, 'Kinds of event and the tasks they need on top of the usual ones. Change people, days or the main rota right on each line.')),
          h('div', { class: 'page-actions' }, tabs)),
        eventTypesBoard());
      return;
    }
    const isTypical = config.scope === TYPICAL;
    const showArchived = state.session.showArchived;
    const areas = config.areas.filter((a) => showArchived || !a.archived);
    const scopePicker = select(config.scopes.map((s) => ({ value: s.id, label: s.title })), config.scope,
      { 'aria-label': 'Configuration', class: 'select scope-select', onchange: (e) => loadConfig(e.target.value) });
    const columns = areas.map((area, index) => {
      const tasks = config.tasks.filter((t) => t.area_id === area.id && (showArchived || !t.archived));
      return h('section', { class: ['area-column', `area-${area.color}`, area.archived && 'archived'],
        ondragover: (e) => { if (dragged) e.preventDefault(); },
        ondrop: (e) => { e.preventDefault(); dropTask(area.id, null); } },
      h('header', { class: 'area-column-head' },
        h('span', { class: 'swatch', 'aria-hidden': 'true' }),
        h('span', { class: 'area-name' }, area.name),
        h('span', { class: 'faint' }, area.manager),
        h('span', { class: 'area-tools' },
          changes(h('button', { class: 'btn ghost icon-only small', type: 'button', disabled: index === 0, dataset: { focusId: `area-earlier-${area.id}` },
            'aria-label': `Move ${area.name} earlier`, title: 'Move earlier (the order on the rota and the printouts)', onclick: () => moveArea(area, -1, areas) }, icon('left'))),
          changes(h('button', { class: 'btn ghost icon-only small', type: 'button', disabled: index === areas.length - 1, dataset: { focusId: `area-later-${area.id}` },
            'aria-label': `Move ${area.name} later`, title: 'Move later', onclick: () => moveArea(area, 1, areas) }, icon('right'))),
          h('button', { class: 'btn ghost icon-only small', type: 'button', 'aria-label': `Edit ${area.name}`, onclick: () => editArea(area) }, icon('sliders')))),
      h('div', { class: 'task-cards' }, tasks.map((task) => taskCard(task, setup))),
      changes(h('button', { class: 'btn ghost add-task', type: 'button', onclick: () => editTask(null, area.id) }, icon('plus'), 'Add a task')));
    });
    replaceKeepingFocus(root,
      h('header', { class: 'page-head' },
        h('div', {}, h('h1', {}, 'Tasks & Areas'),
          h('p', { class: 'muted' }, 'What needs doing, when, by whom and how many. Each month has its own configuration. Drag cards to reorder them or move them to another area; the arrows move an area.')),
        h('div', { class: 'page-actions' },
          tabs,
          changes(h('button', { class: 'btn', type: 'button', onclick: () => newSkill(() => {}) }, icon('plus'), 'New skill')),
          changes(h('button', { class: 'btn primary', type: 'button', onclick: () => editArea(null) }, icon('plus'), 'Add an area')))),
      h('div', { class: 'scope-bar' },
        h('span', { class: 'field-label' }, 'Configuration of'), scopePicker,
        h('span', { class: 'muted scope-note' }, isTypical
          ? 'The starting point when there is no previous month; the regular week uses these tasks.'
          : `Changes apply to ${config.title} only, and can be undone with the arrows at the top, like its rota.`),
        config.event_tasks ? h('a', { class: 'muted', href: '#/setup', title: 'Tasks of an event are edited with their event, in Set up' },
          `+ ${config.event_tasks} task${config.event_tasks > 1 ? 's' : ''} of events`) : null,
        h('span', { class: 'spacer' }),
        isTypical ? null : changes(h('button', { class: 'btn', type: 'button', onclick: useConfiguration }, icon('repeat'), 'Use the configuration of…')),
        changes(h('button', { class: 'btn', type: 'button', onclick: saveAsTemplate }, 'Save as a task template…')),
        h('button', { class: 'btn', 'aria-pressed': String(showArchived), type: 'button',
          onclick: () => setSession({ showArchived: !showArchived }) }, 'Show archived')),
      areas.length ? h('div', { class: 'area-columns', dataset: { scrollId: 'area-columns' } }, columns)
        : h('p', { class: 'empty' }, 'No area yet. Start with an area (Kitchen, Reception…), then add its tasks.'));
  }

  return { render };
}
