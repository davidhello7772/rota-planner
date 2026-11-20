// Other computers: setting it up in three steps (where they meet, this
// computer, done), the panel that shows how things stand, and the first
// screen of a computer that has no data yet. The program decides and words
// everything (sync.py); this shows it.
import { api, ApiError } from './api.js';
import { field, formError, openDialog, segmented, textInput, toggle } from './components.js';
import { fill, h } from './dom.js';
import { icon } from './icons.js';
import { errorText } from './setup.js';
import { CLOUDS, onSyncChange, openSync, refreshSync, syncStatus, useStatus } from './sync.js';
import { copyText, toast } from './ui.js';

const KOOFR = 'https://app.koofr.net/dav/Koofr/Rota Planner/';
const FOLDER_HINT = 'The full path of a folder every computer sees, e.g. /home/you/Google Drive/Rota (Linux), '
  + '/Users/you/Google Drive/My Drive/Rota (Mac) or G:\\My Drive\\Rota (Windows).';
function codeBox(code) {
  return h('div', { class: 'form-part' },
    h('p', {}, 'On each other computer, choose “Join the others” and type this pairing code:'),
    h('div', { class: 'command' }, h('code', { class: 'sync-code' }, code),
      h('button', { class: 'btn small ghost icon-only', type: 'button', 'aria-label': 'Copy the code', title: 'Copy',
        onclick: () => copyText(code) }, icon('copy'))),
    h('p', { class: 'warn-text' }, 'Keep this code private: it is the key to the data. Never put it on the server or in an e-mail.'));
}

/** A big choice with an icon, a title and a line of explanation. */
function tile(name, title, text, pressed, onclick) {
  return h('button', { class: 'choice-tile', type: 'button', 'aria-pressed': String(pressed), 'aria-label': `${title}. ${text}`, onclick },
    icon(name), h('span', { class: 'choice-title' }, title), h('span', { class: 'choice-text' }, text));
}

function steps(current) {
  const labels = ['Where they meet', 'This computer', 'Done'];
  return h('ol', { class: 'phase-steps wizard-steps', 'aria-label': 'Steps' }, labels.map((label, i) => h('li', {
    class: ['phase-step', i + 1 < current ? 'done' : i + 1 === current ? 'current' : ''],
    'aria-current': i + 1 === current ? 'step' : null,
  }, h('span', { class: 'phase-num', 'aria-hidden': 'true' }, i + 1 < current ? '✓' : i + 1), h('span', { class: 'phase-label' }, label))));
}

// ---- The three steps ---------------------------------------------------------

/**
 * Sets up the meeting place and this computer. `join`: start on "Join the
 * others" (a new computer); `placeOnly`: only change where they meet.
 */
export function openSyncWizard({ join = false, placeOnly = false, onDone } = {}) {
  const known = syncStatus()?.place || {};
  const form = {
    step: 1,
    kind: known.kind || 'webdav',
    service: !known.url || known.url.startsWith('https://app.koofr.net/') ? 'koofr' : 'other',
    url: known.url || KOOFR, user: known.user || '', password: '', folder: known.folder || '',
    tested: false, name: '', mode: join ? 'join' : 'start', code: '', made: '',
  };
  const body = h('div', { class: 'wizard' });
  const next = h('button', { class: 'btn primary', type: 'button' });
  const back = h('button', { class: 'btn', type: 'button' }, 'Back');
  let finished = false;
  const dialog = openDialog({
    title: placeOnly ? 'Where the computers meet' : 'Other computers', wide: true,
    subtitle: placeOnly ? 'Change the server or its password. The pairing code stays the same.'
      : 'Each computer keeps its own copy. They meet on a server (or in a shared folder) that only ever holds sealed files.',
    body: [body], actions: [back, next],
    onClose: () => { if (finished) onDone?.(); },
  });
  const place = () => (form.kind === 'folder' ? { kind: 'folder', folder: form.folder }
    : { kind: 'webdav', url: form.url, user: form.user, password: form.password });

  function stepPlace() {
    const result = h('div', { class: 'test-result', role: 'status' });
    const untested = () => { form.tested = false; fill(result); next.disabled = true; };
    const address = textInput(form.url, { spellcheck: false, autocomplete: 'off',
      oninput: (e) => { form.url = e.target.value; untested(); } });
    const user = textInput(form.user, { spellcheck: false, autocomplete: 'off', placeholder: 'you@example.org',
      oninput: (e) => { form.user = e.target.value; untested(); } });
    const password = textInput(form.password, { type: 'password', autocomplete: 'new-password',
      placeholder: known.has_password ? 'Unchanged (the one kept)' : '',
      oninput: (e) => { form.password = e.target.value; untested(); } });
    const folder = textInput(form.folder, { spellcheck: false, autocomplete: 'off', placeholder: '/home/you/Google Drive/Rota',
      oninput: (e) => { form.folder = e.target.value; untested(); } });
    const koofr = form.service === 'koofr';
    const test = h('button', { class: 'btn', type: 'button', onclick: async () => {
      test.disabled = true;
      fill(result, h('span', { class: 'muted' }, form.kind === 'folder' ? 'Trying the folder…' : 'Asking the server…'));
      try {
        const answer = await api.post('/api/sync/test', { place: place() });
        form.tested = true;
        if (answer.place.url) { form.url = answer.place.url; address.value = form.url; }
        fill(result, h('span', { class: 'ok-text' }, icon('check'), ' Connected: Rota Planner can read and write there.'));
        next.disabled = false;
      } catch (error) {
        form.tested = false;
        fill(result, h('span', { class: 'warn-text' }, icon('alert'), ` ${errorText(error)}`));
      }
      test.disabled = false;
    } }, 'Test the connection');
    fill(body, steps(1),
      h('div', { class: 'choice-tiles' },
        tile('cloud', 'A server on the internet', 'Nothing to install. Works from anywhere, and without connection in between.',
          form.kind === 'webdav', () => { form.kind = 'webdav'; form.tested = false; render(); }),
        tile('folder', 'A shared folder', 'Google Drive, Synology Drive or a network folder already on every computer.',
          form.kind === 'folder', () => { form.kind = 'folder'; form.tested = false; render(); })),
      form.kind === 'webdav' ? [
        segmented([{ value: 'koofr', label: 'Koofr (free, in Europe)' }, { value: 'other', label: 'Another WebDAV server' }], form.service,
          { label: 'Service', onchange: (value) => { form.service = value; if (value === 'koofr') form.url = KOOFR; form.tested = false; render(); } }),
        koofr ? h('p', { class: 'muted' }, 'Koofr gives 10 GB for free, far more than needed. Make an account at koofr.eu, then an ',
          h('strong', {}, 'app password'), ' in Preferences › Password › App passwords: that is what Rota Planner uses, never the account’s own password.') : null,
        field('Address of the folder on the server', address, koofr
          ? 'The folder “Rota Planner” is made for you. Use the same address on every computer.'
          : 'A WebDAV folder, starting with https://. Use the same address on every computer.'),
        h('div', { class: 'form-grid' },
          field('User name', user, koofr ? 'The e-mail address of the Koofr account' : ''),
          field(koofr ? 'App password' : 'Password', password, 'Kept on this computer only; never shown again')),
      ] : field('Shared folder', folder, FOLDER_HINT),
      h('div', { class: 'card-actions' }, test, result));
    back.hidden = true;
    next.textContent = placeOnly ? 'Use this place' : 'Next';
    next.disabled = !form.tested;
    next.onclick = async () => {
      if (!placeOnly) { form.step = 2; render(); return; }
      next.disabled = true;
      try {
        useStatus(await api.post('/api/sync/setup', { action: 'place', place: place() }));
        finished = true;
        dialog.close();
        toast('The computers now meet there. Do the same on the others.');
      } catch (error) {
        next.disabled = false;
        formError(body, errorText(error));
      }
    };
  }

  function stepComputer() {
    const name = textInput(form.name, { placeholder: 'e.g. Office, Front desk, Laptop', autocomplete: 'off',
      oninput: (e) => { form.name = e.target.value; } });
    const code = textInput(form.code, { placeholder: 'ABCD-EFGH-JKMN-PQRS-TUVW', autocomplete: 'off', spellcheck: false,
      class: 'input sync-code-input', oninput: (e) => {
        // Grouped by four as it is typed or pasted, like the code shown on the first computer.
        const raw = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 20);
        e.target.value = raw.replace(/(.{4})(?=.)/g, '$1-');
        form.code = e.target.value;
      } });
    const confirm = h('div', { class: 'form-part' });
    const send = async (confirmed) => {
      next.disabled = true;
      try {
        const answer = await api.post('/api/sync/setup', { action: form.mode, name: form.name, place: place(), code: form.code, confirmed });
        form.made = answer.code || '';
        form.result = answer;
        finished = true;
        useStatus(answer);
        form.step = 3;
        render();
      } catch (error) {
        next.disabled = false;
        if (error instanceof ApiError && error.payload.confirm) {
          fill(confirm, h('div', { class: 'notice warn' }, icon('alert'), h('span', {}, errorText(error)),
            h('button', { class: 'btn small', type: 'button', onclick: () => send(true) }, 'Join and keep it')));
        } else {
          formError(body, errorText(error));
        }
      }
    };
    fill(body, steps(2),
      h('div', { class: 'form-grid' }, field('Name of this computer', name, 'Shown on the other computers')),
      h('div', { class: 'choice-tiles' },
        tile('plus', 'The first computer', 'Nobody uses that place yet: a pairing code is made here, to type on the others.',
          form.mode === 'start', () => { form.mode = 'start'; render(); }),
        tile('users', 'Join the others', 'Another computer is already there: type its pairing code, and everything arrives.',
          form.mode === 'join', () => { form.mode = 'join'; render(); })),
      form.mode === 'join' ? field('Pairing code', code, 'Shown on a computer already set up: Settings › Other computers › Show the pairing code') : null,
      confirm);
    back.hidden = false;
    back.onclick = () => { form.step = 1; render(); };
    next.textContent = form.mode === 'start' ? 'Start' : 'Join';
    next.disabled = false;
    next.onclick = () => send(false);
  }

  function stepDone() {
    const months = form.result?.bootstrap?.months?.length || 0;
    fill(body, steps(3),
      h('p', { class: 'ok-text done-line' }, icon('check'), form.mode === 'start' ? ' This computer is ready.'
        : ` This computer has joined${months ? `: ${months} month${months === 1 ? '' : 's'} and all the lists have arrived` : ''}.`),
      form.made ? codeBox(form.made) : null,
      h('ul', { class: 'summary-list' },
        h('li', {}, 'Changes travel by themselves. Without connection you keep working; they are sent when it is back.'),
        h('li', {}, 'Only one computer changes the rota at a time: the first change takes the pen, “I have finished” gives it back. The others can look, or “Ask to edit”.'),
        h('li', {}, 'If two computers did change the same month or list (while one was offline), you are asked which to keep. Nothing is mixed.')));
    back.hidden = true;
    next.textContent = 'Done';
    next.disabled = false;
    next.onclick = () => dialog.close();
  }

  function render() {
    [stepPlace, stepComputer, stepDone][form.step - 1]();
  }
  render();
}

// ---- The panel: how things stand ---------------------------------------------

function computerCard(computer, { self = false } = {}) {
  return h('li', { class: ['computer', computer.state, self && 'self'] },
    icon('computer'),
    h('span', { class: 'computer-name' }, computer.name, self ? h('span', { class: 'tag' }, 'this computer') : null),
    h('span', { class: 'computer-state' }, computer.editing ? [icon('pen'), ' '] : null, computer.text),
    // For a computer that no longer takes part (uninstalled, stopped, gone): never the one editing.
    !self && !computer.editing ? h('button', { class: 'btn small ghost', type: 'button',
      title: 'It no longer takes part: remove its file from the meeting place', onclick: () => forget(computer) }, 'Forget this computer') : null);
}

function forget(computer) {
  const dialog = openDialog({
    title: `Forget ${computer.name}?`,
    subtitle: 'Its file leaves the meeting place. If it is used again, it simply comes back.',
    body: [],
    actions: [h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: async () => {
        try {
          useStatus(await api.post('/api/sync/forget', { computer: computer.id }));
          dialog.close();
        } catch (error) {
          formError(dialog.panel.querySelector('.modal-body'), errorText(error));
        }
      } }, 'Forget it')],
  });
}

function panel(card, status) {
  const [text, tone, title] = status.summary;
  const pen = status.pen || {};
  const codeSlot = h('div', {});
  const stop = () => {
    const dialog = openDialog({
      title: 'Stop synchronising?',
      subtitle: 'The data stays on this computer. Its file leaves the meeting place; the other computers go on without it.',
      body: [h('p', {}, 'To take part again later, choose “Join the others” with the same pairing code.')],
      actions: [h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
        h('button', { class: 'btn primary danger', type: 'button', onclick: async () => {
          try {
            const answer = await api.post('/api/sync/stop', {});
            dialog.close();
            useStatus(answer);
            if (answer.removed === false) toast('Stopped. Its file could not be removed from the server: another computer can “Forget” it.');
          } catch (error) {
            formError(dialog.panel.querySelector('.modal-body'), errorText(error));
          }
        } }, 'Stop synchronising')],
    });
  };
  const auto = toggle('Take the others’ changes by themselves when nothing conflicts', status.auto, async (event) => {
    try {
      useStatus(await api.post('/api/sync/options', { auto: event.target.checked }));
    } catch (error) {
      toast(errorText(error), { tone: 'bad' });
    }
  });
  fill(card, h('h2', {}, 'Other computers'),
    h('div', { class: ['sync-head', tone] },
      icon(CLOUDS[status.icon] || 'cloud'),
      h('div', { class: 'sync-head-text' }, h('strong', {}, text), h('span', {}, title)),
      status.needs_choice ? h('button', { class: 'btn primary', type: 'button', onclick: openSync }, icon('sync'), 'Synchronise…') : null,
      h('button', { class: 'btn', type: 'button', title: 'Look at the meeting place now', onclick: async (e) => {
        e.currentTarget.disabled = true;
        try { useStatus(await api.post('/api/sync/check', {})); } catch (error) { toast(errorText(error), { tone: 'bad' }); refreshSync(); }
      } }, 'Check now')),
    h('div', { class: ['sync-map', status.connection.online === false ? 'offline' : ''] },
      h('ul', { class: 'computers' }, computerCard(status.me, { self: true })),
      h('div', { class: 'sync-link' },
        h('span', { class: 'sync-line', 'aria-hidden': 'true' }),
        h('span', { class: 'sync-place' }, icon(status.place.kind === 'folder' ? 'folder' : 'cloud'),
          h('span', { class: 'path' }, status.place.label)),
        h('span', { class: 'sync-connection' }, status.connection.text),
        h('span', { class: 'sync-line', 'aria-hidden': 'true' })),
      status.others.length
        ? h('ul', { class: 'computers' }, status.others.map((c) => computerCard(c)))
        : h('p', { class: 'muted computers-none' }, 'No other computer yet. On another computer: Settings › Other computers › Join the others.')),
    status.problem && status.kind !== 'alone' ? h('p', { class: status.passing ? 'muted' : 'warn-text' }, status.problem) : null,
    pen.text && !pen.mine ? h('p', { class: 'muted' }, icon('eye'), ' ', pen.text) : null,
    status.log.length ? h('details', { class: 'sync-activity', open: true },
      h('summary', {}, 'Activity'),
      h('ul', {}, status.log.map((entry) => h('li', { class: entry.tone }, h('span', { class: 'faint' }, entry.when), ' ', entry.text)))) : null,
    auto,
    h('div', { class: 'card-actions' },
      h('button', { class: 'btn', type: 'button', onclick: async () => {
        try {
          fill(codeSlot, codeBox((await api.post('/api/sync/code', {})).code));
        } catch (error) {
          formError(card, errorText(error));
        }
      } }, 'Show the pairing code'),
      h('button', { class: 'btn', type: 'button', onclick: () => openSyncWizard({ placeOnly: true }) },
        status.place.kind === 'folder' ? 'Change the meeting place' : 'Change the server or password'),
      h('button', { class: 'btn ghost danger', type: 'button', onclick: stop }, 'Stop synchronising')),
    codeSlot,
    h('p', { class: 'faint' }, 'Everything at the meeting place is sealed with the pairing code: the server cannot read it. Comments of a month that is over are never sent.'));
}

function intro(card) {
  fill(card, h('h2', {}, 'Other computers'),
    h('p', { class: 'muted' }, 'Use Rota Planner on several computers, here or elsewhere. Each keeps its own copy and works without '
      + 'connection too; changes travel by themselves, and only one computer changes the rota at a time.'),
    h('div', { class: 'choice-tiles' },
      tile('plus', 'Set up the first computer', 'Choose where the computers meet and get a pairing code.', false,
        () => openSyncWizard({})),
      tile('users', 'Join the others', 'Another computer is already set up: type its pairing code here.', false,
        () => openSyncWizard({ join: true, onDone: () => window.location.reload() }))));
}

/** The Settings card. */
export function otherComputersCard() {
  const card = h('section', { class: 'card sync-card' }, h('h2', {}, 'Other computers'), h('p', { class: 'muted' }, 'Loading…'));
  const show = (status) => {
    if (!card.isConnected && card.dataset.shown) { stopWatching(); return; }
    if (!status) return;
    card.dataset.shown = '1';
    // While something is being typed or read in the card (a code shown, a menu open), leave it alone.
    if (card.contains(document.activeElement) && document.activeElement.tagName === 'INPUT') return;
    const open = card.querySelector('.sync-code');
    if (status.configured) { if (!open) panel(card, status); } else intro(card);
  };
  const stopWatching = onSyncChange(show);
  refreshSync().then(show).catch((error) => formError(card, errorText(error)));
  return card;
}

// ---- A computer with no data yet -----------------------------------------------

/** The first screen: start empty, bring the current workbook, or join computers already set up. */
export function welcomeScreen({ onStartEmpty }) {
  return h('div', { class: 'welcome' },
    h('h1', {}, 'Welcome to Rota Planner'),
    h('p', { class: 'muted' }, 'There is no data on this computer yet. Where do you want to begin?'),
    h('div', { class: 'choice-tiles welcome-tiles' },
      tile('cloud', 'Connect to existing data', 'Rota Planner is already used on another computer: join it with the pairing code, and everything arrives here.',
        false, () => openSyncWizard({ join: true, onDone: () => window.location.reload() })),
      tile('download', 'Bring in the current workbook', 'Import the Google Sheets workbook used so far (people, tasks, rotas).',
        false, () => { onStartEmpty(); window.location.hash = '#/settings'; }),
      tile('plus', 'Start empty', 'Add people and tasks by hand, then create the first month.',
        false, () => { onStartEmpty(); window.location.hash = '#/people'; })));
}
