// Schedules: pick a person in a few keystrokes and copy their schedule,
// ready to paste in an e-mail; then the next person. Everyone's message is
// loaded at once (and again after each change to the rota), so it is instant.
import { api, monthPath, printUrl } from './api.js';
import { openDialog } from './components.js';
import { h } from './dom.js';
import { icon } from './icons.js';
import { errorText } from './setup.js';
import { setSession, state, subscribe } from './state.js';
import { copyText, toast } from './ui.js';

const SHOWN = 8;  // names listed at once
let loading = null;

/** Everyone's messages for the month on screen (null while loading). */
function messages() {
  const month = state.month;
  if (!month) return null;
  const loaded = state.session.schedules;
  if (loaded?.of === month) return loaded.people;
  if (loading !== month) {
    loading = month;
    api.get(monthPath(month.id, '/messages'))
      .then((result) => setSession({ schedules: { of: month, people: result.people } }))
      .catch((error) => toast(errorText(error), { tone: 'bad' }))
      .finally(() => { loading = null; });
  }
  return loaded?.of.id === month.id ? loaded.people : null;  // the previous version meanwhile
}

const copyMessage = (text, who) => copyText(text, `${who}’s schedule copied: paste it in your e-mail`);

/** Copy one person's schedule (from the Build panel, for instance). */
export async function copySchedule(personId) {
  // Only messages made from the rota as it is now: the panel copies right after a change.
  let people = state.session.schedules?.of === state.month ? state.session.schedules.people : null;
  if (!people) {
    const result = await api.get(monthPath(state.month.id, '/messages'));
    setSession({ schedules: { of: state.month, people: result.people } });
    people = result.people;
  }
  const person = people.find((p) => p.id === personId);
  if (person) await copyMessage(person.message, person.name);
}

function download(name, content) {
  const url = URL.createObjectURL(new Blob([content], { type: 'text/calendar' }));
  const link = h('a', { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function calendarFile(person) {
  try {
    const data = await api.get(monthPath(state.month.id, `/personal?person=${encodeURIComponent(person.id)}`));
    download(data.calendar_name, data.calendar);
  } catch (error) {
    toast(errorText(error), { tone: 'bad' });
  }
}

/**
 * The finder: a search box, the matching people, and the chosen person's text.
 * Typing filters, ↑ ↓ choose, Enter copies and gets ready for the next name.
 * Its own state lives in the element, so it survives the screen redrawing around it.
 */
export function schedulesFinder() {
  const query = h('input', { class: 'input', type: 'search', placeholder: 'Type a name…', 'aria-label': 'Find a person',
    autocomplete: 'off', spellcheck: 'false', dataset: { focusId: 'schedules-finder' } });
  const list = h('ul', { class: 'finder-list', role: 'listbox', 'aria-label': 'People' });
  const preview = h('div', { class: 'finder-preview' });
  let active = 0;
  let matches = [];

  const chosen = () => matches[active] || null;

  function showPreview() {
    const person = chosen();
    if (!person) {
      preview.replaceChildren(h('p', { class: 'faint' }, messages() ? 'No one called that.' : 'Loading…'));
      return;
    }
    preview.replaceChildren(
      h('div', { class: 'finder-preview-head' },
        h('strong', {}, person.full),
        h('span', { class: 'muted' }, person.shifts ? `${person.shifts} shift${person.shifts > 1 ? 's' : ''} · ${person.hours} h` : 'No shift')),
      h('pre', { class: 'finder-text' }, person.message),
      h('div', { class: 'finder-actions' },
        h('button', { class: 'btn primary', type: 'button', onclick: () => copyAndNext() }, icon('check'), 'Copy', h('kbd', {}, 'Enter')),
        h('button', { class: 'btn', type: 'button', disabled: !person.shifts, onclick: () => calendarFile(person) }, icon('calendar'), 'Calendar file'),
        h('button', { class: 'btn ghost', type: 'button', disabled: !person.shifts,
          onclick: () => window.open(printUrl(state.month.id, 'people', { person: person.id }), '_blank') }, 'Print')));
  }

  function refresh() {
    const people = messages() || [];
    const text = query.value.trim().toLowerCase();
    matches = people.filter((p) => !text || p.name.toLowerCase().includes(text) || p.full.toLowerCase().includes(text));
    // People who start with what was typed come first.
    if (text) matches.sort((a, b) => Number(!a.name.toLowerCase().startsWith(text)) - Number(!b.name.toLowerCase().startsWith(text)));
    active = Math.min(active, Math.max(0, matches.length - 1));
    list.replaceChildren(...matches.slice(0, SHOWN).map((p, i) => h('li', {
      role: 'option', 'aria-selected': String(i === active), class: ['finder-item', i === active && 'active', !p.shifts && 'none'],
      onmousedown: (e) => e.preventDefault(),  // keep the focus in the box
      onclick: () => { active = i; refresh(); },
      ondblclick: () => { active = i; copyAndNext(); },
    }, h('span', { class: 'finder-name' }, p.name), h('span', { class: 'faint' }, p.shifts ? `${p.shifts} · ${p.hours} h` : '—'))),
    matches.length > SHOWN ? h('li', { class: 'faint finder-more' }, `${matches.length - SHOWN} more: type more letters`) : '');
    showPreview();
  }

  async function copyAndNext() {
    const person = chosen();
    if (!person) return;
    if (await copyMessage(person.message, person.name)) {
      query.value = '';
      active = 0;
      refresh();
      query.focus();
    }
  }

  query.addEventListener('input', () => { active = 0; refresh(); });
  query.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); active = Math.min(active + 1, Math.min(matches.length, SHOWN) - 1); refresh(); }
    if (event.key === 'ArrowUp') { event.preventDefault(); active = Math.max(active - 1, 0); refresh(); }
    if (event.key === 'Enter') { event.preventDefault(); copyAndNext(); }
  });

  const element = h('div', { class: 'finder' },
    h('div', { class: 'finder-pick' }, h('label', { class: 'search' }, icon('search'), query), list), preview);
  element.refresh = refresh;
  element.focus = () => query.focus();
  refresh();
  return element;
}

/** The finder in a dialog, from anywhere (top bar, key S). */
export function openSchedules() {
  if (!state.month) return;
  const finder = schedulesFinder();
  const dialog = openDialog({
    title: `Schedules — ${state.month.title}`, wide: true,
    subtitle: 'Type a few letters, Enter copies the schedule, paste it in your e-mail; then the next name.',
    body: [finder],
    actions: [h('span', { class: 'spacer' }), h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Close')],
  });
  // Messages may arrive (or change) while the dialog is open.
  const stop = subscribe(() => { if (finder.isConnected) finder.refresh(); else stop(); });
  finder.focus();
}
