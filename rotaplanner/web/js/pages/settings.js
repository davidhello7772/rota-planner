// Settings: usual hours, the schedule message, e-mail, other computers, moving the data, the program, and importing the current Google Sheets workbook.
import { field, formError, numberInput, readFileBase64, textInput, toggle } from '../components.js';
import { otherComputersCard } from '../syncsetup.js';
import { updateCard } from '../updates.js';
import { mailCard } from '../mailer.js';
import { fill, h } from '../dom.js';
import { api } from '../api.js';
import { errorText, saveSetup } from '../setup.js';
import { setSession, state } from '../state.js';
import { icon } from '../icons.js';
import { copyText, toast } from '../ui.js';

function settingsCard(setup) {
  const s = setup.settings;
  const centre = textInput(s.centre_name);
  const target = numberInput(s.default_target_hours, { min: 0, step: 0.5 });
  const daily = numberInput(s.default_max_hours_per_day, { min: 0, step: 0.5 });
  const prorate = toggle('Lower the target of people away part of the month', s.prorate_target);
  const card = h('section', { class: 'card' },
    h('h2', {}, 'Usual hours'),
    field('Name of the centre', centre),
    h('div', { class: 'form-grid' },
      field('Target hours a month', target, 'For everyone without their own target'),
      field('Usual hours a day, at most', daily, 'Going over it shows a warning')),
    prorate,
    h('div', { class: 'card-actions' }, h('button', { class: 'btn primary', type: 'button', onclick: async () => {
      try {
        await saveSetup('settings/save', { settings: { centre_name: centre.value, default_target_hours: target.value,
          default_max_hours_per_day: daily.value, prorate_target: prorate.input.checked } }, { message: 'Settings saved' });
        if (state.boot) { state.boot.centre = centre.value.trim(); setSession({}); }
      } catch (error) {
        formError(card, errorText(error));
      }
    } }, 'Save')));
  return card;
}

/** The words around each person's schedule when it is copied for an e-mail. */
function messageCard(setup) {
  const s = setup.settings;
  const greeting = textInput(s.message_greeting, { placeholder: 'Hello {name},' });
  const closing = textInput(s.message_closing, { placeholder: 'Thank you!' });
  const subject = textInput(s.message_subject, { placeholder: 'Your shifts for {month}' });
  // The example is written by the program, exactly as a real message would be.
  const preview = h('pre', { class: 'finder-text', 'aria-live': 'polite' });
  let wait = 0;
  let asked = 0;
  const show = async () => {
    const mine = (asked += 1);
    try {
      const answer = await api.post('/api/settings/message/preview',
        { message_greeting: greeting.value, message_closing: closing.value });
      if (mine === asked) preview.textContent = answer.text;  // a later keystroke may have answered first
    } catch { /* the example simply stays as it was */ }
  };
  const later = () => { clearTimeout(wait); wait = setTimeout(show, 250); };
  greeting.addEventListener('input', later);
  closing.addEventListener('input', later);
  show();
  const card = h('section', { class: 'card' },
    h('h2', {}, 'Message with each schedule'),
    h('p', { class: 'muted' }, 'The text copied for an e-mail (Publish, or key S). {name} becomes the person’s first name.'),
    h('div', { class: 'form-grid' }, field('First line', greeting), field('Last words', closing)),
    field('Subject, when sent by e-mail from Rota Planner', subject, '{month} becomes the month’s name. Only used if E-mail is set up below.'),
    preview,
    h('div', { class: 'card-actions' }, h('button', { class: 'btn primary', type: 'button', onclick: async () => {
      try {
        await saveSetup('settings/save', { settings: { message_greeting: greeting.value, message_closing: closing.value, message_subject: subject.value } },
          { message: 'Message saved' });
        setSession({ schedules: null });  // the messages are made again with the new words
      } catch (error) {
        formError(card, errorText(error));
      }
    } }, 'Save')));
  return card;
}

// ---- Moving to another computer ---------------------------------------------

function saveFile(name, base64) {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/zip' }));
  const link = h('a', { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function moveCard() {
  const comments = toggle('Include the availability comments (they can mention health)', false);
  const file = h('input', { type: 'file', accept: '.zip', class: 'file' });
  const result = h('div', { class: 'import-result' });
  const exportButton = h('button', { class: 'btn primary', type: 'button', onclick: async () => {
    exportButton.disabled = true;
    try {
      const data = await api.post('/api/data/export', { include_comments: comments.input.checked });
      saveFile(data.name, data.file);
      toast('File ready: keep it on a USB stick, not in an e-mail');
    } catch (error) {
      toast(errorText(error), { tone: 'bad' });
    } finally {
      exportButton.disabled = false;
    }
  } }, icon('download'), 'Save everything in one file');

  const check = async () => {
    const chosen = file.files[0];
    fill(result);
    if (!chosen) return;
    const body = { file: await readFileBase64(chosen) };
    try {
      const info = await api.post('/api/data/import/preview', body);
      const here = info.here.months.length || info.here.people ? `It replaces what is on this computer (${info.here.people} people, ${info.here.months.length} month${info.here.months.length === 1 ? '' : 's'}), which is kept in a backup first.`
        : 'There is nothing on this computer yet.';
      const go = h('button', { class: 'btn primary danger', type: 'button', onclick: async () => {
        go.disabled = true;
        try {
          const done = await api.post('/api/data/import/apply', body);
          toast(`Data brought in · the previous data is kept in backups/${done.safety}`);
          setTimeout(() => window.location.reload(), 900);
        } catch (error) {
          go.disabled = false;
          formError(result, errorText(error));
        }
      } }, 'Replace my data with it');
      fill(result,
        h('div', { class: 'import-preview' },
          h('strong', {}, `File of ${info.created}`),
          h('span', {}, `${info.people} people · ${info.tasks} tasks · ${info.months.length ? info.months.join(', ') : 'no month'}`),
          h('span', { class: 'muted' }, info.comments ? 'With the availability comments.' : 'Without the availability comments.'),
          h('span', { class: 'warn-text' }, here)),
        h('div', { class: 'card-actions' }, go));
    } catch (error) {
      formError(result, errorText(error));
    }
  };
  file.addEventListener('change', check);

  return h('section', { class: 'card' },
    h('h2', {}, 'Move to another computer'),
    h('p', { class: 'muted' }, 'Everything (people, tasks, months, settings) in one file, to open Rota Planner elsewhere with the same data. The file holds personal data: carry it on a USB stick rather than by e-mail.'),
    comments,
    h('div', { class: 'card-actions' }, exportButton),
    h('h3', { class: 'form-section' }, 'Bring data from another computer'),
    field('A file saved with the button above', file),
    result);
}

function summaryList(summary) {
  return h('ul', { class: 'summary-list' },
    h('li', {}, `${summary.people} people (${summary.active} on the rota), ${summary.skills} skills`),
    h('li', {}, `${summary.areas} areas, ${summary.tasks} tasks, ${summary.regulars} regular commitments`),
    summary.months.map((m) => h('li', {}, `${m.title}: ${m.filled} of ${m.required} shifts filled, ${m.events} event(s)`)),
    summary.availability_month ? h('li', {}, `${summary.availability_replies} availability replies for ${summary.availability_month}`) : null,
    h('li', {}, `${summary.preferences} people with preferences`),
    summary.anonymous ? h('li', { class: 'ok-text' }, 'Names replaced by invented ones; phones, lay names and comments left out.') : null);
}

function importCard() {
  const file = h('input', { type: 'file', accept: '.xlsx', class: 'file', 'aria-label': 'Workbook file' });
  const anonymous = toggle('Replace names with invented ones (anonymous)', true);
  const result = h('div', { class: 'import-result' });
  let encoded = '';

  const run = async (apply) => {
    if (!file.files[0]) {
      formError(result, 'Choose the workbook file first (File › Download › Microsoft Excel in Google Sheets).');
      return;
    }
    encoded = encoded || await readFileBase64(file.files[0]);
    result.replaceChildren(h('p', { class: 'muted' }, apply ? 'Importing…' : 'Reading the workbook…'));
    try {
      const answer = await api.post(apply ? '/api/import/workbook' : '/api/import/workbook/preview', { file: encoded, anonymous: anonymous.input.checked, apply });
      if (answer.applied) {
        result.replaceChildren(h('p', { class: 'ok-text' }, 'Imported. Opening the rota…'));
        setTimeout(() => { window.location.hash = '#/build'; window.location.reload(); }, 600);
        return;
      }
      fill(result, 
        h('h3', {}, 'This is what will be imported'),
        summaryList(answer.summary),
        answer.warnings.length ? h('div', { class: 'warnings' }, h('h3', {}, 'To know'),
          h('ul', {}, answer.warnings.map((w) => h('li', {}, w)))) : null,
        h('p', { class: 'warn-text' }, 'Importing replaces the current people, tasks and regular week, and these months. A backup of the current files is kept.'),
        h('button', { class: 'btn primary', type: 'button', onclick: () => run(true) }, 'Import'));
    } catch (error) {
      result.replaceChildren();
      formError(result, errorText(error));
    }
  };
  file.addEventListener('change', () => { encoded = ''; result.replaceChildren(); });
  anonymous.input.addEventListener('change', () => result.replaceChildren());

  return h('section', { class: 'card' },
    h('h2', {}, 'Import the current workbook'),
    h('p', { class: 'muted' }, 'Reads the Google Sheets rota downloaded as an Excel file: people and skills, tasks, how many people each day needs, the regular week, the monthly rotas, availability and preferences. The file never leaves this computer.'),
    field('Workbook (.xlsx)', file), anonymous,
    h('div', { class: 'card-actions' }, h('button', { class: 'btn', type: 'button', onclick: () => run(false) }, 'Check the file')),
    result);
}

export function mountSettings(container) {
  const root = h('div', { class: 'page setup-page' });
  container.replaceChildren(root);
  let shown = null;

  function render() {
    const setup = state.session.setup;
    // Forms keep what is being typed: only build them once per visit.
    if (!setup || shown) return;
    shown = setup;
    // Five groups, in the order one meets them; the chips at the top jump to a group.
    const groups = [
      ['The rota', [settingsCard(setup), messageCard(setup)]],
      ['Sending by e-mail', [mailCard()]],
      ['Other computers', [otherComputersCard()]],
      ['Your data', [moveCard(), importCard()]],
      ['The program', [updateCard()]],
    ].map(([title, cards]) => h('section', { class: 'settings-group' },
      h('h2', { class: 'group-title', tabindex: '-1' }, title), h('div', { class: 'cards flow' }, cards)));
    root.replaceChildren(
      h('header', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Settings'))),
      h('nav', { class: 'group-nav', 'aria-label': 'Parts of the settings' }, groups.map((group) => h('button', {
        class: 'btn small', type: 'button', onclick: () => {
          group.scrollIntoView({ behavior: 'smooth', block: 'start' });
          group.querySelector('.group-title').focus({ preventScroll: true });
        },
      }, group.querySelector('.group-title').textContent))),
      ...groups);
  }

  return { render };
}
