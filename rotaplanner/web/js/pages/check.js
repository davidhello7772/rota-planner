// Check: everything still needing attention, and how fairly work is shared.
import { api, monthPath } from '../api.js';
import { badge } from '../components.js';
import { fill, formatDate, formatHours, h } from '../dom.js';
import { icon } from '../icons.js';
import { errorText } from '../setup.js';
import { setSession, setUi, state } from '../state.js';
import { toast } from '../ui.js';

const SORTS = {
  ratio: ['Busiest first', (a, b) => ratio(b) - ratio(a)],
  name: ['A – Z', (a, b) => a.name.localeCompare(b.name)],
  weekends: ['Most weekends', (a, b) => b.weekends - a.weekends],
  evenings: ['Most evenings', (a, b) => b.evenings - a.evenings],
};
let loadingFor = '';

const ratio = (p) => p.ratio;  // hours against the target, from the program

/** Open the rota on a shift's day. */
/** Show a shift on the rota: its day, or its event's rota for a task kept there. */
function goTo(item) {
  if (item.event_id) {
    setUi({ view: 'event' });
    setSession({ eventId: item.event_id, anchor: item.date });
  } else {
    setUi({ view: 'day' });
    setSession({ anchor: item.date });
  }
  window.location.hash = '#/build';
}

function itemList(items, empty, render) {
  return items.length ? h('ul', { class: 'check-list' }, items.slice(0, 60).map((item) => h('li', {},
    h('button', { class: 'item', type: 'button', onclick: () => goTo(item) }, render(item)))),
  items.length > 60 ? h('li', { class: 'faint' }, `…and ${items.length - 60} more`) : null)
    : h('p', { class: 'ok-text' }, empty);
}

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/**
 * A count shaded by its share of the person's shifts: where their month is
 * concentrated. Hatched when they are away some of those days (darker: all of
 * them); the tooltip says which.
 */
function shaded(count, total, extra = '', what = '', away = { level: '', text: '' }) {
  const share = total ? count / total : 0;
  const level = !count ? 0 : share >= 0.6 ? 4 : share >= 0.4 ? 3 : share >= 0.25 ? 2 : 1;
  const title = [`${count || 'No'} shift${count === 1 ? '' : 's'} ${what}`, away.text].filter(Boolean).join('\n');
  return h('td', { class: ['num', 'heat', `heat-${level}`, away.level && `away-${away.level}`, extra], title,
    onclick: () => showDetail(title) }, count || '');
}

/** The tooltip's text, also shown under the table on a click (touch screens have no hover). */
function showDetail(text) {
  const box = document.getElementById('balance-detail');
  if (box) { box.textContent = text; box.hidden = !text; }
}

const WEEKDAY_NAMES = ['Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays', 'Sundays'];

/**
 * Hours a week against the target (the bar), the month's hours, then the hours
 * of each week, Monday to Sunday (whole across two months when the other one
 * has a rota). Every figure and sentence comes from the program.
 */
function hoursRows(report, rows) {
  const maxRatio = Math.max(1.5, ...rows.map(ratio));
  const weeks = report.calendar_weeks;
  return h('table', { class: 'table balance-table hours-table' },
    h('thead', {}, h('tr', {},
      h('th', { class: 'col-person' }, 'Person'),
      h('th', { class: 'col-bar' }, 'Hours a week against target'),
      h('th', { class: ['num', 'col-month'] }, 'Month'),
      weeks.map((w, i) => h('th', { class: ['num', 'week-col', i === 0 && 'group-start'], title: w.title },
        i === 0 ? `Week ${w.short}` : w.short)),  // the days only: the full dates are in the title
      h('th', { class: ['num', 'col-shifts'] }, 'Shifts'),
      h('th', { class: ['num', 'col-weekends'] }, 'Weekends'),
      h('th', { class: ['num', 'col-evenings'] }, 'Evenings'),
      report.previous_short.map((short, i) => h('th', { class: ['num', 'col-before'],
        title: `Hours a week on average in ${report.previous_titles[i]}` }, short)))),
    h('tbody', {}, rows.map((p) => {
      const r = ratio(p);
      return h('tr', {},
        h('th', { scope: 'row', class: 'col-person' }, p.name, !p.replied ? h('span', { class: 'faint' }, ' · no reply') : null),
        h('td', {}, h('div', { class: 'balance-bar', title: `${Math.round(r * 100)}% of target` },
          h('span', { class: ['fill', p.load], style: { width: `${Math.min(100, (r / maxRatio) * 100)}%` } }),
          h('span', { class: 'target-mark', style: { left: `${(1 / maxRatio) * 100}%` } }),
          h('span', { class: 'balance-text' }, p.week_text))),
        h('td', { class: 'num', title: p.month_detail }, formatHours(p.hours)),
        p.by_week.map((w, i) => h('td', {
          class: ['num', 'week-cell', `week-${w.level}`, i === 0 && 'group-start'],  // 3: far above the target
          title: w.text, tabindex: w.hours ? '0' : null,
          onclick: () => showDetail(w.text),
          onfocus: () => showDetail(w.text),
        }, w.hours || '')),
        h('td', { class: 'num' }, p.shifts),
        h('td', { class: 'num' }, p.weekends),
        h('td', { class: 'num' }, p.evenings),
        p.previous_week.map((week, i) => h('td', { class: 'num faint',
          title: `${formatHours(p.previous[i])} in ${report.previous_titles[i]}` }, week)));
    })));
}

/** Where each person's shifts fall: which weekdays, which weeks, and what stands out. */
function daysRows(report, rows) {
  return h('table', { class: 'table balance-table days-table' },
    h('thead', {}, h('tr', {},
      h('th', {}, 'Person'), h('th', { class: 'num' }, 'Shifts'),
      WEEKDAYS.map((d, i) => h('th', { class: ['num', i >= 5 && 'weekend', i === 0 && 'group-start'], title: `Shifts on ${d}days` }, d)),
      report.weeks.map((w, i) => h('th', { class: ['num', i === 0 && 'group-start'], title: `Week starting ${formatDate(w.start)}` },
        i === 0 ? `Week ${w.short}` : w.short)),  // the days only: the full dates are in the title
      h('th', { class: ['num', 'group-start'], title: 'Most shifts on a single day' }, 'Most in a day'),
      h('th', { class: 'num', title: 'Longest run of days with a shift' }, 'Days in a row'),
      h('th', {}, 'Worth a look'))),
    h('tbody', {}, rows.map((p) => h('tr', { class: p.notes.length && 'noted' },
      h('th', { scope: 'row' }, h('button', { class: 'link', type: 'button', title: 'See their shifts on the rota',
        onclick: () => showPerson(p.id) }, p.name)),
      h('td', { class: 'num' }, p.shifts || ''),
      p.weekdays.map((count, i) => shaded(count, p.shifts, [i >= 5 && 'weekend', i === 0 && 'group-start'].filter(Boolean).join(' '),
        `on ${WEEKDAY_NAMES[i]}`, p.weekday_away[i])),
      p.weeks.map((count, i) => shaded(count, p.shifts, i === 0 ? 'group-start' : '',
        `in the week of ${formatDate(report.weeks[i].start)}`, p.week_away[i])),
      h('td', { class: ['num', 'group-start', p.most_in_a_day >= 3 && 'warn-text'], title: p.busiest_day ? formatDate(p.busiest_day) : '' }, p.most_in_a_day || ''),
      h('td', { class: ['num', p.days_in_a_row >= 6 && 'warn-text'] }, p.days_in_a_row || ''),
      h('td', { class: 'notes' }, p.notes.join(' · '))))));
}

/** Each person's month day by day: how many tasks each date, which (tooltip), and the days away. */
function calendarRows(report, rows) {
  return h('table', { class: 'table balance-table days-table calendar-table' },
    h('thead', {}, h('tr', {},
      h('th', {}, 'Person'), h('th', { class: 'num' }, 'Shifts'),
      report.dates.map((d) => h('th', { class: ['num', 'bal-cal-day', d.weekend && 'weekend', d.week_start && 'week-start'],
        title: formatDate(d.date) }, h('span', { class: 'wd' }, d.weekday), h('b', {}, d.day))))),
    h('tbody', {}, rows.map((p) => h('tr', {},
      h('th', { scope: 'row' }, h('button', { class: 'link', type: 'button', title: 'See their shifts on the rota',
        onclick: () => showPerson(p.id) }, p.name)),
      h('td', { class: 'num' }, p.shifts || ''),
      p.calendar.map((day, i) => h('td', {
        class: ['num', 'bal-cal-cell', `load-${Math.min(day.count, 3)}`, day.away && `away-${day.away === 'all' ? 'all' : 'some'}`,
          day.clash && 'clash', report.dates[i].weekend && 'weekend', report.dates[i].week_start && 'week-start'],
        title: day.text, tabindex: day.count || day.away ? '0' : null,
        onclick: () => showDetail(day.text),
        onfocus: () => showDetail(day.text),
      }, day.count || ''))))));
}

/** Open the rota with this person highlighted. */
function showPerson(personId) {
  setUi({ view: 'month', panel: true, panelTab: 'people' });
  setSession({ person: personId, eligible: null, paint: false });
  window.location.hash = '#/build';
}

const unevenness = (p) => p.uneven;  // how unevenly the shifts fall, from the program

function balanceTable(report) {
  const view = state.session.checkView || 'hours';
  const days = view === 'days' || view === 'calendar';
  const sort = state.session.checkSort || 'ratio';
  const rows = [...report.balance].sort(days && sort === 'ratio' ? (a, b) => unevenness(b) - unevenness(a) : SORTS[sort][1]);
  const noted = rows.filter((p) => p.notes.length).length;
  return h('section', { class: 'card' },
    h('div', { class: 'card-title' }, h('h2', {}, 'Balance'),
      h('div', { class: 'segmented', role: 'group', 'aria-label': 'What to compare' },
        h('button', { type: 'button', 'aria-pressed': String(view === 'hours'), onclick: () => setSession({ checkView: 'hours' }) }, 'Hours'),
        h('button', { type: 'button', 'aria-pressed': String(view === 'days'), onclick: () => setSession({ checkView: 'days' }) }, 'Days',
          noted ? h('span', { class: 'count' }, noted) : null),
        h('button', { type: 'button', 'aria-pressed': String(view === 'calendar'), onclick: () => setSession({ checkView: 'calendar' }) }, 'Calendar')),
      h('span', { class: 'spacer' }),
      h('select', { class: 'select', 'aria-label': 'Sort', onchange: (e) => setSession({ checkSort: e.target.value }) },
        Object.entries(SORTS).map(([id, [label]]) => h('option', { value: id, selected: id === sort },
          days && id === 'ratio' ? 'Most uneven first' : label)))),
    view === 'calendar'
      ? h('p', { class: 'muted' }, 'Each person’s month day by day: the number of tasks each date (hover or click for which). Hatched: away that day, part of it or all of it. Red outline: a task while they said they are away.')
      : days
      ? h('p', { class: 'muted' }, 'Where each person’s shifts fall. A darker box holds a larger share of their month: one dark weekday means always the same day; one dark week means everything at once. Hatched: away on some of those days (darker: away all day, every one of them); hover or click to see which.')
      : h('p', { class: 'muted' }, report.balance_text),
    h('p', { class: 'balance-detail', id: 'balance-detail', role: 'status', hidden: true }),
    h('div', { class: 'table-scroll', dataset: { scrollId: 'balance' } },
      view === 'calendar' ? calendarRows(report, rows) : days ? daysRows(report, rows) : hoursRows(report, rows)));
}

export function mountCheck(container) {
  const root = h('div', { class: 'page setup-page' });
  container.replaceChildren(root);

  function render() {
    const month = state.month;
    if (!month) {
      fill(root, h('p', { class: 'empty' }, 'Create the month first, in Set up.'));
      return;
    }
    // Recompute whenever the rota changed.
    const signature = month;  // a new month object after every change
    const report = state.session.check;
    if (!report || report.signature !== signature) {
      if (loadingFor !== signature) {
        loadingFor = signature;
        api.get(monthPath(month.id, '/check'))
          .then((result) => setSession({ check: { ...result, signature } }))
          .catch((error) => toast(errorText(error), { tone: 'bad' }));
      }
      if (!report) return;
    }
    const critical = report.open.filter((o) => o.priority === 'critical');
    fill(root,
      h('header', { class: 'page-head' },
        h('div', {}, h('h1', {}, `Check ${report.title}`),
          h('p', { class: 'muted' }, 'Everything left to look at before handing out the rota. Click a line to see that shift.')),
        h('div', { class: 'page-actions' }, h('a', { class: 'btn', href: '#/build' }, icon('calendar'), 'Back to the rota'))),
      h('div', { class: 'tiles setup-tiles check-tiles' },
        h('div', { class: ['tile', report.to_fix.length ? 'bad' : 'ok'] }, h('span', { class: 'tile-value' }, report.to_fix.length), h('span', { class: 'tile-label' }, 'to fix')),
        h('div', { class: ['tile', critical.length ? 'bad' : report.open.length ? 'warn' : 'ok'] }, h('span', { class: 'tile-value' }, report.open.length),
          h('span', { class: 'tile-label' }, `open shifts${critical.length ? `, ${critical.length} essential` : ''}`)),
        h('div', { class: ['tile', report.overrides.length ? 'forced' : ''] }, h('span', { class: 'tile-value' }, report.overrides.length), h('span', { class: 'tile-label' }, 'assigned anyway'))),
      h('div', { class: 'cards check-cards' },
        h('section', { class: 'card' }, h('h2', {}, 'To fix'),
          itemList(report.to_fix, 'Nothing breaks a rule.', (i) => [h('strong', {}, `${formatDate(i.date)} · ${i.task}`),
            h('span', {}, i.person), h('span', { class: 'bad-text' }, i.findings.join(' · '))])),
        h('section', { class: 'card' }, h('h2', {}, 'Open shifts'),
          itemList(report.open, 'Every shift is filled.', (i) => [h('strong', {}, `${formatDate(i.date)} · ${i.task}`),
            i.priority === 'critical' ? badge('Essential', 'bad') : i.priority === 'optional' ? badge('Optional') : null,
            h('span', { class: 'muted' }, i.time)])),
        h('section', { class: 'card' }, h('h2', {}, 'Assigned anyway'),
          itemList(report.overrides, 'Nobody assigned against a rule.', (i) => [h('strong', {}, `${formatDate(i.date)} · ${i.task}`),
            h('span', {}, i.person), h('span', { class: 'muted' }, i.reason ? `“${i.reason}”` : 'no reason given'),
            h('span', { class: 'faint' }, i.findings.join(' · '))])),
        report.names_to_come.length ? h('section', { class: 'card' }, h('h2', {}, 'Names to come'),
          h('p', { class: 'faint' }, 'Volunteers and guests placed before their name was known. Click to add the name.'),
          itemList(report.names_to_come, '', (i) => [h('strong', {}, `${formatDate(i.date)} · ${i.task}`),
            h('span', {}, i.person), h('span', { class: 'muted' }, i.time)])) : null),
      balanceTable(report));
  }

  return { render };
}
