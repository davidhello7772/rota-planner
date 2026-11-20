// Availability: bring in the form's replies, see who has not replied, read
// the comments, and correct anyone's days by hand.
import { api, monthPath } from '../api.js';
import { badge, field, formError, openDialog, openDrawer, readFileBase64, toggle } from '../components.js';
import { fill, formatDate, h, replaceKeepingFocus } from '../dom.js';
import { icon } from '../icons.js';
import { errorText } from '../setup.js';
import { setMonth, setSession, state } from '../state.js';
import { copyText, toast } from '../ui.js';

const PERIODS = [['morning', 'M', 'Morning'], ['afternoon', 'A', 'Afternoon'], ['evening', 'E', 'Evening']];
const MATCH_WORDS = { exact: ['Found', 'ok'], close: ['Check', 'warn'], none: ['Not found', 'bad'] };

let loading = null;  // month id being fetched, so a render does not ask twice

async function loadAvailability() {
  if (!state.month || loading === state.month.id) return;
  loading = state.month.id;
  try {
    setSession({ availability: await api.get(monthPath(loading, '/availability')) });
  } finally {
    loading = null;
  }
}

async function send(path, body, message) {
  const result = await api.post(monthPath(state.month.id, `/availability/${path}`), body);
  setSession({ availability: result });
  setMonth(await api.get(monthPath(state.month.id)));
  if (message) toast(message, { actionLabel: 'Undo', onAction: undoAvailability });
  return result;
}

async function undoAvailability() {
  setMonth(await api.post(monthPath(state.month.id, '/undo')));
  await loadAvailability();
}

// ---- Importing the form's replies -----------------------------------------

function importReplies() {
  const people = state.session.availability.people;
  const text = h('textarea', { class: 'input textarea', rows: 7,
    placeholder: 'In the form\'s spreadsheet, select everything (Ctrl+A), copy, and paste here.\nThe first row must hold the column names (Name, Morning, Afternoon, Evening, Comment).' });
  const file = h('input', { type: 'file', accept: '.csv,.tsv,.txt,.xlsx', class: 'file' });
  const result = h('div', { class: 'import-result' });
  let rows = [];

  const byId = new Map(people.map((p) => [p.id, p]));

  // The reply's e-mail address, seen only as a fingerprint. What it is for the matched
  // person (email_status) and whether to remember it come from the program; a person
  // chosen by hand shows the choice, set to "do not keep".
  const emailCell = (row) => {
    if (!row.email_fp || !row.person_id) return h('span', { class: 'faint' }, '—');
    if (row.email_status === 'recognised') return h('span', { class: 'ok-text' }, 'Recognised');
    const known = row.email_status === 'changed' || (!row.email_status && byId.get(row.person_id)?.has_email);
    const owner = row.email_owner && row.email_owner !== row.person_id ? byId.get(row.email_owner)?.name || 'someone else' : null;
    return h('span', { class: 'email-choice' },
      known ? h('span', { class: 'warn-text' }, 'Different from the one known') : null,
      owner ? h('span', { class: 'warn-text' }, `Known for ${owner}`) : null,
      h('select', { class: 'select', 'aria-label': `E-mail of ${row.name}`, onchange: (e) => { row.remember_email = e.target.value === 'remember'; } },
        h('option', { value: 'remember', selected: row.remember_email }, known ? 'Remember the new one' : 'Remember it'),
        h('option', { value: 'keep', selected: !row.remember_email }, known ? 'Keep the one known' : 'Do not keep')));
  };

  const personSelect = (row, cell) => h('select', { class: 'select', 'aria-label': `Person for ${row.name}`,
    onchange: (e) => {
      row.person_id = e.target.value || null;
      row.email_status = row.email_owner && row.email_owner === row.person_id ? 'recognised' : '';
      row.remember_email = false;
      if (cell) cell.replaceChildren(emailCell(row));
    } },
  h('option', { value: '' }, '— leave out —'),
  row.suggestions.length ? h('optgroup', { label: 'Suggested' },
    row.suggestions.map((id) => h('option', { value: id, selected: id === row.person_id }, people.find((p) => p.id === id)?.name || id))) : null,
  h('optgroup', { label: 'Everyone' }, people.map((p) => h('option', { value: p.id, selected: !row.suggestions.length && p.id === row.person_id }, p.name))));

  const show = (preview) => {
    rows = preview.rows.map((row) => ({ ...row, keep: !row.replaced && row.match !== 'none' }));
    const withEmail = rows.some((row) => row.email_fp);
    const c = preview.counts;
    fill(result, 
      h('p', { class: 'muted' }, `${c.replies} replies: ${c.exact} found, ${c.check} to check, ${c.unknown} not found`
        + (c.replaced ? `, ${c.replaced} replaced by a later reply` : '')),
      c.mismatches ? h('p', { class: 'warn-text' }, `${c.mismatches} day(s) had a weekday that does not match ${state.month.title}: are these replies for this month?`) : null,
      h('div', { class: 'table-scroll' }, h('table', { class: 'table compact' },
        h('thead', {}, h('tr', {}, h('th', {}, 'Use'), h('th', {}, 'Name typed'), h('th', {}, 'Person'),
          withEmail ? h('th', {}, 'E-mail') : null, h('th', {}, 'Days away'), h('th', {}, 'Comment'))),
        h('tbody', {}, rows.map((row) => {
          const cell = withEmail ? h('td', {}, emailCell(row)) : null;
          return h('tr', { class: row.replaced && 'inactive' },
            h('td', {}, h('input', { type: 'checkbox', checked: row.keep, 'aria-label': `Use the reply of ${row.name}`,
              onchange: (e) => { row.keep = e.target.checked; } })),
            h('td', {}, row.name, ' ', badge(...(row.replaced ? ['Replaced', ''] : MATCH_WORDS[row.match]))),
            h('td', {}, personSelect(row, cell)),
            cell,
            h('td', { class: 'muted' }, PERIODS.map(([key, letter]) => `${letter} ${row.unavailable.filter((k) => k.endsWith(key)).length}`).join(' · ')),
            h('td', { class: 'muted note-cell' }, row.comment));
        })))));
    apply.disabled = false;
  };

  const check = async () => {
    const body = { text: text.value };
    if (file.files[0]) {
      body.file = await readFileBase64(file.files[0]);
      body.filename = file.files[0].name;
    }
    try {
      show(await api.post(monthPath(state.month.id, '/availability/preview'), body));
    } catch (error) {
      formError(result, errorText(error));
    }
  };
  const save = async () => {
    const replies = rows.filter((r) => r.keep && r.person_id);
    try {
      const done = await send('apply', { replies }, null);
      dialog.close();
      const remembered = done.remembered ? ` · ${done.remembered} e-mail address${done.remembered > 1 ? 'es' : ''} remembered` : '';
      toast(`${done.saved} replies saved${remembered}`, { actionLabel: 'Undo', onAction: undoAvailability });
    } catch (error) {
      formError(result, errorText(error));
    }
  };
  const apply = h('button', { class: 'btn primary', type: 'button', disabled: true, onclick: save }, 'Save the ticked replies');
  const dialog = openDialog({
    title: `Availability replies for ${state.month.title}`, wide: true,
    subtitle: 'Each reply replaces what was known for that person. Nothing changes before you save.',
    body: [field('Paste the replies', text), field('Or a file (CSV or Excel)', file),
      h('button', { class: 'btn', type: 'button', onclick: check }, 'Check the replies'), result],
    actions: [h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'), apply],
  });
}

// ---- One person's days ----------------------------------------------------

function editPerson(person) {
  const data = state.session.availability;
  const away = new Set(person.unavailable);
  // Entering someone's days by hand means they told you: count it as a reply.
  const replied = toggle('Has replied (or told you)', true);
  const offset = (new Date(`${data.dates[0]}T12:00:00`).getDay() + 6) % 7;
  const cell = (iso) => h('div', { class: 'avail-day' },
    h('span', { class: 'avail-num' }, Number(iso.slice(-2))),
    PERIODS.map(([key, letter, label]) => h('button', {
      type: 'button', class: 'avail-part', 'aria-pressed': String(away.has(`${iso}/${key}`)),
      'aria-label': `${formatDate(iso)} ${label}: away`, title: label,
      onclick: (e) => {
        const k = `${iso}/${key}`;
        if (away.has(k)) away.delete(k); else away.add(k);
        e.currentTarget.setAttribute('aria-pressed', String(away.has(k)));
      },
    }, letter)));
  const grid = h('div', { class: 'avail-calendar' },
    ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => h('div', { class: 'cal-head' }, d)),
    Array.from({ length: offset }, () => h('div', {})),
    data.dates.map(cell));
  const save = async () => {
    try {
      await send('set', { person_id: person.id, unavailable: [...away], responded: replied.input.checked }, `${person.name} saved`);
      drawer.close();
    } catch (error) {
      formError(drawer.panel.querySelector('.drawer-body'), errorText(error));
    }
  };
  const drawer = openDrawer({
    title: person.name, subtitle: `${person.full} · click M, A or E for the parts of a day they are away`,
    body: [replied, grid, person.comment ? h('blockquote', { class: 'comment' }, person.comment) : null],
    actions: [h('button', { class: 'btn', type: 'button', onclick: () => drawer.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: save }, 'Save')],
  });
}

// ---- Comments: read them, then delete them ----------------------------------

function forgetComments(count) {
  const dialog = openDialog({
    title: `Delete the ${count} comment${count > 1 ? 's' : ''}?`,
    subtitle: 'Comments can mention health or other private matters, so they are not kept longer than needed.',
    body: [h('p', {}, 'They are deleted from this month, its backups and its undo history. This cannot be undone.'),
      h('p', { class: 'muted' }, 'The days people are away stay, so the rota is not affected.')],
    actions: [h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary danger', type: 'button', onclick: async () => {
        try {
          const result = await api.post(monthPath(state.month.id, '/availability/forget-comments'), {});
          setSession({ availability: result });
          setMonth(await api.get(monthPath(state.month.id)));
          dialog.close();
          toast(`${result.forgotten} comment${result.forgotten === 1 ? '' : 's'} deleted`);
        } catch (error) {
          formError(dialog.panel.querySelector('.modal-body'), errorText(error));
        }
      } }, 'Delete them')],
  });
}

function commentsCard(data, comments) {
  const toRead = comments.filter((p) => !p.comment_done);
  return h('section', { class: 'card side-card comments-card' },
    h('div', { class: 'card-title' }, h('h2', {}, 'Comments'),
      h('span', { class: 'faint' }, comments.length ? `${toRead.length} to read of ${comments.length}` : '')),
    comments.length ? h('ul', { class: 'comment-list' }, comments.map((p) => h('li', { class: p.comment_done && 'done' },
      h('input', { type: 'checkbox', checked: p.comment_done, 'aria-label': `Dealt with ${p.name}'s comment`, title: 'Dealt with',
        onchange: (e) => send('comment', { person_id: p.id, done: e.target.checked }) }),
      h('div', {}, h('button', { class: 'link', type: 'button', onclick: () => editPerson(p) }, p.name), h('p', {}, p.comment)))))
      : h('p', { class: 'faint' }, 'No comment.'),
    h('div', { class: 'privacy-note' },
      h('span', {}, `Comments can be private (health…): they are deleted after ${formatDate(data.comments_until)}.`),
      h('button', { class: 'btn small ghost danger', type: 'button', disabled: !comments.length,
        onclick: () => forgetComments(comments.length) }, 'Delete all')));
}

function silentCard(silent) {
  return h('section', { class: 'card side-card' },
    h('div', { class: 'card-title' }, h('h2', {}, `No reply · ${silent.length}`),
      silent.length ? h('button', { class: 'btn small', type: 'button',
        onclick: () => copyText(silent.map((p) => p.name).join(', '), 'Names copied: paste them in a reminder message') }, 'Copy names') : null),
    silent.length ? h('p', { class: 'name-cloud' }, silent.map((p) => h('button', { class: 'link', type: 'button', onclick: () => editPerson(p) }, p.name)))
      : h('p', { class: 'ok-text' }, 'Everyone has replied.'));
}

// ---- The people × days table ---------------------------------------------------

const WEEKDAY = new Intl.DateTimeFormat('en-GB', { weekday: 'narrow' });
const FILTERS = [
  { value: 'all', label: 'Everyone', test: () => true },
  { value: 'silent', label: 'No reply', test: (p) => !p.replied },
  { value: 'comment', label: 'With a comment', test: (p) => p.comment },
  { value: 'away', label: 'Away some days', test: (p) => p.unavailable.length },
];

function matrix(data, people) {
  return h('div', { class: 'avail-scroll', dataset: { scrollId: 'availability' } }, h('table', { class: 'avail-matrix' },
    h('thead', {}, h('tr', {},
      h('th', { class: 'sticky' }, `${people.length} ${people.length === 1 ? 'person' : 'people'}`),
      data.dates.map((iso) => {
        const day = new Date(`${iso}T12:00:00`);
        return h('th', { class: day.getDay() % 6 === 0 && 'weekend', title: formatDate(iso) },
          h('span', { class: 'wd' }, WEEKDAY.format(day)), h('b', {}, day.getDate()));
      }),
      h('th', { class: 'away-count', title: 'Days with at least one part away' }, 'Away'))),
    h('tbody', {}, people.map((person) => {
      const away = new Set(person.unavailable);
      const daysAway = new Set(person.unavailable.map((k) => k.slice(0, 10))).size;
      return h('tr', { class: !person.replied && 'silent', onclick: () => editPerson(person), title: `${person.full}: click to change`,
        tabindex: '0', 'aria-label': `${person.full}: change their days`,
        onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); editPerson(person); } } },
        h('th', { class: 'sticky', scope: 'row' },
          h('span', { class: 'who' }, person.name),
          person.comment ? h('span', { class: ['has-comment', person.comment_done && 'done'], title: person.comment }, icon('message')) : null,
          !person.replied ? h('span', { class: 'faint' }, 'no reply') : null),
        data.dates.map((iso) => h('td', {}, h('span', { class: 'avail-mini' },
          PERIODS.map(([key]) => h('span', { class: away.has(`${iso}/${key}`) ? 'away' : '' }))))),
        h('td', { class: 'away-count' }, daysAway || ''));
    })),
    people.length ? null : h('caption', {}, 'Nobody matches.')));
}

export function mountAvailability(container) {
  const root = h('div', { class: 'page setup-page avail-page' });
  container.replaceChildren(root);
  setSession({ availability: null });  // people may have been added or changed elsewhere

  function render() {
    const data = state.session.availability;
    if (!state.month) {
      fill(root, h('p', { class: 'empty' }, 'Create the month first, in Set up.'));
      return;
    }
    if (!data || data.title !== state.month.title) {
      loadAvailability().catch((error) => toast(errorText(error), { tone: 'bad' }));
      return;
    }
    const query = (state.session.availabilityQuery || '').toLowerCase();
    const filter = FILTERS.find((f) => f.value === state.session.availabilityFilter) || FILTERS[0];
    const people = data.people.filter((p) => filter.test(p)
      && (!query || p.name.toLowerCase().includes(query) || p.full.toLowerCase().includes(query)));
    const silent = data.people.filter((p) => !p.replied);
    const comments = data.people.filter((p) => p.comment);
    const counts = Object.fromEntries(FILTERS.map((f) => [f.value, data.people.filter((p) => f.test(p)).length]));

    replaceKeepingFocus(root,
      h('header', { class: 'page-head compact' },
        h('h1', {}, `Availability — ${data.title}`),
        h('span', { class: 'head-stat' }, h('strong', {}, `${data.people.length - silent.length}/${data.people.length}`), ' replied'),
        h('span', { class: 'faint' }, 'No reply: treated as available.'),
        h('span', { class: 'legend' }, h('span', { class: 'avail-mini' }, h('span', { class: 'away' }), h('span', {}), h('span', {})),
          'Each day: morning, afternoon, evening · red = away'),
        h('div', { class: 'page-actions' },
          h('button', { class: 'btn primary', type: 'button', onclick: importReplies }, icon('users'), 'Import replies'),
          h('a', { class: 'btn', href: '#/build' }, 'Next: build the rota', icon('right')))),
      data.away_regulars ? h('div', { class: 'notice warn' },
        h('span', {}, `${data.away_regulars} shift(s) from the regular week fall on days the person is away.`),
        h('button', { class: 'btn small', type: 'button', onclick: async () => {
          const result = await send('free-regulars', {}, null);
          toast(`${result.freed} shift(s) freed: they show “cover needed” on the rota`, { actionLabel: 'Undo', onAction: undoAvailability });
        } }, 'Free them')) : null,
      h('div', { class: 'avail-body' },
        h('section', { class: 'avail-main' },
          h('div', { class: 'page-tools' },
            h('div', { class: 'segmented', role: 'group', 'aria-label': 'Show' }, FILTERS.map((f) => h('button', {
              type: 'button', 'aria-pressed': String(f === filter), onclick: () => setSession({ availabilityFilter: f.value }),
            }, f.label, h('span', { class: 'count' }, counts[f.value])))),
            h('label', { class: 'search' }, icon('search'), h('input', { class: 'input', type: 'search', placeholder: 'Find someone',
              value: state.session.availabilityQuery || '', dataset: { focusId: 'availability-search' }, 'aria-label': 'Find someone',
              oninput: (e) => setSession({ availabilityQuery: e.target.value }) })),
),
          matrix(data, people)),
        h('aside', { class: 'avail-side' }, commentsCard(data, comments), silentCard(silent))));
  }

  return { render };
}
