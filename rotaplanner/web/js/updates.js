// A newer version of the program: its line in Settings, the word at start-up,
// and installing it once the coordinator said yes. The program looks and
// installs (updates.py); this shows what it found and waits while it starts again.
import { api } from './api.js';
import { explain, formError, openDialog, statusLine, toggle } from './components.js';
import { fill, h } from './dom.js';
import { icon } from './icons.js';
import { errorText } from './setup.js';
import { toast } from './ui.js';
import { setSession } from './state.js';

const whatIsNew = (info) => h('ul', { class: 'update-list' },
  info.items.map((item) => h('li', {}, item)),
  info.more ? h('li', { class: 'faint' }, `… and ${info.more} more`) : null);

/** Waits until the program that answers is no longer the one that installed (`boot` names
 * one run of the program), then reloads the page: the new version serves it. */
async function waitForRestart(boot) {
  for (let i = 0; i < 180; i += 1) {
    await new Promise((resolve) => { setTimeout(resolve, 500); });
    try {
      if ((await api.get('/api/update')).boot !== boot) break;
    } catch (error) {
      if (error.status) break;  // it answers again, and asks for the password or a new address
    }
  }
  window.location.reload();
}

/** Looks once more first, so the list shown is exactly what will be installed. */
async function confirmInstall(known, onDone) {
  let info = known;
  try { info = await api.post('/api/update/check', {}); } catch { /* no connection now: what was found earlier */ }
  if (!info.available) { onDone?.(); return; }
  const body = h('div', { class: 'update-body' },
    h('p', {}, 'What is new:'), whatIsNew(info),
    h('ul', { class: 'summary-list' },
      h('li', {}, 'A copy of all the data is saved first (backups/before-update-…).'),
      h('li', {}, 'Rota Planner starts again by itself; this page comes back after a few seconds.'),
      h('li', {}, 'If other computers share this rota, update them the same day.')));
  const go = h('button', { class: 'btn primary', type: 'button' }, icon('download'), 'Update and start again');
  const dialog = openDialog({
    title: 'Update Rota Planner?', subtitle: `This computer has the version of ${info.version}.`,
    body: [body], actions: [h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Not now'), go],
  });
  go.onclick = async () => {
    go.disabled = true;
    fill(body, h('p', { class: 'muted' }, 'Saving a copy of the data and installing the new version…'));
    try {
      const done = await api.post('/api/update/install', { target: info.target });
      fill(body, h('p', { class: 'ok-text' }, icon('check'), ' Installed. Rota Planner is starting again…'));
      go.hidden = true;
      await waitForRestart(done.boot);
    } catch (error) {
      go.disabled = false;
      fill(body, h('p', { class: 'warn-text' }, errorText(error)));
      onDone?.();
    }
  };
}

/** The line in Settings › The program: which version, whether a newer one exists, and installing it. */
export function updateLine() {
  const box = h('div', { class: 'status-block' }, statusLine({ iconName: 'download', title: 'Version and updates', text: 'Loading…' }));
  const show = (info) => {
    if (!info.supported) {
      fill(box, statusLine({ iconName: 'download', title: 'Version and updates', text: info.reason }));
      return;
    }
    const auto = toggle('Look for a new version by itself, once a day', info.auto, async (event) => {
      try { show(await api.post('/api/update/options', { auto: event.target.checked })); } catch (error) { formError(box, errorText(error)); }
    });
    const check = h('button', { class: 'btn', type: 'button', onclick: async () => {
      check.disabled = true;
      check.textContent = 'Looking…';
      try { show(await api.post('/api/update/check', {})); } catch (error) { show(info); formError(box, errorText(error)); }
    } }, 'Check now');
    const version = `This computer has the version of ${info.version || 'an unknown date'}.`;
    const looked = info.checked_text ? ` Last looked on ${info.checked_text}.` : ' Not looked yet.';
    fill(box, statusLine({
      iconName: 'download', title: 'Version and updates',
      state: info.available ? ['New version', 'warn'] : info.problem ? ['Could not look', 'warn'] : info.checked_text ? ['Up to date', 'ok'] : null,
      text: info.problem ? `${version} ${info.problem}` : version + (info.available ? '' : looked),
      actions: [
        info.available ? h('button', { class: 'btn primary', type: 'button', onclick: () => confirmInstall(info, refresh) }, 'Update…') : null,
        check].filter(Boolean),
      extra: [
        info.available ? h('div', { class: 'update-new' }, h('strong', {}, 'What is new:'), whatIsNew(info)) : null,
        auto,
        explain('Does looking send anything?',
          'No. Looking only asks the program’s page on GitHub whether there is something new: nothing about your data is sent.',
          'Nothing is ever installed without your yes. Before installing, all the data is copied to the backups folder; if the new version does not start, the previous one comes back.')],
    }));
  };
  const refresh = () => api.get('/api/update').then(show).catch((error) => formError(box, errorText(error)));
  refresh();
  return box;
}

/** Once per visit, at start-up: say that a newer version is there (found by the daily look). */
export async function announceUpdate() {
  try {
    if (sessionStorage.getItem('rota-planner-update-said')) return;
    const info = await api.get('/api/update');
    if (!info.supported || !info.available) return;
    sessionStorage.setItem('rota-planner-update-said', '1');
    toast('A new version of Rota Planner is available.', { actionLabel: 'See', onAction: () => { setSession({ settingsFocus: 'update' }); window.location.hash = '#/settings'; } });
  } catch { /* not now: Settings says it anyway */ }
}
