// Which days are on screen for each view, and the zoom levels.

export const VIEWS = [
  { id: 'month', label: 'Month' },
  { id: '2weeks', label: '2 weeks' },
  { id: 'week', label: 'Week' },
  { id: 'day', label: 'Day' },
  { id: 'event', label: 'Event' },  // offered only when the month has events
];

/** Views drawn as vertical lists by area, without the grid's zoom (the Day view). */
export const isListView = (view) => view === 'day';

/** The event shown in the Event view: the chosen one, else the first big one, else the first. */
export function shownEvent(month, eventId) {
  return month.events.find((e) => e.id === eventId) || month.events.find((e) => e.big) || month.events[0] || null;
}

const pad = (n) => String(n).padStart(2, '0');
const isoOf = (day) => `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;

/** The date `days` days after (or before) an ISO date. */
export function addDays(iso, days) {
  const day = new Date(`${iso}T12:00:00`);
  day.setDate(day.getDate() + days);
  return isoOf(day);
}

/** Every date from `start` to `end` (ISO strings), across months if needed. */
export function datesBetween(start, end) {
  const dates = [];
  for (const day = new Date(`${start}T12:00:00`); ; day.setDate(day.getDate() + 1)) {
    const iso = isoOf(day);
    if (iso > end) return dates;
    dates.push(iso);
  }
}

/** The months ("2026-12") an item from `start` to `end` touches, in order. */
export const monthsSpanned = (start, end) => [...new Set(datesBetween(start, end).map((d) => d.slice(0, 7)))];

const MONTH_TITLE = new Intl.DateTimeFormat('en-GB', { month: 'long', year: 'numeric' });
export const monthTitle = (monthId) => MONTH_TITLE.format(new Date(`${monthId}-01T12:00:00`));

const SPAN = { week: 7, '2weeks': 14, day: 1 };

const mondayOffset = (iso) => (new Date(`${iso}T12:00:00`).getDay() + 6) % 7;

export function visibleDays(days, view, anchor, event = null) {
  if (view === 'event' && event) return days.filter((d) => event.start <= d.date && d.date <= event.end);
  if (view === 'month' || view === 'event' || !days.length) return days;
  const index = Math.max(0, days.findIndex((d) => d.date === anchor));
  if (view === 'day') return [days[index]];
  const start = index - mondayOffset(days[index].date);
  return days.slice(Math.max(0, start), Math.max(0, start + SPAN[view]));
}

/** Move the anchor one view-length forward (+1) or back (-1), within the month. */
export function stepAnchor(days, view, anchor, direction) {
  const index = Math.max(0, days.findIndex((d) => d.date === anchor));
  let target = index + direction * (SPAN[view] || 0);
  if (view !== 'day') target -= mondayOffset(days[index].date);
  target = Math.min(days.length - 1, Math.max(0, target));
  return days[target].date;
}

const SHORT = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' });
const LONG = new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long' });

export function rangeLabel(visible, view) {
  if (!visible.length) return '';
  const first = new Date(`${visible[0].date}T12:00:00`);
  if (view === 'day') return LONG.format(first);
  if (visible.length === 1) return LONG.format(first);
  const last = new Date(`${visible[visible.length - 1].date}T12:00:00`);
  return `${SHORT.format(first)} – ${SHORT.format(last)}`;
}

// Zoom levels. Sizes are in rem so that the "Interface size" setting scales
// them; the grid reads them through CSS variables.
// `col` is the narrowest a day column may be at that level.
export const ZOOMS = [
  { name: 'Overview', col: 1.25, label: 10.5, chip: 0.375, font: 0.625 },
  { name: 'Compact', col: 1.75, label: 10.5, chip: 1.125, font: 0.65625 },
  { name: 'Standard', col: 4.25, label: 11.5, chip: 1.25, font: 0.6875 },
  { name: 'Comfortable', col: 6.75, label: 15, chip: 1.375, font: 0.75 },
  { name: 'Detail', col: 8.5, label: 15, chip: 2.125, font: 0.75 },
];

const remPx = () => parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;

/** Width in pixels the grid needs at `level` (one pixel of border per column). */
const naturalWidth = (level, dayCount) => (ZOOMS[level].label + dayCount * ZOOMS[level].col) * remPx() + dayCount;

/**
 * Size the grid for a zoom level. When there is spare room, the day columns
 * widen to use the full width rather than leaving an empty band.
 */
export function applyZoom(table, level, dayCount, availablePx) {
  const zoom = ZOOMS[level];
  const spare = Math.max(0, availablePx - naturalWidth(level, dayCount) - 2);
  table.dataset.zoom = String(level);
  table.style.setProperty('--days', String(dayCount));
  table.style.setProperty('--col-w', `${Math.floor(zoom.col * remPx() + spare / dayCount)}px`);
  table.style.setProperty('--label-w', `${zoom.label}rem`);
  table.style.setProperty('--chip-h', `${zoom.chip}rem`);
  table.style.setProperty('--chip-fs', `${zoom.font}rem`);
}

/** "Fit" never goes below this level, so names ("Quinn J.") stay readable;
    smaller levels (initials, bars) are only chosen by hand. */
export const MIN_FIT_ZOOM = 2;

/**
 * Largest zoom level at which `dayCount` columns fit in `widthPx`, but never
 * below MIN_FIT_ZOOM: when even that is too wide, the grid scrolls sideways.
 */
export function fittingZoom(widthPx, dayCount) {
  for (let level = ZOOMS.length - 1; level > MIN_FIT_ZOOM; level -= 1) {
    if (naturalWidth(level, dayCount) <= widthPx) return level;
  }
  return MIN_FIT_ZOOM;
}
