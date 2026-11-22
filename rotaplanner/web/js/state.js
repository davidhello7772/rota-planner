// One central state object. Screens read it and re-render when it changes.
// `ui` holds display preferences remembered between sessions (browser
// storage, per computer); `session` holds what only matters right now.

const PREFS_KEY = 'rota-planner-ui';
const DEFAULT_UI = {
  view: 'month',          // month | 2weeks | week | day | event
  autoFit: true,          // pick the zoom that fits the screen, whenever the space changes
  zoom: 2,                // zoom chosen by hand (0 Overview … 4 Detail), used when autoFit is off
  panel: true,
  panelTab: 'assistant',  // assistant | people
  rail: false,
  size: 100,              // interface size, percent
  showEmpty: false,
  showTimes: true,        // times and hours under each task name
  hideEventOnly: false,   // hide the tasks events keep off the printed main rota (always shown in the Event view)
  eventOwnOnly: false,    // Event view: only the event's own tasks
  collapsed: {},          // area id -> true
  helpSeen: {},           // screen -> true once its first-visit tip was dismissed
};

function loadUi() {
  try {
    return { ...DEFAULT_UI, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') };
  } catch {
    return { ...DEFAULT_UI };
  }
}

export const state = {
  boot: null,
  month: null,
  route: 'build',
  ui: loadUi(),
  session: {
    anchor: null,     // ISO date the week/day views are centred on
    openOnly: false,  // false | 'open' | 'wanted' (highlighted open shifts)
    area: '',         // area filter
    person: null,     // highlighted person id
    eligible: null,   // { "task|date|pos": "best" | "warn" } for that person
    paint: false,
    focus: false,
    zoom: 2,          // zoom level actually on screen
    eventId: null,    // event shown in the Event view
    otherMonths: {},  // views of other months, for an event running into them
    looking: null,    // 'prefill' | 'rebalance' | 'beyond' while the program looks for suggestions
    peopleQuery: '',
    peopleSort: 'load',
    setup: null,      // people, tasks, regular week… for the set-up screens
    directory: { query: '', filter: 'active', view: 'list' },  // People screen
    showArchived: false,  // Tasks & Areas screen
    tasksTab: 'tasks',    // Tasks & Areas: 'tasks' (usual tasks) | 'types' (event types)
    settingsFocus: null,  // Settings: the part to bring into view ('message', 'mail', 'sync', 'backup')
    config: null,     // areas and tasks of the month (or typical month) being configured
    monthSetup: null, // Set up screen: days, events, closures, hours to cover
    newMonth: null,   // which month can be created next, and from what
    availability: null,
    availabilityQuery: '',
    availabilityFilter: 'all',  // all | silent | comment | away
    shortages: null,  // { signature, items } for the Assistant panel
    check: null,      // the Check screen's report
    exportsInfo: null,  // can PDFs be saved directly?
    versions: null,     // versions handed out and changes since (Publish screen)
    schedules: null,    // everyone's schedule message, for the month on screen
    checkSort: 'ratio',
    checkView: 'hours',  // Check › Balance: 'hours' or 'days'
    pen: null,        // who edits, as the program said it (sync.js): read_only, text, how
  },
};

/** Another computer edits: everything can be seen, nothing changed (decided by the program, sync.py). */
export const readOnly = () => Boolean(state.session.pen?.read_only);

const listeners = new Set();
let queued = false;

// Changes made together are rendered once, right after the current task.
// (Not tied to animation frames, which stop while the tab is hidden.)
function emit() {
  if (queued) return;
  queued = true;
  queueMicrotask(() => {
    queued = false;
    for (const listener of listeners) listener(state);
  });
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setUi(patch) {
  Object.assign(state.ui, patch);
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(state.ui)); } catch { /* not persisted */ }
  emit();
}

export function setSession(patch) {
  Object.assign(state.session, patch);
  emit();
}

export function setMonth(month) {
  // Other months were loaded for an event of the previous one: start afresh.
  if (month?.id !== state.month?.id) state.session.otherMonths = {};
  state.month = month;
  emit();
}

export function setRoute(route) {
  state.route = route;
  emit();
}

/** People of a month view (the month on screen by default), by id. */
export const peopleById = (month = state.month) => new Map((month?.people || []).map((p) => [p.id, p]));

/** The month view holding a date: the month on screen, or another one loaded for an event. */
export function monthViewFor(date) {
  const id = date.slice(0, 7);
  return state.month?.id === id ? state.month : state.session.otherMonths[id] || null;
}
