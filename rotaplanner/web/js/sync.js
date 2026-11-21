// The other computers: the cloud in the top bar, who is editing, the banners
// (someone else is editing; something needs a choice), and the Synchronise
// window. Everything is decided and worded by the program (sync.py); this
// only shows it, and asks again every few seconds (the program answers from
// what it already knows, never waiting for a server).
import { api, ApiError, onRefused } from './api.js';
import { applyReadOnly, formError, openDialog, refreshLookSlots, segmented, setLookBar } from './components.js';
import { fill, h } from './dom.js';
import { icon } from './icons.js';
import { errorText } from './setup.js';
import { setSession, state } from './state.js';
import { settingsLink, toast } from './ui.js';

const REFRESH_MS = 15_000;
export const CLOUDS = { ok: 'cloudOk', sending: 'cloudUp', offline: 'cloudOff', alert: 'cloudAlert' };
const ASKING_REFRESH_MS = 3_000;  // while asking for the pen: it arrives within a minute
const MOVING_REFRESH_MS = 1_000;  // while a take or an ask runs: each of its steps
const ARRIVED_MS = 1_500;         // the editing came here: its steps stay a moment, all done
const PEN_MOVES = ['take', 'ask', 'cancel'];  // the pen actions the look bar follows

let status = null;
let timer = 0;
let loading = null;      // the status request under way: never two at once
let again = false;       // asked for while one was under way: once more after it
let pill = null;
let penChip = null;
let chipShown = '';
let banner = null;
let lookTop = null;      // the look bar at the top of every screen, updated where it is
let otherShown = '';
let otherBanners = [];
let pending = null;      // the pen action pressed, until its request answers: {action}
let arrived = null;      // the steps that brought the editing here, all done, for a moment
let arrivedTimer = 0;
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

/** The pen shown now (null when other computers are not set up). */
const livePen = () => (status?.configured ? status.pen || null : null);

function renderPen() {
  const pen = status.pen || {};
  const action = pending?.action || '';
  const moving = !pen.mine && (action === 'take' || action === 'ask' || pen.busy || pen.asking);
  const viewing = Boolean(pen.holder && !pen.mine);
  const taking = pen.busy === 'take' || action === 'take' || pen.handed;
  const view = pen.mine ? ['mine', 'Only one computer changes the rota at a time: right now, this one.', 'You are editing']
    : moving ? ['other asking', pen.text, taking ? 'Taking over…' : `Asking ${pen.name}…`]
      : pen.working_offline ? ['offline', pen.text, 'Working offline']
        : viewing ? ['other', pen.text, `Look only · ${pen.name} is editing`] : null;
  const shown = JSON.stringify([view, Boolean(pending)]);
  if (shown === chipShown) return;  // a button under the pointer stays where it is
  chipShown = shown;
  penChip.hidden = !view;
  if (!view) return;
  const [kind, title, label] = view;
  penChip.className = `pen-chip ${kind}`;
  penChip.title = title || '';
  const busy = (what) => ({ 'aria-disabled': pending ? 'true' : null, 'aria-busy': pending?.action === what ? 'true' : null });
  if (pen.mine) {
    fill(penChip, icon('pen'), h('span', { class: 'topbar-label' }, label),
      h('button', { class: 'btn small', type: 'button', title: 'Send your changes and let another computer edit',
        ...busy('release'), onclick: () => penAction('release') }, 'I have finished'));
  } else if (moving) {
    fill(penChip, icon('sync'), h('span', { class: 'topbar-label' }, label));
  } else if (pen.working_offline) {
    fill(penChip, icon('cloudOff'), h('span', { class: 'topbar-label' }, label),
      h('button', { class: 'btn small', type: 'button', ...busy('offline'), onclick: () => penAction('offline', { on: false }) }, 'Stop'));
  } else {
    fill(penChip, icon('eye'), h('span', { class: 'topbar-label' }, label));
  }
}

/**
 * What the look bar shows, or null: why this computer only looks (another one
 * edits), or the editing coming here, step by step (`pen.steps`, worded by the
 * program). `live`: the pen shown now, with the action pressed on this page.
 */
function barView(pen, live) {
  if (!pen) return null;
  if (live && arrived && pen.mine) return { text: pen.text, how: '', steps: arrived, roles: [], busy: '', state: 'done' };
  const action = live && PEN_MOVES.includes(pending?.action) ? pending.action : '';
  const steps = pen.steps || [];
  if (!(pen.read_only || action || pen.busy || pen.asking || steps.length)) return null;
  const roles = !pen.read_only ? []
    : [pen.stale ? 'take' : null, pen.asking ? 'cancel' : !pen.stale && !pen.handing ? 'ask' : null].filter(Boolean);
  if (action && !roles.includes(action)) roles.push(action);
  const busy = action || pen.busy || '';
  const moving = Boolean(busy) || (pen.asking && !pen.stale) || steps.some((s) => s.state === 'now');
  // The program's "how to edit" is left out where the steps already say it (kept beside a warning: it explains "Take over…").
  const said = busy || (steps.length && !steps.some((s) => s.state === 'warn'));
  return { text: pen.text, how: said ? '' : pen.how, steps, roles, busy, state: moving ? 'moving' : 'looking' };
}

const BUTTONS = {  // [label, label while it runs, primary]
  take: ['Take over…', 'Taking over…', true],
  ask: ['Ask to edit', 'Asking…', true],
  cancel: ['Cancel', 'Cancel', false],
};

/** The step line: done ✓, the current one with a pulsing dot, a warning, what is still to come. */
function drawSteps(list, steps) {
  const shown = JSON.stringify(steps);
  if (list.shown === shown) return;
  list.shown = shown;
  list.hidden = !steps.length;
  fill(list, steps.map((step) => h('li', { class: ['pen-step', step.state], 'aria-current': step.state === 'now' ? 'step' : null },
    step.state === 'done' ? icon('check') : step.state === 'warn' ? icon('alert') : h('span', { class: 'pen-dot', 'aria-hidden': 'true' }),
    h('span', { class: 'pen-step-text' }, step.text),
    step.detail ? h('span', { class: 'pen-step-detail' }, step.detail) : null)));
}

/**
 * Why this computer only looks (another one edits), the way back to editing,
 * and the editing changing hands: at the top of every screen, and in any window
 * that would change something. Without `fixed` it follows the pen shown now:
 * `bar.update()` redraws it where it is (false when there is nothing to show),
 * so a button under the pointer is never replaced while its words change.
 * `then` runs after a button was used (a window closes itself).
 */
export function lookBar(fixed = null, then = () => {}) {
  const current = () => (fixed ? barView(fixed, false) : barView(livePen(), true));
  const first = current();
  if (!first) return null;
  const text = h('strong', {});
  const how = h('span', { class: 'look-how' });
  const steps = h('ol', { class: 'pen-steps' });
  const said = h('span', { class: 'visually-hidden', role: 'status' });  // the step reached, not every second's count
  const bar = h('div', { class: 'notice look-bar' }, icon('eye'), h('div', { class: 'look-main' }, h('span', {}, text, how), steps, said));
  let roles = null;
  let buttons = {};
  const press = {
    take: () => confirmTakeOver(fixed || livePen()),
    ask: () => askToEdit(),
    cancel: () => penAction('cancel'),
  };
  const draw = (view) => {
    const glyph = view.state === 'done' ? 'check' : view.state === 'moving' ? 'sync' : 'eye';
    bar.className = ['notice', 'look-bar', view.state === 'moving' ? 'asking' : '', view.state === 'done' ? 'done' : ''].filter(Boolean).join(' ');
    if (bar.dataset.icon !== glyph) {
      bar.firstElementChild.replaceWith(icon(glyph));
      bar.dataset.icon = glyph;
    }
    if (text.textContent !== (view.text || '')) text.textContent = view.text || '';
    const hint = view.how ? ` ${view.how}` : '';
    if (how.textContent !== hint) how.textContent = hint;
    drawSteps(steps, view.steps);
    const reached = view.state === 'done' ? view.steps.at(-1) : view.steps.find((s) => s.state === 'now' || s.state === 'warn');
    const words = reached?.text || '';
    if (said.textContent !== words) said.textContent = words;
    const key = view.roles.join(' ');
    if (key !== roles) {  // other buttons: made again (the words alone never do)
      roles = key;
      const focused = bar.contains(document.activeElement);
      for (const button of Object.values(buttons)) button.remove();
      buttons = Object.fromEntries(view.roles.map((role) => [role, h('button', {
        class: ['btn', 'small', BUTTONS[role][2] ? 'primary' : ''], type: 'button',
        // Busy, it keeps the focus (aria-disabled, not disabled): a keyboard user stays where they were.
        onclick: (event) => { if (pending || event.currentTarget.getAttribute('aria-disabled') === 'true') return; then(); press[role](); },
      }, BUTTONS[role][0])]));
      bar.append(...Object.values(buttons));
      if (focused && !bar.contains(document.activeElement)) Object.values(buttons)[0]?.focus({ preventScroll: true });
    }
    for (const [role, button] of Object.entries(buttons)) {
      const running = view.busy === role;
      if (view.busy) button.setAttribute('aria-disabled', 'true'); else button.removeAttribute('aria-disabled');
      if (running) button.setAttribute('aria-busy', 'true'); else button.removeAttribute('aria-busy');
      const label = BUTTONS[role][running ? 1 : 0];
      if (button.textContent !== label) button.textContent = label;
    }
  };
  bar.update = () => {
    const view = current();
    if (!view) return false;
    draw(view);
    return true;
  };
  draw(first);
  return bar;
}

/** Before starting a change: false, and says why and what to do, while another computer edits. */
export function mayChange() {
  const pen = status?.pen;
  if (!pen?.read_only) return true;
  explainLooking(pen);
  return false;
}

let explaining = null;  // the dialog saying so, if open (one is enough)

function explainLooking(pen) {
  if (explaining) return;
  const dialog = explaining = openDialog({
    onClose: () => { explaining = null; },
    title: pen.asking || pen.handing ? 'The editing is changing hands' : `${pen.name} is editing`,
    subtitle: 'Only one computer changes the rota at a time. You can look at everything here.',
    body: [lookBar(pen, () => dialog.close())],
    actions: [h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Only look')],
  });
}

function makeOtherBanners(pen) {
  const parts = [];
  const button = (label, onclick, primary = false) =>
    h('button', { class: ['btn', 'small', primary ? 'primary' : ''], type: 'button', onclick }, label);
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
  return parts;
}

/** The banners at the top: each made again only when what it says changed (no click lost to a refresh). */
function banners() {
  const pen = status.pen || {};
  if (!lookTop?.update()) lookTop = lookBar();
  const shown = JSON.stringify([pen.notice || null, status.hold, status.problem, status.to_fix, status.needs_choice,
    status.units.map((u) => [u.state, u.label])]);
  if (shown !== otherShown) {
    otherShown = shown;
    otherBanners = makeOtherBanners(pen);
  }
  const parts = [lookTop, ...otherBanners].filter(Boolean);
  const focused = banner.contains(document.activeElement);
  for (const kid of [...banner.children]) if (!parts.includes(kid)) kid.remove();
  parts.forEach((part, index) => {
    if (banner.children[index] !== part) banner.insertBefore(part, banner.children[index] || null);
  });
  banner.hidden = !parts.length;
  // The bar under the keyboard went (the editing came here): on to the pen in the top bar, not lost.
  if (focused && !banner.contains(document.activeElement)) (penChip.querySelector('button') || banner.querySelector('button'))?.focus({ preventScroll: true });
}

/** The screens follow who edits: the flag every screen reads, then every control and window. */
function follow() {
  const pen = livePen();
  const before = state.session.pen;
  const same = (a, b) => (a?.read_only || false) === (b?.read_only || false) && (a?.text || '') === (b?.text || '')
    && (a?.how || '') === (b?.how || '') && Boolean(a?.stale) === Boolean(b?.stale) && Boolean(a?.asking) === Boolean(b?.asking);
  if (same(before, pen)) return;
  state.session.pen = pen;  // set first, so that what is drawn now and applyReadOnly agree
  setSession({ pen });
  applyReadOnly();
}

function render() {
  follow();
  refreshLookSlots();  // the windows' look bars: the steps, in place
  if (!pill || !banner) return;
  if (!status?.configured) {
    pill.hidden = true;
    penChip.hidden = true;
    chipShown = '';
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

/**
 * Newer than the status shown? A slower answer made earlier never replaces a
 * newer one (`snapshot`, counted by the program); a program started again
 * (another `run`) counts from 1 again.
 */
function isNewer(answer) {
  if (!answer?.snapshot || !status?.snapshot) return true;
  return answer.run !== status.run || answer.snapshot >= status.snapshot;
}

/** Shows `answer`; the editing just came here: its steps stay a moment, all done. */
function accept(answer) {
  const before = livePen();
  status = answer;
  const pen = livePen();
  if (pen?.mine && before && !before.mine && before.steps?.length) {
    arrived = before.steps.map((step) => ({ text: step.text, state: 'done', detail: '' }));
    clearTimeout(arrivedTimer);
    arrivedTimer = setTimeout(() => { arrived = null; render(); }, ARRIVED_MS);
  } else if (!pen?.mine) {
    arrived = null;
  }
}

async function load() {
  try {
    const answer = await api.get('/api/sync/status');
    if (isNewer(answer)) {
      const previous = status;
      accept(answer);
      if (status.configured) announce(previous);
    }
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return status;
    // Never hide the warning when something is wrong: keep what was known, and say it.
    if (status?.configured) {
      const problem = `The other computers could not be checked: ${errorText(error)}`;
      status = { ...status, problem, kind: 'error', icon: 'alert', summary: ['Needs attention', 'bad', problem] };
    }
  } finally {
    schedule();
  }
  render();
  for (const watcher of watchers) watcher(status);
  return status;
}

export async function refreshSync() {
  if (loading) {  // one request at a time; one more once it has answered
    again = true;
    return loading;
  }
  loading = load();
  try {
    return await loading;
  } finally {
    loading = null;
    if (again) {
      again = false;
      refreshSync();
    }
  }
}

/** The next look: every second while the editing is changing hands here, every few while asking. */
function schedule() {
  clearTimeout(timer);
  const pen = livePen();
  const wait = pending || pen?.busy ? MOVING_REFRESH_MS : pen?.asking ? ASKING_REFRESH_MS : REFRESH_MS;
  timer = setTimeout(refreshSync, wait);
}

/** Use a status the program just answered with (after an action), without asking again. */
export function useStatus(answer) {
  if (isNewer(answer)) {
    accept(answer);
    if (status?.configured) seenSeq = status.applied?.seq || 0;
  }
  render();
  for (const watcher of watchers) watcher(status);
  schedule();
}

/** One pen action at a time: pressed again (or another) while one runs, nothing happens. */
async function penAction(action, more = {}) {
  if (pending) return false;
  pending = { action };
  render();
  schedule();
  try {
    useStatus(await api.post('/api/sync/pen', { action, ...more }));
    return true;
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 423)) toast(errorText(error), { tone: 'bad' });
    return false;
  } finally {
    pending = null;
    render();
    schedule();
  }
}

/** "Ask to edit": the computer editing sends its changes and hands over by itself. */
async function askToEdit() {
  const asked = await penAction('ask');
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
        dialog.close();  // the steps show at the top of the screen (and in any open window)
        if (await penAction('take', { force: true }) && status.pen?.mine) toast('You are editing now.');
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
  else if ((payload.kind === 'held' || payload.kind === 'handing') && payload.pen?.read_only) {
    explainLooking(payload.pen);
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
  setLookBar(() => lookBar());
  refreshSync();  // then again and again (schedule)
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
