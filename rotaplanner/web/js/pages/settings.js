// Settings: usual hours, who can use the program, and importing the current Google Sheets workbook.
import { field, formError, numberInput, openDialog, readFileBase64, segmented, textInput, toggle } from '../components.js';
import { otherComputersCard } from '../syncsetup.js';
import { mailCard } from '../mailer.js';
import { fill, h } from '../dom.js';
import { api, setToken } from '../api.js';
import { errorText, saveSetup } from '../setup.js';
import { setSession, state } from '../state.js';
import { icon } from '../icons.js';
import { toast } from '../ui.js';

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
  const preview = h('pre', { class: 'finder-text' });
  const show = () => {
    const hello = greeting.value.replace('{name}', 'Ana').trim();
    preview.textContent = [hello ? `${hello}\n` : '', 'Here are your shifts for December 2026:',
      '• Tue 1 Dec · 09:00–13:00 · Lunch cook', '• Sat 5 Dec · 16:00–19:00 · Supper cook',
      closing.value.trim() ? `\n${closing.value.trim()}` : ''].filter((line) => line !== '').join('\n');
  };
  greeting.addEventListener('input', show);
  closing.addEventListener('input', show);
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

// ---- Who can use Rota Planner: password, other computers, firewall -----------

function passwordInput(autocomplete) {
  return h('input', { class: 'input', type: 'password', autocomplete });
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Copied');
  } catch {
    toast('Could not copy: select the text instead', { tone: 'bad' });
  }
}

function commandList(lines) {
  return h('div', { class: 'command-list' }, lines.map((line) => h('div', { class: 'command' },
    h('code', {}, line),
    h('button', { class: 'btn small ghost icon-only', type: 'button', 'aria-label': 'Copy the command', title: 'Copy',
      onclick: () => copyText(line) }, icon('copy')))));
}

const FIREWALL_STATE = {
  on: ['The firewall of this computer is on.', 'ok-text'],
  off: ['The firewall of this computer is off.', 'warn-text'],
  unknown: ['Whether the firewall is on could not be checked without administrator rights.', 'muted'],
};

function firewallBox(info) {
  const box = h('div', { class: 'firewall' });
  const check = async () => {
    fill(box, h('p', { class: 'muted' }, 'Looking at the firewall…'));
    try {
      const fw = await api.get('/api/access/firewall');
      const [words, tone] = FIREWALL_STATE[fw.state] || FIREWALL_STATE.unknown;
      const open = info.mode === 'network';
      fill(box,
        h('p', { class: tone }, words, fw.detail ? ` ${fw.detail}` : ''),
        fw.state === 'off' && open ? h('p', { class: 'warn-text' },
          'With the firewall off, every program of this computer can be reached from the network, not only Rota Planner. Turning it on is advised.') : null,
        fw.rule === true ? h('p', { class: 'ok-text' }, 'A “Rota Planner” rule is already there.') : null,
        open && fw.commands.open.length ? [
          h('p', {}, 'For the other computers to get through, open this port to them only. ', fw.commands.where),
          commandList(fw.commands.open)] : null,
        !open && fw.commands.close.length ? [
          h('p', {}, 'If you opened the port before, close it again. ', fw.commands.where),
          commandList(fw.commands.close)] : null,
        fw.commands.note ? h('p', { class: 'muted' }, fw.commands.note) : null,
        h('p', { class: 'faint' }, 'Rota Planner never changes the firewall itself.'),
        h('button', { class: 'btn small', type: 'button', onclick: check }, 'Check again'));
    } catch (error) {
      formError(box, errorText(error));
    }
  };
  fill(box, h('button', { class: 'btn', type: 'button', onclick: check }, 'Check the firewall'));
  return box;
}

function accessCard() {
  const card = h('section', { class: 'card' }, h('h2', {}, 'Who can use Rota Planner'), h('p', { class: 'muted' }, 'Loading…'));

  const show = (info) => {
    if (!info.local) {
      fill(card, h('h2', {}, 'Who can use Rota Planner'),
        h('p', { class: 'muted' }, 'You are using Rota Planner from another computer. The password and the computers allowed are changed on the computer where it runs.'));
      return;
    }
    // Password
    const current = passwordInput('current-password');
    const fresh = passwordInput('new-password');
    const repeat = passwordInput('new-password');
    const savePassword = async (remove) => {
      if (!remove && fresh.value !== repeat.value) {
        formError(passwordPart, 'The two new passwords are not the same.');
        return;
      }
      try {
        const answer = await api.post('/api/access/password', { current: current.value, new: remove ? '' : fresh.value });
        if (answer.session) setToken(answer.session);
        toast(remove ? 'Password removed' : 'Password saved');
        load();
        if (state.boot?.auth) { state.boot.auth.password = !remove; setSession({}); }
      } catch (error) {
        formError(passwordPart, errorText(error));
      }
    };
    const passwordPart = h('div', { class: 'form-part' },
      h('h3', { class: 'form-section' }, 'Password'),
      h('p', { class: 'muted' }, info.has_password
        ? 'A password is asked each time Rota Planner is opened, on this computer too.'
        : 'No password: anyone using this computer can open Rota Planner. A password is needed before other computers can be allowed.'),
      info.password_unreadable ? h('p', { class: 'warn-text' }, 'The password file is damaged: set a new password.') : null,
      h('div', { class: 'form-grid' },
        info.has_password && !info.password_unreadable ? field('Current password', current) : null,
        field(info.has_password ? 'New password' : 'Password', fresh, 'At least 8 characters'),
        field('The same again', repeat)),
      h('div', { class: 'card-actions' },
        h('button', { class: 'btn primary', type: 'button', onclick: () => savePassword(false) }, info.has_password ? 'Change the password' : 'Set the password'),
        info.has_password && info.mode === 'local' ? h('button', { class: 'btn ghost danger', type: 'button', onclick: () => savePassword(true) }, 'Remove the password') : null));

    // Other computers
    const mode = segmented([{ value: 'local', label: 'This computer only' }, { value: 'network', label: 'Also other computers' }],
      info.mode, { label: 'Who can connect', onchange: (value) => { list.hidden = value !== 'network'; } });
    const addresses = h('textarea', { class: 'input textarea', rows: 3, placeholder: '192.168.1.57' }, info.allowed.join('\n'));
    const here = info.computer.addresses.length
      ? info.computer.addresses.map((a) => `http://${a}:${info.port}`).join('  or  ') : 'no network address found';
    const list = h('div', { class: 'form-part', hidden: info.mode !== 'network' },
      field('Addresses of the other computers', addresses, 'One per line: an address (192.168.1.57) or a range (192.168.1.0/24). Nobody else can connect.'),
      h('p', {}, 'On the other computer, open ', h('strong', {}, here), ' and give the password.'));
    const networkPart = h('div', { class: 'form-part' },
      h('h3', { class: 'form-section' }, 'Other computers'),
      mode, list,
      info.public.length ? h('p', { class: 'warn-text' }, `Not addresses of a local network: ${info.public.join(', ')}. Allow only computers of the centre.`) : null,
      h('p', { class: 'warn-text' }, 'What travels between the computers is not encrypted: allow other computers only on the staff network, never on a public Wi-Fi.'),
      h('div', { class: 'card-actions' }, h('button', { class: 'btn primary', type: 'button', onclick: async () => {
        try {
          const answer = await api.post('/api/access/network', { mode: mode.getValue(), allowed: addresses.value.split(/[\s,;]+/) });
          toast(answer.mode === 'network' ? 'Other computers allowed' : 'This computer only');
          show(answer);
        } catch (error) {
          formError(networkPart, errorText(error));
        }
      } }, 'Save')));

    fill(card, h('h2', {}, 'Who can use Rota Planner'), passwordPart, networkPart,
      h('h3', { class: 'form-section' }, 'Firewall'), firewallBox(info));
  };

  const load = () => api.get('/api/access').then(show).catch((error) => formError(card, errorText(error)));
  load();
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
      const answer = await api.post('/api/import/workbook', { file: encoded, anonymous: anonymous.input.checked, apply });
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
    root.replaceChildren(
      h('header', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Settings'))),
      h('div', { class: 'cards' }, settingsCard(setup), messageCard(setup), mailCard(), accessCard(), otherComputersCard(), moveCard(), importCard()));
  }

  return { render };
}
