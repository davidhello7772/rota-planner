// Regular week: who does the same task on the same weekday, to pre-fill months.
import { field, formError, openDialog, select } from '../components.js';
import { h } from '../dom.js';
import { icon } from '../icons.js';
import { errorText, personById, saveSetup, timeText, usualCount, WEEKDAYS } from '../setup.js';
import { state } from '../state.js';

const DAY_NAMES = {
  mon: 'Monday', tue: 'Tuesday', wed: 'Wednesday', thu: 'Thursday', fri: 'Friday', sat: 'Saturday', sun: 'Sunday',
};

function editRegular(task, weekday, position, current) {
  const setup = state.session.setup;
  const active = setup.people.filter((p) => p.active && !p.archived).sort((a, b) => a.name.localeCompare(b.name));
  const trained = active.filter((p) => !task.skill_id || p.skills.includes(task.skill_id));
  const others = active.filter((p) => !trained.includes(p));
  const person = h('select', { class: 'select', autofocus: true },
    h('option', { value: '' }, 'Nobody'),
    h('optgroup', { label: task.skill_id ? 'Trained for this task' : 'Everyone' },
      trained.map((p) => h('option', { value: p.id, selected: p.id === current?.person_id }, p.name))),
    others.length ? h('optgroup', { label: 'Not trained' },
      others.map((p) => h('option', { value: p.id, selected: p.id === current?.person_id }, p.name))) : null);
  const frequency = select(Object.entries(setup.frequencies).map(([value, label]) => ({ value, label })), current?.frequency || 'weekly');
  const start = h('input', { class: 'input', type: 'date', value: current?.start || '' });
  const end = h('input', { class: 'input', type: 'date', value: current?.end || '' });
  const day = DAY_NAMES[weekday];

  const send = async (personId) => {
    try {
      await saveSetup('regulars/set', { task_id: task.id, weekday, position, person_id: personId,
        frequency: frequency.value, start: start.value, end: end.value }, { message: personId ? 'Regular saved' : 'Regular removed' });
      dialog.close();
    } catch (error) {
      formError(dialog.panel.querySelector('.modal-body'), errorText(error));
    }
  };
  const dialog = openDialog({
    title: `${task.name} — ${day}s`,
    subtitle: [timeText(task), position ? `position ${position + 1}` : ''].filter(Boolean).join(' · '),
    body: [field('Person', person), field('How often', frequency),
      h('div', { class: 'form-grid' }, field('From', start, 'Optional'), field('Until', end, 'Optional'))],
    actions: [
      current ? h('button', { class: 'btn ghost danger', type: 'button', onclick: () => send('') }, 'Remove') : null,
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: () => send(person.value) }, 'Save'),
    ],
  });
}

export function mountRegular(container) {
  const root = h('div', { class: 'page setup-page' });
  container.replaceChildren(root);

  function render() {
    const setup = state.session.setup;
    if (!setup) return;
    const people = personById(setup);
    const regulars = new Map(setup.catalog.regulars.map((r) => [`${r.task_id}|${r.weekday}|${r.position}`, r]));

    const body = [];
    for (const area of setup.catalog.areas.filter((a) => !a.archived)) {
      const tasks = setup.catalog.tasks.filter((t) => t.area_id === area.id && !t.archived);
      const rows = [];
      for (const task of tasks) {
        const taken = setup.catalog.regulars.filter((r) => r.task_id === task.id).map((r) => r.position + 1);
        const positions = Math.max(0, ...WEEKDAYS.map(([key]) => usualCount(task, key)), ...taken);
        for (let position = 0; position < positions; position += 1) {
          rows.push(h('tr', { class: `area-${area.color}` },
            h('th', { scope: 'row', class: 'task-label' },
              h('span', { class: 'task-name' }, task.name, positions > 1 ? h('span', { class: 'faint' }, ` #${position + 1}`) : null),
              h('span', { class: 'task-meta' }, timeText(task))),
            WEEKDAYS.map(([key, label]) => {
              const regular = regulars.get(`${task.id}|${key}|${position}`);
              const needed = usualCount(task, key) > position;
              if (!needed && !regular) return h('td', { class: 'not-needed', title: `Not usually needed on ${label}s` });
              const person = regular && people.get(regular.person_id);
              return h('td', {}, h('button', {
                type: 'button', class: ['regular-cell', regular && 'set', regular && !needed && 'stale'],
                'aria-label': `${task.name}, ${label}: ${person ? person.name : 'nobody'}`,
                onclick: () => editRegular(task, key, position, regular),
              },
              person ? h('span', {}, person.name) : h('span', { class: 'faint' }, '+'),
              regular && regular.frequency !== 'weekly' ? h('small', {}, setup.frequencies[regular.frequency]) : null));
            })));
        }
      }
      if (rows.length) {
        body.push(h('tbody', {},
          h('tr', { class: `area-row area-${area.color}` }, h('th', { colspan: 8 }, h('span', { class: 'area-head' }, h('span', { class: 'area-name' }, area.name)))),
          rows));
      }
    }

    const load = setup.regular_load;  // hours a month from the regular week, counted by the program
    root.replaceChildren(
      h('header', { class: 'page-head' },
        h('div', {}, h('h1', {}, 'Regular week'),
          h('p', { class: 'muted' }, 'Who does the same task on the same weekday. Each new month starts from this week; people who are away are skipped and marked “cover needed”.'))),
      h('div', { class: 'regular-layout' },
        h('div', { class: 'regular-scroll' }, h('table', { class: 'table regular-table' },
          h('thead', {}, h('tr', {}, h('th', {}, 'Task'), WEEKDAYS.map(([, label]) => h('th', {}, label)))),
          body)),
        h('aside', { class: 'regular-side' },
          h('h3', {}, 'From the regular week'),
          h('p', { class: 'field-hint' }, 'About how many hours a month each person gets before anything else is added.'),
          load.length ? h('ul', { class: 'bars' }, load.map((entry) => {
            const person = people.get(entry.person_id);
            return h('li', { class: 'bar-row static' },
              h('span', { class: 'name' }, person ? person.name : '?'),
              h('span', { class: 'faint' }, `${entry.shifts}×`),
              h('span', { class: 'num' }, `≈${entry.hours} h`));
          })) : h('p', { class: 'faint' }, 'Nobody yet. Click a day to choose someone.'),
          h('p', { class: 'field-hint' }, icon('info'), ' Grey days: the task is not usually needed that weekday.'))));
  }

  return { render };
}
