// Grid toolbar: view, period, zoom, filters, panel.
import { h } from '../dom.js';
import { icon } from '../icons.js';
import { setSession, setUi, state } from '../state.js';
import { isListView, rangeLabel, shownEvent, stepAnchor, VIEWS, ZOOMS } from './range.js';

/** Zoom by hand: this also stops fitting to the screen automatically. */
export function zoomBy(delta) {
  const zoom = Math.min(ZOOMS.length - 1, Math.max(0, state.session.zoom + delta));
  setUi({ zoom, autoFit: false });
}

export function buildToolbar({ month, visible }) {
  const { ui, session } = state;
  const eventOnly = month.areas.reduce((n, a) => n + a.tasks.filter((t) => !t.on_main_rota).length, 0);
  const pressed = (on) => (on ? 'true' : 'false');

  const views = h('div', { class: 'segmented', role: 'group', 'aria-label': 'View' },
    VIEWS.filter((v) => v.id !== 'event' || month.events.length)
      .map((v) => h('button', { 'aria-pressed': pressed(ui.view === v.id), onclick: () => setUi({ view: v.id }) }, v.label)));

  const event = shownEvent(month, session.eventId);
  const eventPicker = ui.view === 'event' && event ? h('select', { class: 'select', 'aria-label': 'Event',
    onchange: (e) => setSession({ eventId: e.target.value }) },
  month.events.map((e) => h('option', { value: e.id, selected: e.id === event.id }, e.name))) : null;

  const nav = ui.view === 'month' || ui.view === 'event' ? eventPicker : h('div', { class: 'group' },
    h('button', { class: 'btn ghost icon-only', 'aria-label': 'Previous', onclick: () => setSession({ anchor: stepAnchor(month.days, ui.view, session.anchor, -1) }) }, icon('left')),
    h('span', { class: 'range-label' }, rangeLabel(visible, ui.view)),
    h('button', { class: 'btn ghost icon-only', 'aria-label': 'Next', onclick: () => setSession({ anchor: stepAnchor(month.days, ui.view, session.anchor, 1) }) }, icon('right')));

  const zoom = isListView(ui.view) ? null : h('div', { class: 'group', role: 'group', 'aria-label': 'Zoom' },
    h('button', { class: 'btn ghost icon-only', 'aria-label': 'Show more (zoom out)', title: 'Zoom out (Ctrl −)', disabled: session.zoom === 0, onclick: () => zoomBy(-1) }, icon('minus')),
    h('span', { class: 'zoom-name', 'aria-live': 'polite' }, ZOOMS[session.zoom].name),
    h('button', { class: 'btn ghost icon-only', 'aria-label': 'Show more detail (zoom in)', title: 'Zoom in (Ctrl +)', disabled: session.zoom === ZOOMS.length - 1, onclick: () => zoomBy(1) }, icon('plus')),
    h('button', { class: 'btn ghost', 'aria-pressed': pressed(ui.autoFit), title: 'Fit the period to the screen, keeping names readable (Ctrl 0)',
      onclick: () => setUi({ autoFit: !ui.autoFit, zoom: session.zoom }) }, icon('fit'), 'Fit'));

  const areaSelect = h('select', { class: 'select', 'aria-label': 'Area', onchange: (e) => setSession({ area: e.target.value }) },
    h('option', { value: '' }, 'All areas'),
    month.areas.map((a) => h('option', { value: a.id, selected: session.area === a.id }, a.name)));

  const filters = h('div', { class: 'group' },
    h('button', { class: 'btn', 'aria-pressed': pressed(session.openOnly), title: 'Only tasks with open shifts',
      onclick: () => setSession({ openOnly: session.openOnly ? false : 'open' }) },
    icon('filter'), session.openOnly === 'wanted' ? 'Highlighted only' : 'Open only'),
    h('button', { class: 'btn', 'aria-pressed': pressed(ui.showTimes), title: 'Show or hide the time and hours of each task',
      onclick: () => setUi({ showTimes: !ui.showTimes }) }, icon('clock'), 'Times'),
    isListView(ui.view) ? null : h('button', { class: 'btn', 'aria-pressed': pressed(ui.showEmpty), title: 'Also show tasks that need nobody on these days',
      onclick: () => setUi({ showEmpty: !ui.showEmpty }) }, 'All tasks'),
    eventOnly && ui.view !== 'event' ? h('button', { class: 'btn', 'aria-pressed': pressed(!ui.hideEventOnly),
      title: 'Tasks of events that are to fill here but not printed on the main rota. Press to hide or show them.',
      onclick: () => setUi({ hideEventOnly: !ui.hideEventOnly }) }, `Not on printed rota · ${eventOnly}`) : null,
    areaSelect);

  return h('div', { class: 'toolbar', role: 'toolbar', 'aria-label': 'Rota display' },
    views, nav, h('span', { class: 'divider' }), zoom, zoom && h('span', { class: 'divider' }), filters,
    h('span', { class: 'spacer' }),
    h('button', { class: 'btn', 'aria-pressed': pressed(ui.panel), title: 'Assistant and people',
      onclick: () => setUi({ panel: !ui.panel }) }, icon('panel'), 'Side panel'));
}
