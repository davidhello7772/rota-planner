// The Build screen: toolbar, month strip, grid (or day view), side panel.
import { assign, clearSlot, createMonth, loadOtherMonths, reportError, setHeadcount } from '../actions.js';
import { openMenu, toast } from '../ui.js';
import { h, replaceKeepingFocus } from '../dom.js';
import { monthViewFor, peopleById, setSession, setUi, state } from '../state.js';
import { buildDayView } from './dayview.js';
import { buildGrid, onGridKey } from './grid.js';
import { buildPanel } from './panel.js';
import { closePicker, openPicker } from './picker.js';
import { buildEventView } from './eventview.js';
import { fittingZoom, monthsSpanned, monthTitle, shownEvent, visibleDays } from './range.js';
import { buildStrip } from './strip.js';
import { openEvent } from '../pages/monthsetup.js';
import { buildToolbar, zoomBy } from './toolbar.js';
import { attachTooltips, hideTooltip } from './tooltip.js';

const WHEEL_STEP = 80; // accumulated wheel delta per zoom step (smooths trackpads)

function defaultAnchor(month) {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  if (month.days.some((d) => d.date === today)) return today;
  return month.days[0]?.date;
}

async function paint(chip) {
  const { person } = state.session;
  if (chip.dataset.person === person && !chip.classList.contains('proposed')) {
    await clearSlot(chip.dataset.key);
  } else {
    await assign(chip.dataset.key, { personId: person });
  }
}

/** A shift switched off for one day: offer to bring it back. */
function offerRestore(chip) {
  const [taskId, date] = chip.dataset.off.split('|');
  const usual = monthViewFor(date)?.cells[chip.dataset.off]?.usual || 1;
  openMenu(chip, [{ value: 'restore', label: `Needed after all (${usual} ${usual === 1 ? 'person' : 'people'})` }],
    () => setHeadcount(taskId, date, null));
}

/** The event's days month by month: views already loaded, months to load, months not created yet. */
function eventParts(event) {
  const existing = new Set((state.boot?.months || []).map((m) => m.id));
  return monthsSpanned(event.start, event.end).map((id) => {
    const view = id === state.month.id ? state.month : state.session.otherMonths[id] || null;
    return {
      id, view, exists: Boolean(view) || existing.has(id),
      days: view ? view.days.filter((d) => event.start <= d.date && d.date <= event.end) : [],
      people: view ? peopleById(view) : null,
    };
  });
}

/** Create a month an event runs into, from the month on screen. */
async function createForEvent(monthId) {
  try {
    const result = await createMonth(monthId, `month:${state.month.id}`);
    await loadOtherMonths([monthId]);
    toast(`${monthTitle(monthId)} created · ${result.placed} shifts filled from the regular week`);
  } catch (error) {
    reportError(error);
  }
}

function scrollToDay(scroller, date) {
  const head = scroller.querySelector(`.day-head[data-date="${date}"]`);
  const label = scroller.querySelector('.corner');
  if (head && label) scroller.scrollTo({ left: Math.max(0, head.offsetLeft - label.offsetWidth - 40), behavior: 'smooth' });
}

export function mountBuild(container) {
  const toolbarSlot = h('div', { class: 'slot' });
  const paintSlot = h('div', { class: 'slot' });
  const stripSlot = h('div', { class: 'slot' });
  const panelSlot = h('div', { class: 'slot' });
  const scroller = h('div', { class: 'grid-scroll', 'aria-label': 'Rota' });
  container.replaceChildren(h('div', { class: 'build' },
    toolbarSlot, paintSlot, stripSlot, h('div', { class: 'build-body' }, scroller, panelSlot)));

  scroller.addEventListener('click', (event) => {
    const eventTasks = event.target.closest('[data-event-tasks]');
    if (eventTasks) {
      openEvent(eventTasks.dataset.eventTasks, { newTask: Boolean(eventTasks.dataset.newTask) });
      return;
    }
    const pill = event.target.closest('.pill[data-event]');
    if (pill) {
      setUi({ view: 'event' });
      setSession({ eventId: pill.dataset.event });
      return;
    }
    const chip = event.target.closest('.chip');
    if (chip?.dataset.off) {
      offerRestore(chip);
      return;
    }
    if (chip?.dataset.add) {
      const [taskId, date] = chip.dataset.add.split('|');
      openMenu(chip, [{ value: 1, label: 'Needed this day: 1 person' }, { value: 2, label: 'Needed this day: 2 people' }],
        (count) => setHeadcount(taskId, date, count));
      return;
    }
    if (chip) {
      if (state.session.paint && state.session.person) paint(chip);
      else openPicker(chip, chip.dataset.key);
      return;
    }
    const head = event.target.closest('.day-head');
    if (head) {
      setUi({ view: 'day' });
      setSession({ anchor: head.dataset.date });
      return;
    }
    const area = event.target.closest('.area-head[data-area]');
    if (area) setUi({ collapsed: { ...state.ui.collapsed, [area.dataset.area]: !state.ui.collapsed[area.dataset.area] } });
  });
  scroller.addEventListener('keydown', (event) => {
    const table = scroller.querySelector('.grid');
    if (table) onGridKey(event, table);
  });
  scroller.addEventListener('focusin', (event) => {
    const chip = event.target.closest?.('.chip');
    if (!chip) return;
    scroller.querySelectorAll('.chip[tabindex="0"]').forEach((c) => { if (c !== chip) c.tabIndex = -1; });
    chip.tabIndex = 0;
  });
  let wheel = 0;
  scroller.addEventListener('wheel', (event) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    wheel += event.deltaY;
    if (Math.abs(wheel) >= WHEEL_STEP) {
      zoomBy(wheel < 0 ? 1 : -1);
      wheel = 0;
    }
  }, { passive: false });
  scroller.addEventListener('scroll', () => { closePicker(); hideTooltip(); });
  attachTooltips(scroller);

  function render() {
    const { month, ui, session } = state;
    if (!session.anchor || !month.days.some((d) => d.date === session.anchor)) session.anchor = defaultAnchor(month);
    if (ui.view === 'event' && !month.events.length) ui.view = 'month';  // nothing to show: the month instead
    const view = ui.view;
    const event = view === 'event' ? shownEvent(month, session.eventId) : null;
    const visible = visibleDays(month.days, view, session.anchor, event);
    const people = peopleById();

    // The panel goes first: its width decides how much room the grid has.
    // With "Fit" on, the zoom follows the free width; otherwise it is the
    // level last chosen by hand.
    replaceKeepingFocus(panelSlot, ui.panel ? buildPanel(month) : '');
    session.zoom = ui.autoFit ? fittingZoom(scroller.clientWidth, visible.length) : ui.zoom;

    replaceKeepingFocus(toolbarSlot, buildToolbar({ month, visible }));
    const painter = session.paint && people.get(session.person);
    paintSlot.replaceChildren(painter ? h('div', { class: 'paintbar', role: 'status' },
      h('strong', {}, `Painting with ${painter.name}`),
      h('span', {}, 'Click shifts to give them to this person; click their own shift to remove them. Esc to stop.'),
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn small', onclick: () => setSession({ paint: false }) }, 'Stop')) : '');
    stripSlot.replaceChildren(buildStrip(month, visible, (date) => {
      if (ui.view === 'month') scrollToDay(scroller, date);
      else setSession({ anchor: date });
    }));
    let content;
    if (view === 'event') {
      const parts = eventParts(event);
      const toLoad = parts.filter((p) => p.exists && !p.view).map((p) => p.id);
      if (toLoad.length) loadOtherMonths(toLoad);
      content = buildEventView({ event, parts, currentId: month.id, ui, session, onCreate: createForEvent });
    }
    else if (view === 'day') content = buildDayView({ month, day: visible[0], ui, session, people });
    else content = buildGrid({ month, visible, ui, session, people, width: scroller.clientWidth });
    replaceKeepingFocus(scroller, content);
  }

  return { render };
}
