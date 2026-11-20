// Side panel: the Assistant (where the rota stands, hints) and People
// (everyone's hours, highlight someone, paint their shifts).
import { acceptProposals, discardProposals, loadEligible, prefill, rebalance } from '../actions.js';
import { api, monthPath } from '../api.js';
import { formatDate, formatHours, h } from '../dom.js';
import { icon } from '../icons.js';
import { setSession, setUi, state } from '../state.js';
import { hint } from '../help.js';
import { copySchedule } from '../schedules.js';
import { openMenu } from '../ui.js';
import { datesBetween, monthTitle, shownEvent, visibleDays } from './range.js';
import { changes } from '../components.js';

const TIP_ICONS = { bad: 'alert', warn: 'alert', info: 'info' };
const SORTS = {
  load: { label: 'Least busy first', compare: (a, b) => ratio(a) - ratio(b) || a.name.localeCompare(b.name) },
  name: { label: 'A – Z', compare: (a, b) => a.name.localeCompare(b.name) },
  busy: { label: 'Busiest first', compare: (a, b) => ratio(b) - ratio(a) || a.name.localeCompare(b.name) },
};

function ratio(person) {
  return person.target > 0 ? person.hours / person.target : 0;
}

function loadBar(person) {
  const r = ratio(person);
  return h('span', { class: ['load', r > 1 && 'over', r < 0.5 && 'under'] },
    h('span', { style: { width: `${Math.min(100, Math.round(r * 100))}%` } }));
}

function tile(value, label, tone, onclick) {
  return h(onclick ? 'button' : 'div', { class: ['tile', tone], onclick },
    h('span', { class: 'tile-value' }, value), h('span', { class: 'tile-label' }, label));
}

function openByArea(month) {
  return month.areas.map((area) => {
    let open = 0;
    for (const task of area.tasks) {
      for (const day of month.days) {
        const cell = month.cells[`${task.id}|${day.date}`];
        if (cell) open += cell.slots.filter((s, i) => s.open && i < cell.required).length;
      }
    }
    return { area, open };
  }).filter((row) => row.open > 0).sort((a, b) => b.open - a.open);
}

function assistant(month) {
  const s = month.summary;
  // "Open shifts on an event's own rota": only worth saying while those tasks are hidden here.
  const tips = month.tips.filter((t) => !t.event || state.ui.hideEventOnly);
  const coverage = Math.round(s.coverage * 100);
  const showOpen = (areaId = '', filter = 'open') => setSession({ openOnly: filter, area: areaId });
  return [
    h('section', {},
      h('h3', {}, 'Where the rota stands'),
      h('div', { class: 'tiles' },
        h('div', { class: ['tile', coverage >= 100 ? 'ok' : ''] },
          h('span', { class: 'tile-value' }, `${coverage}%`),
          h('span', { class: 'tile-label' }, `${s.filled} of ${s.required} shifts filled`),
          h('span', { class: 'meter' }, h('span', { style: { width: `${coverage}%` } }))),
        tile(String(s.open), 'open shifts', s.open ? 'warn' : 'ok', s.open ? () => showOpen() : null),
        tile(String(s.wanted), 'highlighted to fill', s.wanted ? 'wanted' : '', s.wanted ? () => showOpen('', 'wanted') : null),
        tile(String(s.conflicts), 'to fix', s.conflicts ? 'bad' : 'ok'),
        tile(String(s.forced), 'assigned anyway', s.forced ? 'forced' : ''),
        tile(String(s.guests), 'not on the list', ''))),
    month.proposals.length ? proposalsBox(month) : h('section', { class: 'actions' },
      h('h3', {}, 'Suggestions', hint('prefill')),
      changes(h('button', { class: 'btn', type: 'button', title: 'Suggest someone for open shifts',
        onclick: (e) => prefillMenu(e.currentTarget, month) }, icon('wand'), 'Prefill the rota…')),
      changes(h('button', { class: 'btn', type: 'button', title: 'Suggest moves from the busiest people to the least busy',
        onclick: () => rebalance() }, icon('scale'), 'Rebalance hours'))),
    otherMonthsProposals(),
    shortagesBox(month),
    tips.length ? h('section', {},
      h('h3', {}, 'Hints'),
      h('ul', { class: 'tips' }, tips.map((t) => h('li', { class: ['tip', t.tone] }, icon(TIP_ICONS[t.tone] || 'info'),
        h('span', {}, t.text, t.event ? [' ', h('button', { class: 'link', type: 'button',
          onclick: () => { setUi({ view: 'event' }); setSession({ eventId: t.event }); } }, 'Open it')] : null))))) : null,
    h('section', {},
      h('h3', {}, 'Open shifts by area'),
      h('ul', { class: 'bars' }, openByArea(month).map(({ area, open }) =>
        h('li', {}, h('button', { class: 'bar-row', onclick: () => showOpen(area.id) },
          h('span', { class: 'name' }, area.name), h('span', {}), h('span', { class: 'num' }, String(open))))))),
  ];
}

// ---- Suggestions: prefill and rebalance ---------------------------------

/** Dates on screen; for an event, all its days in months that exist (it may run into the next month). */
function datesOnScreen(month) {
  const { session, ui } = state;
  const event = ui.view === 'event' ? shownEvent(month, session.eventId) : null;
  if (!event) return visibleDays(month.days, ui.view, session.anchor).map((d) => d.date);
  const existing = new Set([month.id, ...(state.boot?.months || []).map((m) => m.id)]);
  return datesBetween(event.start, event.end).filter((d) => existing.has(d.slice(0, 7)));
}

function prefillMenu(anchor, month) {
  const { session, ui } = state;
  const dates = datesOnScreen(month);
  const area = month.areas.find((a) => a.id === session.area);
  const items = [
    { value: 'month', label: 'Every open shift of the month' },
    dates.length < month.days.length || dates.some((d) => !d.startsWith(month.id))
      ? { value: 'visible', label: ui.view === 'event' ? `Only this event (${dates.length} days)` : `Only the days on screen (${dates.length})` } : null,
    area ? { value: 'area', label: `Only ${area.name}` } : null,
  ].filter(Boolean);
  openMenu(anchor, items, (choice) => {
    if (choice === 'visible') prefill({ dates });
    else if (choice === 'area') prefill({ area_id: area.id });
    else prefill();
  });
}

function proposalsBox(month) {
  const people = new Map(month.people.map((p) => [p.id, p]));
  const tasks = new Map(month.areas.flatMap((a) => a.tasks).map((t) => [t.id, t]));
  const moves = month.proposals.filter((p) => p.replaces);
  const fills = month.proposals.length - moves.length;
  return h('section', { class: 'proposals-box' },
    h('h3', {}, moves.length ? 'Suggested moves' : 'Suggestions'),
    h('p', {}, moves.length
      ? `${moves.length} moves would even out hours. Accept them one by one, or all at once.`
      : `${fills} open shifts have a suggestion, shown dashed on the rota. Click one to choose someone else.`),
    moves.length ? h('ul', { class: 'move-list' }, moves.map((m) => h('li', {},
      h('div', {}, h('strong', {}, `${tasks.get(m.task_id)?.name || ''}, ${formatDate(m.date)}`),
        h('span', { class: 'muted' }, `${people.get(m.replaces)?.name} → ${people.get(m.person_id)?.name}`),
        h('span', { class: 'faint' }, m.why)),
      changes(h('button', { class: 'btn small', type: 'button', onclick: () => acceptProposals([m.key]) }, 'Accept'))))) : null,
    changes(h('div', { class: 'proposal-actions' },
      h('button', { class: 'btn primary', type: 'button', onclick: () => acceptProposals() }, icon('check'), moves.length ? 'Accept all moves' : `Accept the ${fills} suggestions`),
      h('button', { class: 'btn ghost', type: 'button', onclick: () => discardProposals() }, 'Discard'))));
}

/** Suggestions waiting in the other month of an event shown in the Event view. */
function otherMonthsProposals() {
  if (state.ui.view !== 'event') return null;
  const months = Object.values(state.session.otherMonths).filter((m) => m.proposals.length);
  if (!months.length) return null;
  return h('section', { class: 'proposals-box' },
    h('h3', {}, "Suggestions in the event's other month"),
    months.map((m) => changes(h('div', { class: 'proposal-actions' },
      h('span', {}, `${monthTitle(m.id)}: ${m.proposals.length} suggestion${m.proposals.length > 1 ? 's' : ''}`),
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn small primary', type: 'button', onclick: () => acceptProposals(null, m.id) }, icon('check'), 'Accept'),
      h('button', { class: 'btn small ghost', type: 'button', onclick: () => discardProposals(null, m.id) }, 'Discard')))));
}

// ---- Shortages: days with more open shifts than people who could take them -

let shortagesFor = null;  // the month (as it was) the shortages were computed for

function shortagesBox(month) {
  const signature = month;  // a new month object after every change
  if (signature !== shortagesFor) {
    shortagesFor = signature;
    api.get(monthPath(month.id, '/shortages'))
      .then((result) => setSession({ shortages: { signature, items: result.items } }))
      .catch(() => { shortagesFor = null; });  // try again next time
  }
  const found = state.session.shortages;
  if (!found || found.signature !== signature || !found.items.length) return null;
  return h('section', {},
    h('h3', {}, 'Not enough people'),
    h('ul', { class: 'tips' }, found.items.map((item) => h('li', { class: ['tip', 'warn'] }, icon('alert'),
      item.date
        ? h('button', { class: 'link', type: 'button', onclick: () => { setUi({ view: 'day' }); setSession({ anchor: item.date }); } }, item.text)
        : h('span', {}, item.text)))));
}

async function selectPerson(personId) {
  if (state.session.person === personId) {
    setSession({ person: null, eligible: null, paint: false });
    return;
  }
  setSession({ person: personId, eligible: null, paint: false });
  const eligible = await loadEligible(personId);
  if (state.session.person === personId) setSession({ eligible });  // still the one highlighted
}

function personCard(person) {
  const away = Math.round((1 - person.presence) * 100);
  return h('section', { class: 'person-card' },
    h('div', { class: 'who' }, h('strong', {}, person.full), h('span', { class: 'muted' }, `${formatHours(person.hours)} / ${formatHours(person.target)}`)),
    loadBar(person),
    h('span', { class: 'muted' }, [
      person.replied ? (away ? `Away ${away}% of the month` : 'Available all month') : 'No availability reply',
      person.resident ? 'Resident' : 'Non-resident',
    ].join(' · ')),
    person.note ? h('span', {}, person.note) : null,
    person.skills.length ? h('div', { class: 'chips-inline' }, person.skills.map((s) => h('span', {}, s))) : null,
    h('div', { class: 'person-actions' },
      changes(h('button', { class: 'btn', 'aria-pressed': state.session.paint ? 'true' : 'false',
        title: 'Then click open shifts to give them to this person',
        onclick: () => setSession({ paint: !state.session.paint }) }, icon('brush'), state.session.paint ? 'Painting…' : 'Paint shifts')),
      h('button', { class: 'btn', title: 'Their schedule, ready to paste in an e-mail', onclick: () => copySchedule(person.id) }, icon('message'), 'Copy schedule'),
      h('button', { class: 'btn ghost', onclick: () => selectPerson(person.id) }, 'Clear')));
}

function peopleTab(month) {
  const { session } = state;
  const text = session.peopleQuery.trim().toLowerCase();
  const people = month.people
    .filter((p) => !text || p.name.toLowerCase().includes(text) || p.full.toLowerCase().includes(text))
    .sort(SORTS[session.peopleSort].compare);
  const active = people.filter((p) => p.active);
  const inactive = people.filter((p) => !p.active);
  const selected = month.people.find((p) => p.id === session.person);

  const row = (person) => h('li', {}, h('button', {
    class: ['bar-row', !person.active && 'inactive'], 'aria-pressed': person.id === session.person ? 'true' : 'false',
    onclick: () => selectPerson(person.id), title: person.full,
  }, h('span', { class: 'name' }, person.name), loadBar(person), h('span', { class: 'num' }, formatHours(person.hours).replace(' h', ''))));

  const search = h('input', { class: 'input', type: 'search', placeholder: 'Find someone', value: session.peopleQuery, 'aria-label': 'Find someone',
    dataset: { focusId: 'people-search' },
    oninput: (e) => { state.session.peopleQuery = e.target.value; setSession({}); } });

  return [
    selected ? personCard(selected) : h('p', { class: 'faint' }, 'Click a name to see their shifts on the rota and where they could still help.'),
    h('div', { class: 'list-tools' },
      h('label', { class: 'search' }, icon('search'), search),
      h('select', { class: 'select', 'aria-label': 'Sort', onchange: (e) => setSession({ peopleSort: e.target.value }) },
        Object.entries(SORTS).map(([id, s]) => h('option', { value: id, selected: id === session.peopleSort }, s.label)))),
    h('section', {}, h('h3', {}, `Active · ${active.length}`), h('ul', { class: 'bars' }, active.map(row))),
    inactive.length ? h('section', {}, h('h3', {}, `Not on the rota · ${inactive.length}`), h('ul', { class: 'bars' }, inactive.map(row))) : null,
  ];
}

export function buildPanel(month) {
  const tab = state.ui.panelTab;
  const tabs = h('div', { class: 'panel-tabs' }, h('div', { class: 'segmented', role: 'tablist' },
    [['assistant', 'Assistant'], ['people', 'People']].map(([id, label]) => h('button', {
      role: 'tab', 'aria-selected': String(tab === id), 'aria-pressed': tab === id ? 'true' : 'false',
      onclick: () => setUi({ panelTab: id }),
    }, label))));
  return h('aside', { class: 'panel', 'aria-label': 'Assistant and people' },
    tabs, h('div', { class: 'panel-body', dataset: { scrollId: 'panel' } }, tab === 'people' ? peopleTab(month) : assistant(month)));
}

export { selectPerson };
