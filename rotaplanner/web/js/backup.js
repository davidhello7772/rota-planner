// A backup copy: everything (people, tasks, months, settings) in one file, to
// keep safe or to bring back, here or on a new computer. The line for
// Settings, and its two windows (save a copy, restore one).
import { api, ApiError } from './api.js';
import { field, formError, openDialog, readFileBase64, statusLine, toggle } from './components.js';
import { fill, h } from './dom.js';
import { icon } from './icons.js';
import { errorText } from './setup.js';
import { toast } from './ui.js';

function saveFile(name, base64) {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/zip' }));
  const link = h('a', { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function openSaveCopy() {
  const comments = toggle('Include the availability comments', false);
  const go = h('button', { class: 'btn primary', type: 'button' }, icon('download'), 'Save the file');
  const body = h('div', { class: 'form-part' },
    h('p', {}, 'The file holds everything about the people of the centre. Keep it on a USB stick or in a private folder: never send it by e-mail.'),
    comments,
    h('p', { class: 'field-hint' }, 'Comments left with availability replies can mention health. Leave this off, unless you move to a new computer before this month’s comments are dealt with.'));
  const dialog = openDialog({
    title: 'Save a backup copy',
    subtitle: 'People, tasks, months and settings, in one file.',
    body: [body],
    actions: [h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'), go],
  });
  go.onclick = async () => {
    go.disabled = true;
    try {
      const data = await api.post('/api/data/export', { include_comments: comments.input.checked });
      saveFile(data.name, data.file);
      dialog.close();
      toast('Backup copy saved: see your browser’s downloads');
    } catch (error) {
      go.disabled = false;
      formError(body, errorText(error));
    }
  };
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

export function openRestore() {
  const file = h('input', { type: 'file', accept: '.zip', class: 'file' });
  const result = h('div', { class: 'form-part', role: 'status' });
  const go = h('button', { class: 'btn primary danger', type: 'button', hidden: true }, 'Replace my data with it');
  const body = h('div', { class: 'form-part' },
    h('p', {}, 'Brings back everything from a backup copy. What is on this computer now is replaced; it is kept in a safety copy first.'),
    field('Backup copy (.zip)', file),
    result);
  const dialog = openDialog({
    title: 'Restore a backup copy',
    body: [body],
    actions: [h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'), go],
  });
  let chosen = null;
  let asked = 0;  // another file picked meanwhile: only the last one is shown and restored
  file.addEventListener('change', async () => {
    const mine = (asked += 1);
    go.hidden = true;
    chosen = null;
    fill(result);
    if (!file.files[0]) return;
    fill(result, h('p', { class: 'muted' }, 'Reading the file…'));
    try {
      const body = { file: await readFileBase64(file.files[0]) };
      const info = await api.post('/api/data/import/preview', body);
      if (mine !== asked) return;
      chosen = body;
      const here = info.here.months.length || info.here.people
        ? `It replaces what is on this computer now (${info.here.people} ${info.here.people === 1 ? 'person' : 'people'}, ${plural(info.here.months.length, 'month')}).`
        : 'There is nothing on this computer yet.';
      fill(result, h('div', { class: 'import-preview' },
        h('strong', {}, `Backup copy of ${info.created}`),
        h('span', {}, `${info.people} people · ${info.tasks} tasks · ${info.months.length ? info.months.join(', ') : 'no month'}`),
        h('span', { class: 'muted' }, info.comments ? 'With the availability comments.' : 'Without the availability comments.'),
        h('span', { class: 'warn-text' }, here)));
      go.hidden = false;
    } catch (error) {
      if (mine !== asked) return;
      fill(result);
      formError(result, error instanceof ApiError ? errorText(error) : 'The file could not be read.');
    }
  });
  go.onclick = async () => {
    if (!chosen) return;
    go.disabled = true;
    try {
      const done = await api.post('/api/data/import/apply', chosen);
      toast(`Restored · what was here is kept in backups/${done.safety}`);
      setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      go.disabled = false;
      formError(result, errorText(error));
    }
  };
}

/** The line on the Settings page. */
export function backupLine() {
  return statusLine({
    iconName: 'download', title: 'Backup copy',
    text: 'Everything in one file, to keep safe or to bring back later, here or on a new computer.',
    actions: [
      h('button', { class: 'btn', type: 'button', onclick: openSaveCopy }, 'Save a copy…'),
      h('button', { class: 'btn', type: 'button', onclick: openRestore }, 'Restore…')],
  });
}
