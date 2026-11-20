// The other computers: the cloud in the top bar, who is editing, the banners
// (someone else is editing; something needs a choice), and the Synchronise
// window. Everything is decided and worded by the program (sync.py); this
// only shows it, and asks again every few seconds (the program answers from
// what it already knows, never waiting for a server).
import { api, ApiError, onRefused } from './api.js';
import { formError, openDialog, segmented } from './components.js';
import { fill, h } from './dom.js';
import { icon } from './icons.js';
import { errorText } from './setup.js';
import { settingsLink, toast } from './ui.js';

const REFRESH_MS = 15_000;
export const CLOUDS = { ok: 'cloudOk', sending: 'cloudUp', offline: 'cloudOff', alert: 'cloudAlert' };
const ASKING_REFRESH_MS = 3_000;  // while asking for the pen: it arrives within a minute

let status = null;
let timer = 0;
let soon = 0;
let pill = null;
let penChip = null;
let banner = null;
let seenSeq = null;      // the last "taken from another computer" this page has shown
let wasOnline = null;
const watchers = new Set();

/** The last status the program gave (null before the first answer). */
export const syncStatus = () => status;

/** Called with the status every time it is refreshed (the line in Settings listens). */
export function onSyncChange(callback) {
  watchers.add(callback);
  return () => watchers.delete(callback);
}

function renderPen() {
  const pen = status.pen || {};
  const viewing = Boolean(pen.holder && !pen.mine);
  penChip.hidden = !(pen.mine || viewing || pen.working_offline);
  if (penChip.hidden) return;
  if (pen.mine) {
    penChip.className = 'pen-chip mine';
    penChip.title = 'Only one computer changes the rota at a time: right now, this one.';
    fill(penChip, icon('pen'), h('span', { class: 'topbar-label' }, 'You are editing'),
      h('button', { class: 'btn small', type: 'button', title: 'Send your changes and let another computer edit',
        onclick: () => penAction('release') }, 'I have finished'));
  } else if (pen.asking) {
    penChip.className = 'pen-chip other asking';
    penChip.title = pen.text;
    fill(penChip, icon('sync'), h('span', { class: 'topbar-label' }, `Asking ${pen.name}…`));
  } else if (pen.working_offline) {
    penChip.className = 'pen-chip offline';
    penChip.title = pen.text;
    fill(penChip, icon('cloudOff'), h('span', { class: 'topbar-label' }, 'Working offline'),
      h('button', { class: 'btn small', type: 'button', onclick: () => penAction('offline', { on: false }) }, 'Stop'));
  } else {
    penChip.className = 'pen-chip other';
    penChip.title = pen.text;
    fill(penChip, icon('eye'), h('span', { class: 'topbar-label' }, `${pen.name} is editing`));
  }
}

function banners() {
  const pen = status.pen || {};
  const parts = [];
  const button = (label, onclick, primary = false) =>
    h('button', { class: ['btn', 'small', primary ? 'primary' : ''], type: 'button', onclick }, label);
  if (pen.asking && !pen.mine) {
    parts.push(h('div', { class: ['notice', 'look-bar', pen.stale ? '' : 'asking'] }, icon(pen.stale ? 'eye' : 'sync'),
      h('span', {}, pen.text, pen.hint ? h('span', { class: 'muted' }, ` ${pen.hint}`) : null),
      pen.stale ? button('Take over…', () => confirmTakeOver(pen), true) : null,
      button('Cancel', () => penAction('cancel'))));
  } else if (pen.holder && !pen.mine && !pen.working_offline) {
    parts.push(h('div', { class: 'notice look-bar' }, icon('eye'), h('span', {}, pen.text),
      pen.stale ? button('Take over…', () => confirmTakeOver(pen))
        : button('Ask to edit', (event) => askToEdit(event.currentTarget), true)));
  }
  if (pen.notice) {
    const warn = pen.notice.tone === 'warn';
    parts.push(h('div', { class: ['notice', warn ? 'warn' : 'info', 'sync-banner'] }, icon(warn ? 'alert' : 'info'),
      h('span', {}, pen.notice.text), button('OK', () => penAction('release'))));
  }
  const both = status.units.filter((u) => u.state === 'both');
  const newer = status.units.filter((u) => u.state === 'get');
  const list = (units) => h('strong', {}, units.map((u) => u.label).join(', '));
  if (status.hold === 'restored') {
    parts.push(h('div', { class: 'notice warn sync-banner' }, icon('alert'), h('span', {}, status.problem),
      h('button', { class: 'btn small', type: 'button', onclick: () => chooseAfterRestore() }, 'Choose…')));
  } else if (both.length) {
    parts.push(h('div', { class: 'notice warn sync-banner' }, icon('alert'),
      h('span', {}, 'Changed on more than one computer: ', list(both), '. Choose which version to keep.'),
      h('button', { class: 'btn small primary', type: 'button', onclick: openSync }, 'Choose…')));
  } else if (newer.length && status.needs_choice) {
    parts.push(h('div', { class: 'notice warn sync-banner' }, icon('alert'),
      h('span', {}, 'Another computer has changes you do not have yet: ', list(newer), '.'),
      h('button', { class: 'btn small primary', type: 'button', onclick: openSync }, 'Synchronise')));
  } else if (status.to_fix) {
    parts.push(h('div', { class: 'notice warn sync-banner' }, icon('alert'), h('span', {}, status.problem),
      settingsLink('sync', 'Open Settings', 'btn small')));
  }
  banner.hidden = !parts.length;
  fill(banner, parts);
}

function render() {
  if (!pill || !banner) return;
  if (!status?.configured) {
    pill.hidden = true;
    penChip.hidden = true;
    banner.hidden = true;
    return;
  }
  const [text, tone, title] = status.summary;  // worded by the program (sync.summary)
  pill.hidden = false;
  pill.className = ['btn', 'ghost', 'sync-pill', tone, status.icon].filter(Boolean).join(' ');
  pill.title = `${title} Click for the details.`;
  pill.setAttribute('aria-label', `Several computers: ${text}. ${title}`);
  fill(pill, icon(CLOUDS[status.icon] || 'cloud'), h('span', { class: 'topbar-label' }, text));
  renderPen();
  banners();
}

/** What changed since the last look: taken from another computer, connection lost or back. */
function announce(previous) {
  if (previous?.pen?.asking && status.pen?.mine) toast(`You are editing now: ${previous.pen.name}’s changes were taken first.`);
  const seq = status.applied?.seq || 0;
  if (seenSeq !== null && seq > seenSeq) {
    toast(status.applied.text || 'Updated from another computer.');
    window.dispatchEvent(new CustomEvent('rota:data-changed'));
  }
  seenSeq = seq;
  const online = status.connection?.online;
  if (wasOnline === true && online === false) toast('No connection to the other computers: keep working, your changes are kept here.');
  if (wasOnline === false && online === true && previous) toast('Back online.');
  if (online !== null && online !== undefined) wasOnline = online;
}

export async function refreshSync() {
  const previous = status;
  try {
    status = await api.get('/api/sync/status');
    if (status.configured) announce(previous);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return status;
    // Never hide the warning when something is wrong: keep what was known, and say it.
    if (status?.configured) {
      const problem = `The other computers could not be checked: ${errorText(error)}`;
      status = { ...status, problem, kind: 'error', icon: 'alert', summary: ['Needs attention', 'bad', problem] };
    }
  }
  render();
  for (const watcher of watchers) watcher(status);
  hurry();
  return status;
}

/** While asking for the pen, look again every few seconds (the program answers from memory). */
function hurry() {
  clearTimeout(soon);
  if (status?.pen?.asking) soon = setTimeout(refreshSync, ASKING_REFRESH_MS);
}

/** Use a status the program just answered with (after an action), without asking again. */
export function useStatus(answer) {
  status = answer;
  if (status?.configured) seenSeq = status.applied?.seq || 0;
  render();
  for (const watcher of watchers) watcher(status);
  hurry();
}

async function penAction(action, more = {}) {
  try {
    useStatus(await api.post('/api/sync/pen', { action, ...more }));
    return true;
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 423)) toast(errorText(error), { tone: 'bad' });
    return false;
  }
}

/** "Ask to edit": the computer editing sends its changes and hands over by itself. */
async function askToEdit(button = null) {
  if (button) {  // the first look at the other computers takes a moment: show it is under way
    button.disabled = true;
    button.textContent = 'Asking…';
  }
  const asked = await penAction('ask');
  if (button) {
    button.disabled = false;
    button.textContent = 'Ask to edit';
  }
  if (asked && status.pen?.mine) toast('You are editing now.');
}

function confirmTakeOver(pen) {
  const dialog = openDialog({
    title: 'Take over the editing?',
    subtitle: pen.text,
    body: [h('p', {}, 'Do this only if you are sure they have stopped. What they changed and have not sent yet '
      + 'may then have to be chosen against your changes, one or the other.')],
    actions: [h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: async () => {
        if (await penAction('take', { force: true })) { dialog.close(); toast('You are editing now.'); }
      } }, 'Take over')],
  });
}

function chooseAfterRestore() {
  const choose = async (keep) => {
    try {
      useStatus(await api.post('/api/sync/options', { resume: keep }));
      dialog.close();
    } catch (error) {
      formError(dialog.panel.querySelector('.modal-body'), errorText(error));
    }
  };
  const dialog = openDialog({
    title: 'Which data to keep?',
    subtitle: 'This computer’s data is older than what it had sent: a backup was probably put back.',
    body: [h('p', {}, 'Either the other computers’ newer data comes here, or this computer’s older data becomes the newest everywhere.')],
    actions: [h('button', { class: 'btn', type: 'button', onclick: () => choose('mine') }, 'Keep this computer’s older data'),
      h('button', { class: 'btn primary', type: 'button', onclick: () => choose('newer') }, 'Take the newer data')],
  });
}

/** A change was refused: someone else edits, there is no connection, or newer data just arrived. */
function refusedChange(code, payload) {
  if (code === 409) {  // newer data was taken while taking the pen: show it
    window.dispatchEvent(new CustomEvent('rota:data-changed'));
    refreshSync();
    return;
  }
  refreshSync();
  if (payload.kind === 'stale' && payload.pen) confirmTakeOver(payload.pen);
  else if (payload.kind === 'behind') openSync();
  else if (payload.kind === 'handing') toast(payload.error, { tone: 'bad' });
  else if (payload.kind === 'held' && payload.pen?.name && !status?.pen?.asking) {
    const dialog = openDialog({
      title: `${payload.pen.name} is editing`,
      subtitle: 'Only one computer changes the rota at a time. You can look at everything here.',
      body: [h('p', {}, `If you left Rota Planner open on ${payload.pen.name}, ask it to hand over: it sends `
        + 'its changes first, then you can edit here, usually within a minute.')],
      actions: [h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Only look'),
        h('button', { class: 'btn primary', type: 'button', onclick: async () => {
          dialog.close();
          await askToEdit();
        } }, 'Ask to edit')],
    });
  }
  else if (payload.kind === 'offline') {
    const dialog = openDialog({
      title: 'No connection to the other computers',
      subtitle: 'So this computer cannot make sure nobody else is editing.',
      body: [h('p', {}, 'You can look at everything. If you change things all the same, your changes are sent when the '
        + 'connection is back; if someone else changed the same month or list meanwhile, you will be asked which to keep.')],
      actions: [h('button', { class: 'btn primary', type: 'button', onclick: () => dialog.close() }, 'Only look'),
        h('button', { class: 'btn', type: 'button', onclick: async () => {
          if (await penAction('offline', { on: true })) { dialog.close(); toast('Working offline: make your change again.'); }
        } }, 'Work offline anyway')],
    });
  }
}

/** Places the cloud, the pen and the banners (once, by the shell) and keeps them current. */
export function mountSync(pillSlot, bannerSlot) {
  pill = settingsLink('sync', '', 'btn ghost sync-pill');
  pill.hidden = true;
  penChip = h('span', { class: 'pen-chip', hidden: true });
  banner = bannerSlot;
  pillSlot.replaceWith(pill, penChip);
  onRefused(refusedChange);
  refreshSync();
  clearInterval(timer);
  timer = setInterval(refreshSync, REFRESH_MS);
  window.addEventListener('focus', refreshSync);
}

// ---- The Synchronise window -------------------------------------------------

export async function openSync() {
  const body = h('div', { class: 'sync-body' }, h('p', { class: 'muted' }, 'Looking…'));
  const go = h('button', { class: 'btn primary', type: 'button', disabled: true }, icon('sync'), 'Synchronise');
  let synced = false;  // the data changed: the screens are loaded again on closing
  const dialog = openDialog({
    title: 'Synchronise', subtitle: 'What other computers changed, and what needs your choice.', wide: true,
    body: [body], actions: [h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Close'), go],
    onClose: () => { if (synced) window.dispatchEvent(new CustomEvent('rota:data-changed')); refreshSync(); },
  });
  let preview;
  try {
    preview = await api.post('/api/sync/preview', {});
  } catch (error) {
    fill(body, h('p', { class: 'warn-text' }, errorText(error)),
      h('p', { class: 'muted' }, 'You can go on working: your changes are kept and sent as soon as possible.'));
    return;
  }
  const others = preview.other || 'the other computers';
  const choices = {};
  const both = preview.items.filter((i) => i.state === 'both');
  const get = preview.items.filter((i) => i.state === 'get');
  const choiceFor = (item) => segmented(
    item.choices.map((c) => ({ value: c.id, label: c.id === 'mine' ? 'This computer’s' : `${c.name}’s` })),
    '', { label: `Which ${item.label} to keep`, onchange: (value) => { choices[item.unit] = value; } });
  fill(body,
    !preview.items.length ? h('p', { class: 'ok-text' }, `Nothing new from ${others}.`) : null,
    get.length ? [h('h3', { class: 'form-section' }, 'Newer elsewhere: will be taken'),
      h('ul', { class: 'sync-list' }, get.map((i) => h('li', {}, icon('check'), i.label,
        i.from ? h('span', { class: 'muted' }, ` · from ${i.from}`) : null)))] : null,
    both.length ? [h('h3', { class: 'form-section' }, 'Changed on more than one computer: choose which to keep'),
      h('p', { class: 'muted' }, 'Nothing is mixed: one version is kept whole. The others are not lost: this computer saves a copy of its data before it changes.'),
      h('ul', { class: 'sync-list choose' }, both.map((i) => h('li', {},
        h('div', {}, h('strong', {}, i.label),
          h('span', { class: 'muted' }, i.choices.filter((c) => c.difference).map((c) => ` · ${c.name}: ${c.difference}`).join(''))),
        choiceFor(i))))] : null,
    preview.sent.length ? h('p', { class: 'muted' }, `Sent, waiting for the others to take them: ${preview.sent.join(', ')}.`) : null);
  go.disabled = !preview.items.length;
  go.onclick = async () => {
    const missing = both.filter((i) => !choices[i.unit]);
    if (missing.length) {
      formError(body, `Choose which to keep for: ${missing.map((i) => i.label).join(', ')}.`);
      return;
    }
    go.disabled = true;
    try {
      const done = await api.post('/api/sync/apply', { plan: preview.plan, choices });
      synced = true;
      go.hidden = true;
      seenSeq = null;  // this page asked for it: no "updated" message on top
      fill(body,
        h('p', { class: 'ok-text' }, done.taken.length ? `Taken from ${done.from.join(', ') || others}: ${done.taken.join(', ')}.` : 'Kept this computer’s versions.'),
        done.kept.length ? h('p', {}, `Kept from this computer: ${done.kept.join(', ')} (the others take them by themselves).`) : null,
        done.safety ? h('p', { class: 'muted' }, `The data as it was is kept in backups/${done.safety}.`) : null,
        done.warnings.length ? h('div', { class: 'warnings' }, h('h3', {}, 'To check'), h('ul', {}, done.warnings.map((w) => h('li', {}, w)))) : null);
      toast('Synchronised');
      if (!done.warnings.length) setTimeout(() => dialog.close(), 1500);
    } catch (error) {
      go.disabled = false;
      formError(body, errorText(error));
    }
  };
}
