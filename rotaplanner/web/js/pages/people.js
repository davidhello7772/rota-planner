// People: everyone with their skills and constraints; skills grid; import.
import { badge, changes, chipChoice, field, formError, numberInput, openDialog, openDrawer, readFileBase64, segmented, select, textInput, toggle } from '../components.js';
import { h, replaceKeepingFocus } from '../dom.js';
import { icon } from '../icons.js';
import { errorText, failed, fullName, saveSetup, WEEKDAYS } from '../setup.js';
import { api } from '../api.js';
import { setSession, state } from '../state.js';
import { toast } from '../ui.js';


const FILTERS = [
  { value: 'active', label: 'On the rota' },
  { value: 'inactive', label: 'Not on the rota' },
  { value: 'all', label: 'Everyone' },
];
const NEVER_PERIODS = [
  { value: 'any', label: 'All day' }, { value: 'morning', label: 'Morning' },
  { value: 'afternoon', label: 'Afternoon' }, { value: 'evening', label: 'Evening' },
];

function matches(person, query) {
  if (!query) return true;
  const text = `${person.name} ${person.first_name} ${person.last_name} ${person.lay_name}`.toLowerCase();
  return query.toLowerCase().split(/\s+/).every((word) => text.includes(word));
}

function visiblePeople(setup) {
  const { query, filter } = state.session.directory;
  return setup.people
    .filter((p) => !p.archived)
    .filter((p) => filter === 'all' || (filter === 'active') === p.active)
    .filter((p) => matches(p, query))
    .sort((a, b) => a.name.localeCompare(b.name));
}

function constraintsText(person) {
  const parts = [];
  if (person.target_hours != null) parts.push(`Target ${person.target_hours} h`);
  if (person.max_hours != null) parts.push(`Max ${person.max_hours} h/month`);
  if (person.max_hours_per_day != null) parts.push(`Max ${person.max_hours_per_day} h/day`);
  if (person.max_tasks_per_day != null) parts.push(`Max ${person.max_tasks_per_day} task${person.max_tasks_per_day > 1 ? 's' : ''}/day`);
  if (person.never.length) parts.push(`${person.never.length} standing absence${person.never.length > 1 ? 's' : ''}`);
  if (person.avoid_task_ids.length) parts.push(`avoids ${person.avoid_task_ids.length} task${person.avoid_task_ids.length > 1 ? 's' : ''}`);
  return parts.join(' · ');
}

// ---- Editing one person ---------------------------------------------------

function neverEditor(rules) {
  const list = h('div', { class: 'rule-list' });
  const addRow = (rule = { weekday: 'mon', period: 'any' }) => {
    const row = h('div', { class: 'rule-row' },
      select(WEEKDAYS.map(([value, label]) => ({ value, label })), rule.weekday, { 'aria-label': 'Day' }),
      select(NEVER_PERIODS, rule.period, { 'aria-label': 'Part of the day' }),
      h('button', { type: 'button', class: 'btn ghost icon-only', 'aria-label': 'Remove', onclick: () => row.remove() }, icon('close')));
    list.append(row);
  };
  rules.forEach(addRow);
  const el = h('div', {}, list, h('button', { type: 'button', class: 'btn small', onclick: () => addRow() }, icon('plus'), 'Add'));
  el.getValue = () => [...list.children].map((row) => {
    const [weekday, period] = row.querySelectorAll('select');
    return { weekday: weekday.value, period: period.value };
  });
  return el;
}

function preferenceEditor(areas, preferences) {
  const rows = areas.filter((a) => !a.archived).map((area) => {
    const choice = segmented(
      [{ value: '', label: '–' }, ...[1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: String(n) }))],
      preferences[area.id] ? String(preferences[area.id]) : '', { label: `Preference for ${area.name}` });
    return { area, choice, el: h('div', { class: 'pref-row' }, h('span', {}, area.name), choice) };
  });
  const el = h('div', { class: 'pref-list' }, rows.map((r) => r.el));
  el.getValue = () => Object.fromEntries(rows.filter((r) => r.choice.getValue()).map((r) => [r.area.id, Number(r.choice.getValue())]));
  return el;
}

export function editPerson(person) {
  const setup = state.session.setup;
  const p = person || { first_name: '', last_name: '', name: '', lay_name: '', phone: '', resident: true, active: true,
    skills: [], preferences: {}, never: [], avoid_task_ids: [], note: '' };
  const first = textInput(p.first_name, { autofocus: true, autocomplete: 'off' });
  const last = textInput(p.last_name, { autocomplete: 'off' });
  const rota = textInput(p.name, { placeholder: 'First name and initial, e.g. Quinn J.', autocomplete: 'off' });
  const lay = textInput(p.lay_name);
  const phone = textInput(p.phone, { type: 'tel' });
  // Write-only: the address is turned into a fingerprint by the program and never shown again.
  const email = textInput('', { type: 'email', autocomplete: 'off',
    placeholder: p.has_email ? 'Known · type a new one to replace it' : 'name@example.org' });
  let forgetEmail = false;
  const emailState = h('span', { class: 'field-hint' });
  const forgetButton = h('button', { class: 'btn small ghost', type: 'button', onclick: () => {
    forgetEmail = true;
    emailState.textContent = 'The address will be forgotten when you save.';
    forgetButton.hidden = true;
  } }, 'Forget it');
  emailState.textContent = p.has_email ? 'An address is known (only as a fingerprint, it cannot be read back).'
    : 'Not kept: only a fingerprint, to recognise their replies to the availability form.';
  forgetButton.hidden = !p.has_email;
  const resident = toggle('Resident (lives at the centre)', p.resident);
  const active = toggle('On the rota', p.active);
  const skills = chipChoice(setup.catalog.skills.map((s) => ({ value: s.id, label: s.name })), p.skills, { label: 'Skills' });
  const target = numberInput(p.target_hours, { min: 0, step: 0.5, placeholder: String(setup.settings.default_target_hours) });
  const maxMonth = numberInput(p.max_hours, { min: 0, step: 0.5, placeholder: 'No limit' });
  const maxDay = numberInput(p.max_hours_per_day, { min: 0, step: 0.5, placeholder: String(setup.settings.default_max_hours_per_day) });
  const tasksDay = numberInput(p.max_tasks_per_day, { min: 1, step: 1, placeholder: 'No limit' });
  const never = neverEditor(p.never);
  const avoid = chipChoice(setup.catalog.tasks.filter((t) => !t.archived).map((t) => ({ value: t.id, label: t.name })), p.avoid_task_ids, { label: 'Tasks to avoid' });
  const prefs = preferenceEditor(setup.catalog.areas, p.preferences);
  const note = h('textarea', { class: 'input textarea', rows: 3 }, p.note);
  const archived = toggle('Remove from the list (kept in past rotas)', p.archived);

  const save = async () => {
    const data = {
      id: p.id, first_name: first.value, last_name: last.value, name: rota.value, lay_name: lay.value, phone: phone.value,
      resident: resident.input.checked, active: active.input.checked, archived: archived.input.checked,
      skills: skills.getValue(), target_hours: target.value, max_hours: maxMonth.value,
      max_hours_per_day: maxDay.value, max_tasks_per_day: tasksDay.value, never: never.getValue(),
      avoid_task_ids: avoid.getValue(), preferences: prefs.getValue(), note: note.value,
      email: email.value, forget_email: forgetEmail,
    };
    try {
      await saveSetup('people/save', { person: data }, { message: `${data.name || data.first_name} saved` });
      drawer.close();
    } catch (error) {
      formError(drawer.panel.querySelector('.drawer-body'), errorText(error));
    }
  };

  const drawer = openDrawer({ changes: true,
    title: person ? person.name : 'Add a person',
    subtitle: person ? fullName(person) : 'Only a first name is needed; the rest can come later.',
    body: [
      h('div', { class: 'form-grid' }, field('First name', first), field('Last name', last)),
      field('Name on the rota', rota, 'Left empty, it is made from the first name and the initial.'),
      h('div', { class: 'form-grid' }, field('Lay name', lay), field('Phone', phone)),
      h('div', { class: 'email-field' }, field('E-mail address', email), h('div', { class: 'email-state' }, emailState, forgetButton)),
      h('div', { class: 'form-row' }, resident, active),
      h('h3', { class: 'form-section' }, 'Skills'), skills,
      h('h3', { class: 'form-section' }, 'Hours'),
      h('div', { class: 'form-grid' },
        field('Target a month', target, 'Empty: the usual target'), field('At most a month', maxMonth),
        field('At most a day', maxDay, 'Empty: the usual limit (a warning only)'), field('Tasks a day, at most', tasksDay)),
      h('h3', { class: 'form-section' }, 'Never available'), never,
      h('h3', { class: 'form-section' }, 'Tasks to avoid'), avoid,
      h('h3', { class: 'form-section' }, 'Likes helping in…'),
      h('p', { class: 'field-hint' }, '1 = rather not, 5 = loves it. Used to break ties when suggesting people.'), prefs,
      h('h3', { class: 'form-section' }, 'Note'),
      h('p', { class: 'field-hint' }, 'Practical notes only. Nothing about health or other private matters: everyone who uses Rota Planner can read it.'),
      note,
      person ? archived : null,
    ],
    actions: [
      h('button', { class: 'btn', type: 'button', onclick: () => drawer.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: save }, person ? 'Save' : 'Add'),
    ],
  });
}

// ---- Importing a list of names --------------------------------------------

const STATUS_WORDS = { new: 'New', exists: 'Already in the list', duplicate: 'Looks like', invalid: 'No name' };

function importPeople() {
  const text = h('textarea', { class: 'input textarea', rows: 8, placeholder: 'One person per line, e.g.\nQuinn Jansen\nMaya Marin\n\nOr paste a table with a header row (First name, Last name, Phone, skills…).' });
  const file = h('input', { type: 'file', accept: '.csv,.txt,.xlsx', class: 'file' });
  const result = h('div', { class: 'import-result' });
  let rows = [];

  // E-mail of someone already in the list: remember it, or keep the one known.
  const emailCell = (row) => {
    if (row.email_invalid) return h('span', { class: 'warn-text' }, 'Not an address');
    if (!row.email_fp) return h('span', { class: 'faint' }, '—');
    const owner = row.email_owner ? h('span', { class: 'warn-text' }, ` Already known for ${row.email_owner}`) : null;
    if (row.status !== 'exists') return h('span', {}, row.email_owner ? '' : 'Will be recognised', owner);
    if (row.email_status === 'recognised') return h('span', { class: 'faint' }, 'Same as known');
    const choice = h('select', { class: 'select', 'aria-label': `E-mail of ${row.name}`,
      onchange: (e) => { row.remember_email = e.target.value === 'remember'; } },
    h('option', { value: 'remember', selected: row.remember_email }, row.email_status === 'new' ? 'Remember it' : 'Remember the new one'),
    h('option', { value: 'keep', selected: !row.remember_email }, row.email_status === 'new' ? 'Do not keep' : 'Keep the one known'));
    return h('span', {}, row.email_status === 'changed' ? h('span', { class: 'warn-text' }, 'Different from the one known ') : null, choice, owner);
  };

  const showPreview = (preview) => {
    // Whether to remember each address comes from the program (remember_email).
    rows = preview.rows.map((row) => ({ ...row, keep: row.status === 'new' }));
    const c = preview.counts;
    result.replaceChildren(
      h('p', { class: 'muted' }, `${c.new} new · ${c.duplicate} to check · ${c.exists} already in the list${c.invalid ? ` · ${c.invalid} without a name` : ''}`),
      h('table', { class: 'table compact' },
        h('thead', {}, h('tr', {}, h('th', {}, 'Add'), h('th', {}, 'Name on the rota'), h('th', {}, 'Full name'), h('th', {}, 'Status'),
          preview.rows.some((r) => r.email_fp || r.email_invalid) ? h('th', {}, 'E-mail') : null)),
        h('tbody', {}, rows.map((row) => h('tr', {},
          h('td', {}, h('input', { type: 'checkbox', checked: row.keep, disabled: row.status === 'exists' || row.status === 'invalid',
            'aria-label': `Add ${row.name}`, onchange: (e) => { row.keep = e.target.checked; } })),
          h('td', {}, row.name || '—'),
          h('td', {}, `${row.first_name} ${row.last_name}`.trim()),
          h('td', {}, badge(row.match ? `${STATUS_WORDS[row.status]} ${row.match}` : STATUS_WORDS[row.status],
            { new: 'ok', exists: '', duplicate: 'warn', invalid: 'bad' }[row.status])),
          preview.rows.some((r) => r.email_fp || r.email_invalid) ? h('td', {}, emailCell(row)) : null)))));
    addButton.disabled = false;
  };

  const check = async () => {
    const body = { text: text.value };
    if (file.files[0]) {
      body.file = await readFileBase64(file.files[0]);
      body.filename = file.files[0].name;
    }
    try {
      showPreview(await api.post('/api/people/import/preview', body));
    } catch (error) {
      formError(result, errorText(error));
    }
  };

  const add = async () => {
    const chosen = rows.filter((r) => r.keep || (r.status === 'exists' && r.remember_email));
    if (!chosen.length) return;
    try {
      const done = await saveSetup('people/import/apply', { rows: chosen });
      dialog.close();
      setSession({ directory: { ...state.session.directory, filter: 'all', query: '' } });
      const remembered = done.remembered ? ` ${done.remembered} e-mail address${done.remembered > 1 ? 'es' : ''} remembered.` : '';
      toast(done.added ? `${done.added} people added. Tick their skills in the skills grid.${remembered}` : `Nobody added.${remembered}`);
    } catch (error) {
      formError(result, errorText(error));
    }
  };

  const addButton = h('button', { class: 'btn primary', type: 'button', disabled: true, onclick: add }, 'Save');
  const dialog = openDialog({ changes: true,
    title: 'Import people', subtitle: 'Paste names, or choose a CSV or Excel file. Nothing is added before you confirm.',
    wide: true,
    body: [field('Names', text), field('Or a file', file), h('button', { class: 'btn', type: 'button', onclick: check }, 'Check the list'), result],
    actions: [h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'), addButton],
  });
}

// ---- The screen -----------------------------------------------------------

function listView(setup, people) {
  const skills = new Map(setup.catalog.skills.map((s) => [s.id, s.name]));
  return h('table', { class: 'table people-table' },
    h('thead', {}, h('tr', {}, h('th', {}, 'Name on the rota'), h('th', {}, 'Skills'), h('th', {}, 'Hours and limits'), h('th', {}, 'Note'))),
    h('tbody', {}, people.map((person) => h('tr', { class: ['clickable', !person.active && 'inactive'], tabindex: '0',
      onclick: () => editPerson(person), onkeydown: (e) => { if (e.key === 'Enter') editPerson(person); } },
    h('td', {}, h('strong', {}, person.name), h('span', { class: 'sub' }, fullName(person)),
      !person.resident ? badge('Non-resident') : null, !person.active ? badge('Not on the rota') : null),
    h('td', {}, h('span', { class: 'tags' }, person.skills.slice(0, 5).map((id) => h('span', {}, skills.get(id) || '?')),
      person.skills.length > 5 ? h('span', {}, `+${person.skills.length - 5}`) : null,
      !person.skills.length ? h('span', { class: 'faint' }, 'No skill yet') : null)),
    h('td', { class: 'muted' }, constraintsText(person) || '—'),
    h('td', { class: 'muted note-cell' }, person.note || '')))));
}

/** One skill: its name, or deleting it (the program refuses while a task still needs it). */
function editSkill(skill, holders) {
  const name = textInput(skill.name, { autofocus: true });
  const fail = (error) => formError(dialog.panel.querySelector('.modal-body'), errorText(error));
  const save = async () => {
    try {
      await saveSetup('skills/save', { skill: { id: skill.id, name: name.value } }, { message: 'Skill saved' });
      dialog.close();
    } catch (error) { fail(error); }
  };
  let sure = false;
  const remove = h('button', { class: 'btn ghost danger', type: 'button', onclick: async () => {
    if (!sure) {  // asked twice: it comes off everyone who has it
      sure = true;
      remove.textContent = 'Yes, delete this skill';
      return;
    }
    try {
      await saveSetup('skills/delete', { id: skill.id }, { message: `${skill.name} deleted` });
      dialog.close();
    } catch (error) { fail(error); }
  } }, 'Delete…');
  const dialog = openDialog({ changes: true,
    title: skill.name, subtitle: `Held by ${holders} of the people on the rota`,
    body: [field('Name', name),
      h('p', { class: 'field-hint' }, 'Deleting a skill takes it off everyone who has it. A skill that a task still needs cannot be deleted: change those tasks first.')],
    actions: [remove, h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: save }, 'Save')],
  });
}

function gridView(setup, people) {
  const skills = setup.catalog.skills;
  const counts = new Map(skills.map((s) => [s.id, setup.people.filter((p) => p.active && !p.archived && p.skills.includes(s.id)).length]));
  const toggleSkill = async (person, skill, button) => {
    const on = button.getAttribute('aria-pressed') !== 'true';
    button.setAttribute('aria-pressed', String(on));
    try {
      await saveSetup('people/skill', { person_id: person.id, skill_id: skill.id, on });
    } catch (error) {
      button.setAttribute('aria-pressed', String(!on));
      failed(error);
    }
  };
  return h('div', { class: 'skills-scroll', dataset: { scrollId: 'skills' } }, h('table', { class: 'table skills-grid' },
    h('thead', {}, h('tr', {}, h('th', { class: 'sticky' }, 'Person'),
      skills.map((s) => h('th', { class: 'skill-head' },
        h('button', { class: 'skill-name', type: 'button', title: `${s.name}: rename or delete`, 'aria-label': `${s.name}: rename or delete`,
          onclick: () => editSkill(s, counts.get(s.id)) }, h('span', {}, s.name)),
        h('small', {}, String(counts.get(s.id))))))),
    h('tbody', {}, people.map((person) => h('tr', { class: !person.active && 'inactive' },
      h('th', { class: 'sticky', scope: 'row' }, h('button', { class: 'link', type: 'button', onclick: () => editPerson(person) }, person.name)),
      skills.map((skill) => h('td', {}, changes(h('button', {
        type: 'button', class: 'tick', 'aria-pressed': String(person.skills.includes(skill.id)),
        'aria-label': `${person.name}: ${skill.name}`,
        onclick: (e) => toggleSkill(person, skill, e.currentTarget),
      })))))))));
}

export function mountPeople(container) {
  const root = h('div', { class: 'page setup-page' });
  container.replaceChildren(root);

  function render() {
    const setup = state.session.setup;
    if (!setup) return;
    const { directory } = state.session;
    const people = visiblePeople(setup);
    const active = setup.people.filter((p) => p.active && !p.archived).length;
    const search = h('input', { class: 'input', type: 'search', placeholder: 'Find someone', value: directory.query,
      'aria-label': 'Find someone', dataset: { focusId: 'directory-search' },
      oninput: (e) => setSession({ directory: { ...directory, query: e.target.value } }) });

    replaceKeepingFocus(root,
      h('header', { class: 'page-head' },
        h('div', {}, h('h1', {}, 'People'), h('p', { class: 'muted' }, `${active} on the rota · ${setup.people.length - active} not on the rota`)),
        h('div', { class: 'page-actions' },
          changes(h('button', { class: 'btn', type: 'button', onclick: importPeople }, icon('users'), 'Import people')),
          changes(h('button', { class: 'btn primary', type: 'button', onclick: () => editPerson(null) }, icon('plus'), 'Add a person')))),
      h('div', { class: 'page-tools' },
        h('label', { class: 'search' }, icon('search'), search),
        segmented(FILTERS, directory.filter, { label: 'Show', onchange: (filter) => setSession({ directory: { ...directory, filter } }) }),
        h('span', { class: 'spacer' }),
        segmented([{ value: 'list', label: 'List' }, { value: 'grid', label: 'Skills grid' }], directory.view,
          { label: 'View', onchange: (view) => setSession({ directory: { ...directory, view } }) })),
      people.length
        ? (directory.view === 'grid' ? gridView(setup, people) : listView(setup, people))
        : h('p', { class: 'empty' }, directory.query ? 'Nobody matches.' : 'Nobody here yet. Add people one by one, or import a list.'));
  }

  return { render };
}
