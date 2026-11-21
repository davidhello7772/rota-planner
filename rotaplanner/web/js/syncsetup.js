// Several computers: setting it up in three steps (where they meet, this
// computer, done), the line in Settings that shows how things stand, and the
// first screen of a computer that has no data yet. The program decides and
// words everything (sync.py); this shows it.
import { api, ApiError } from './api.js';
import { explain, field, formError, openDialog, segmented, statusLine, textInput, toggle } from './components.js';
import { fill, h, replaceKeepingFocus } from './dom.js';
import { icon } from './icons.js';
import { errorText } from './setup.js';
import { CLOUDS, onSyncChange, openSync, refreshSync, syncStatus, useStatus } from './sync.js';
import { copyText, toast } from './ui.js';
import { openRestore } from './backup.js';
import { state } from './state.js';

const KOOFR = 'https://app.koofr.net/dav/Koofr/Rota Planner/';
const FOLDER_HINT = 'The full path of a folder every computer sees, e.g. /home/you/Google Drive/Rota (Linux), '
  + '/Users/you/Google Drive/My Drive/Rota (Mac) or G:\\My Drive\\Rota (Windows).';
function codeBox(code, lead = 'On each other computer, choose “Join the others” and type this pairing code:') {
  return h('div', { class: 'form-part' },
    lead ? h('p', {}, lead) : null,
    h('div', { class: 'command' }, h('code', { class: 'sync-code' }, code),
      h('button', { class: 'btn small ghost icon-only', type: 'button', 'aria-label': 'Copy the code', title: 'Copy',
        onclick: () => copyText(code) }, icon('copy'))),
    h('p', { class: 'warn-text' }, 'Keep this code private: it opens the sealed files. Never put it on the server or in an e-mail.'));
}

const PAIRING_CODE = explain('What is the pairing code?',
  'The key that opens the sealed files. It is made on the first computer and typed once on each of the others.',
  'The server or shared folder never has it, so it cannot read your rota. If you lose it, any computer already set up shows it again (Settings › Several computers › Add a computer).');

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
    title: placeOnly ? 'Where the computers meet' : 'Set up several computers', wide: true,
    subtitle: placeOnly ? 'Change the server or its password. The pairing code stays the same.'
      : 'Each computer keeps its own copy of the rota. They meet in a place you choose, which only ever holds sealed files.',
    body: [body], actions: [back, next],
    onClose: () => {
      if (!finished) return;
      onDone?.();
      // Everything arrived (a join), or the first screen of an empty computer is still up: show the data.
      if (!placeOnly && (form.mode === 'join' || state.boot?.empty)) window.location.reload();
    },
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
      explain('What is the meeting place?',
        'Think of a letterbox the computers share: each one drops its copy there in a sealed envelope, and picks up the others’.',
        'It can be a free online storage account (Koofr: nothing to install, works from anywhere), or a folder every computer already sees (Google Drive, Synology Drive, a network folder).',
        'Only your computers can open the envelopes: whoever runs the storage cannot read your rota.'),
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
        koofr ? explain('What is an app password?',
          'A separate password that lets one program use the storage. It is not the account’s own password: you can cancel it in Koofr at any time without changing anything else.') : null,
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
      form.mode === 'join' ? field('Pairing code', code, 'Shown on a computer already set up: Settings › Several computers › Add a computer') : null,
      confirm, PAIRING_CODE.cloneNode(true));
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

// ---- The line in Settings: how things stand ---------------------------------

function computerCard(computer, { self = false } = {}) {
  return h('li', { class: ['computer', computer.state, self && 'self'] },
    icon('computer'),
    h('span', { class: 'computer-name' }, computer.name, self ? h('span', { class: 'tag' }, 'this computer') : null),
    h('span', { class: 'computer-state' }, computer.editing ? [icon('pen'), ' '] : null, computer.text),
    // For a computer that no longer takes part (uninstalled, stopped, gone): never the one editing.
    !self && !computer.editing ? h('button', { class: 'btn small ghost', type: 'button', dataset: { focusId: `forget-${computer.id}` },
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

function stopSync() {
  const dialog = openDialog({
    title: 'Stop synchronising?',
    subtitle: 'The data stays on this computer. Its file leaves the meeting place; the other computers go on without it.',
    body: [h('p', {}, 'To take part again later: Set up… › Join the others, with the same pairing code.')],
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
}

/** "Add a computer": the three things to do on the other one, with the pairing code. */
async function openAddComputer(status) {
  const slot = h('div', { class: 'form-part' }, h('p', { class: 'muted' }, 'Looking for the code…'));
  const place = status.place.kind === 'folder' ? 'the same shared folder' : 'the same server address, user name and app password';
  const dialog = openDialog({
    title: 'Add a computer', subtitle: 'It gets its own copy of everything, and then stays in step by itself.',
    body: [h('ol', { class: 'steps-list' },
      h('li', {}, 'Install Rota Planner on the other computer and open it.'),
      h('li', {}, 'On its first screen, choose “Connect to existing data”.'),
      h('li', {}, `Give ${place} as here (`, h('span', { class: 'path' }, status.place.label), '), then this pairing code.')),
    slot, PAIRING_CODE.cloneNode(true)],
    actions: [h('button', { class: 'btn primary', type: 'button', onclick: () => dialog.close() }, 'Done')],
  });
  try {
    fill(slot, codeBox((await api.post('/api/sync/code', {})).code, null));
  } catch (error) {
    fill(slot);
    formError(slot, errorText(error));
  }
}

const HOW_IT_WORKS = () => explain('How does it work?',
  'The computers meet in a place you choose: a free online storage account such as Koofr, or a folder they already share.',
  'Each computer leaves its copy there in a sealed file and picks up the others’. The file is locked with a pairing code only your computers know: the storage cannot read it.',
  'Only one computer changes the rota at a time; the others can look, or ask to edit. Without connection you keep working: changes are sent when it is back.');

function onLine(status, keepOpen) {
  const [text, tone, title] = status.summary;  // worded by the program (sync.summary), as in the top bar
  const pen = status.pen || {};
  const attention = tone === 'bad' || tone === 'warn' || status.others.some((c) => c.state === 'gone');
  const auto = toggle('Take the others’ changes by themselves when nothing conflicts', status.auto, async (event) => {
    try {
      useStatus(await api.post('/api/sync/options', { auto: event.target.checked }));
    } catch (error) {
      toast(errorText(error), { tone: 'bad' });
    }
  });
  auto.input.dataset.focusId = 'sync-auto';
  // Opened by itself while something needs a look, unless the coordinator opened or closed it.
  // (Only a click on its title counts: a details element made open also fires "toggle".)
  const details = h('details', { class: 'explain sync-details', open: keepOpen.value ?? attention },
    h('summary', { dataset: { focusId: 'sync-details' }, onclick: () => { keepOpen.value = !details.open; } }, 'Details and options'),
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
      : h('p', { class: 'muted computers-none' }, 'No other computer yet: “Add a computer” says what to do on it.')),
  status.problem && status.kind !== 'alone' ? h('p', { class: status.passing ? 'muted' : 'warn-text' }, status.problem) : null,
  pen.text && !pen.mine ? h('p', { class: 'muted' }, icon('eye'), ' ', pen.text) : null,
  status.log.length ? h('div', { class: 'sync-activity' }, h('h4', {}, 'Activity'),
    h('ul', {}, status.log.map((entry) => h('li', { class: entry.tone }, h('span', { class: 'faint' }, entry.when), ' ', entry.text)))) : null,
  auto,
  h('div', { class: 'card-actions' },
    h('button', { class: 'btn', type: 'button', dataset: { focusId: 'sync-place' }, onclick: () => openSyncWizard({ placeOnly: true }) },
      status.place.kind === 'folder' ? 'Change the meeting place…' : 'Change the server or password…'),
    h('button', { class: 'btn ghost danger', type: 'button', dataset: { focusId: 'sync-stop' }, onclick: stopSync }, 'Stop synchronising…')),
  h('p', { class: 'faint' }, 'Everything at the meeting place is sealed with the pairing code: the server cannot read it. Comments of a month that is over are never sent.'));
  return statusLine({
    iconName: CLOUDS[status.icon] || 'cloud', title: 'Several computers', state: [text, tone], text: title,
    actions: [
      status.needs_choice ? h('button', { class: 'btn primary', type: 'button', dataset: { focusId: 'sync-choose' }, onclick: openSync }, icon('sync'), 'Synchronise…') : null,
      h('button', { class: 'btn', type: 'button', dataset: { focusId: 'sync-add' }, onclick: () => openAddComputer(status) }, 'Add a computer…'),
      h('button', { class: 'btn', type: 'button', dataset: { focusId: 'sync-check' }, title: 'Look at the meeting place now', onclick: async (e) => {
        e.currentTarget.disabled = true;
        try { useStatus(await api.post('/api/sync/check', {})); } catch (error) { toast(errorText(error), { tone: 'bad' }); refreshSync(); }
      } }, 'Check now')].filter(Boolean),
    extra: details,
  });
}

function offLine() {
  return statusLine({
    iconName: 'cloud', title: 'Several computers', state: ['Off'],
    text: 'Only this computer has the rota. To use it on another computer too, each keeps its own copy and changes travel between them by themselves.',
    actions: [h('button', { class: 'btn', type: 'button', dataset: { focusId: 'sync-setup' }, onclick: () => openSyncWizard({}) }, 'Set up…')],
    extra: HOW_IT_WORKS(),
  });
}

/** The line in Settings › Your data, kept up to date while it is on screen. */
export function syncLine() {
  const box = h('div', { class: 'status-block' }, statusLine({ iconName: 'cloud', title: 'Several computers', text: 'Loading…' }));
  const keepOpen = { value: null };  // "Details and options" stays as the coordinator left it
  let shown = '';
  const show = (status) => {
    if (!box.isConnected && box.dataset.shown) { stopWatching(); return; }
    if (!status) return;
    // The status is asked often: redraw only when what is shown changed (not the time of the last look,
    // nor how new the answer is, nor the steps of the editing changing hands, shown at the top).
    const same = JSON.stringify({ ...status, snapshot: null, run: null, pen: status.pen && { ...status.pen, steps: null },
      connection: { ...status.connection, last_ok: null } });
    if (same === shown) return;
    shown = same;
    box.dataset.shown = '1';
    replaceKeepingFocus(box, status.configured ? onLine(status, keepOpen) : offLine());
  };
  const stopWatching = onSyncChange(show);
  refreshSync().then(show).catch((error) => formError(box, errorText(error)));
  return box;
}

// ---- A computer with no data yet -----------------------------------------------

/** The first screen: join computers already set up, restore a backup copy, or start empty. */
export function welcomeScreen({ onStartEmpty }) {
  return h('div', { class: 'welcome' },
    h('h1', {}, 'Welcome to Rota Planner'),
    h('p', { class: 'muted' }, 'There is no data on this computer yet. Where do you want to begin?'),
    h('div', { class: 'choice-tiles welcome-tiles' },
      tile('cloud', 'Connect to existing data', 'Rota Planner is already used on another computer: join it with the pairing code, and everything arrives here.',
        false, () => openSyncWizard({ join: true })),
      tile('download', 'Restore a backup copy', 'Bring back everything from a file saved with “Save a copy” in Settings.',
        false, () => openRestore()),
      tile('plus', 'Start empty', 'Add people and tasks by hand, then create the first month.',
        false, () => { onStartEmpty(); window.location.hash = '#/people'; })));
}
