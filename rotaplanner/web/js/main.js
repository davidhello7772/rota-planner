// Application shell: sidebar, top bar, routing and keyboard shortcuts.
import { redo, undo } from './actions.js';
import { api, ApiError, monthPath } from './api.js';
import { h, isTyping } from './dom.js';
import { icon } from './icons.js';
import { mountBuild } from './build/index.js';
import { closePicker } from './build/picker.js';
import { zoomBy } from './build/toolbar.js';
import { openHelp, renderCoach } from './help.js';
import { pageFor } from './pages.js';
import { openSchedules } from './schedules.js';
import { mountSync } from './sync.js';
import { welcomeScreen } from './syncsetup.js';
import { announceUpdate } from './updates.js';
import { mountAvailability } from './pages/availability.js';
import { mountCheck } from './pages/check.js';
import { mountMonthSetup } from './pages/monthsetup.js';
import { mountPeople } from './pages/people.js';
import { mountPublish } from './pages/publish.js';
import { mountRegular } from './pages/regular.js';
import { mountSettings } from './pages/settings.js';
import { mountTasks } from './pages/tasks.js';
import { loadSetup } from './setup.js';
import { setMonth, setRoute, setSession, setUi, state, subscribe } from './state.js';
import { openMenu, toast } from './ui.js';

const NAV = [
  { route: 'build', label: 'Rota', icon: 'calendar' },
  { route: 'people', label: 'People', icon: 'users' },
  { route: 'tasks', label: 'Tasks & Areas', icon: 'layers' },
  { route: 'regular', label: 'Regular week', icon: 'repeat' },
  { route: 'settings', label: 'Settings', icon: 'sliders' },
];
const STEPS = [
  { route: 'setup', label: 'Set up' },
  { route: 'availability', label: 'Availability' },
  { route: 'build', label: 'Build' },
  { route: 'check', label: 'Check' },
  { route: 'publish', label: 'Publish' },
];
const SIZES = [90, 100, 115, 130];
const NARROW = window.matchMedia('(max-width: 1100px)');

// Screens with their own module. Those listed in NEEDS_SETUP show people,
// tasks or settings, loaded fresh each time the screen is opened.
const SCREENS = {
  setup: mountMonthSetup, availability: mountAvailability, check: mountCheck, publish: mountPublish,
  people: mountPeople, tasks: mountTasks, regular: mountRegular, settings: mountSettings,
};
const NEEDS_SETUP = new Set(['people', 'tasks', 'regular', 'settings']);

const app = document.getElementById('app');
let screen = null;       // the mounted screen ({ render }) for contentRoute
let contentRoute = null;

function routeFromHash() {
  const route = window.location.hash.replace(/^#\/?/, '') || 'build';
  return [...NAV, ...STEPS].some((item) => item.route === route) ? route : 'build';
}

function shell() {
  const content = h('main', { class: 'content', id: 'content' });
  const sidebar = h('aside', { class: 'sidebar', 'aria-label': 'Main' },
    h('div', { class: 'brand' },
      h('span', { class: 'brand-mark', 'aria-hidden': 'true' }, 'R'),
      h('span', { class: 'brand-text' }, h('span', { class: 'brand-name' }, 'Rota Planner'), h('span', { class: 'brand-sub', id: 'centre' }))),
    h('nav', { class: 'nav' }, NAV.map((item) => h('a', { class: 'nav-item', href: `#/${item.route}`, dataset: { route: item.route }, title: item.label },
      icon(item.icon), h('span', { class: 'nav-label' }, item.label)))),
    h('div', { class: 'sidebar-foot' },
      h('button', { class: 'nav-item', id: 'rail-toggle', onclick: () => setUi({ rail: !state.ui.rail }) },
        icon('sidebar'), h('span', { class: 'nav-label' }, 'Collapse'))));
  const topbar = h('header', { class: 'topbar' },
    h('div', { class: 'month-switch' },
      h('button', { class: 'btn ghost icon-only', id: 'prev-month', 'aria-label': 'Previous month', onclick: () => switchMonth(-1) }, icon('left')),
      h('span', { class: 'month-name' },
        h('span', { class: 'month-title', id: 'month-title' }),
        // Where the rota stands: draft, with the manager, published… (from the program)
        h('a', { class: 'phase-line', id: 'phase', href: '#/publish', hidden: true })),
      h('button', { class: 'btn ghost icon-only', id: 'next-month', 'aria-label': 'Next month', onclick: () => switchMonth(1) }, icon('right')),
      h('a', { class: 'btn ghost icon-only', href: '#/setup', 'aria-label': 'New month', title: 'New month' }, icon('plus'))),
    h('nav', { class: 'stepper', 'aria-label': 'Steps' }, STEPS.map((step, i) => h('a', { class: 'step', href: `#/${step.route}`, dataset: { route: step.route } },
      h('span', { class: 'step-num' }, i + 1), h('span', { class: 'step-label' }, step.label)))),
    h('span', { class: 'spacer' }),
    h('span', { id: 'sync-pill', hidden: true }),
    h('button', { class: 'btn ghost', id: 'schedules', title: 'Copy a person’s schedule for an e-mail (S)', onclick: openSchedules },
      icon('message'), h('span', { class: 'topbar-label' }, 'Schedules')),
    h('span', { class: 'divider' }),
    h('button', { class: 'btn ghost icon-only', id: 'undo', 'aria-label': 'Undo', title: 'Undo (Ctrl Z)', onclick: () => undo() }, icon('undo')),
    h('button', { class: 'btn ghost icon-only', id: 'redo', 'aria-label': 'Redo', title: 'Redo (Ctrl Y)', onclick: () => redo() }, icon('redo')),
    h('span', { class: 'divider' }),
    h('button', { class: 'btn ghost', id: 'size', title: 'Interface size', onclick: (e) => openMenu(e.currentTarget,
      SIZES.map((size) => ({ value: size, label: `${size}%`, checked: state.ui.size === size })),
      (size) => setUi({ size })) }, icon('type'), h('span', { id: 'size-label' })),
    h('button', { class: 'btn ghost', 'aria-label': 'Focus', title: 'Focus: give the whole screen to the rota (F)', onclick: toggleFocus },
      icon('maximize'), h('span', { class: 'topbar-label' }, 'Focus')),
    h('button', { class: 'btn ghost icon-only', id: 'help', 'aria-label': 'Help', title: 'Help (?)', onclick: () => openHelp() }, icon('help')));
  const syncBanner = h('div', { class: 'sync-slot', id: 'sync-banner', hidden: true });
  app.replaceChildren(sidebar, h('div', { class: 'main' }, topbar, syncBanner, h('div', { class: 'coach-slot', id: 'coach', hidden: true }), content));
}

function toggleFocus() {
  setSession({ focus: !state.session.focus });
}

async function switchMonth(direction) {
  const months = state.boot.months;
  const index = months.findIndex((m) => m.id === state.month.id) + direction;
  if (index < 0 || index >= months.length) return;
  await loadMonth(months[index].id);
}

async function loadMonth(monthId) {
  setSession({ anchor: null, person: null, eligible: null, paint: false });
  setMonth(await api.get(monthPath(monthId)));
}

function renderShell() {
  const { month, ui, session, route } = state;
  app.classList.toggle('rail', ui.rail || NARROW.matches);
  app.classList.toggle('focus', session.focus);
  document.documentElement.style.setProperty('font-size', `${ui.size}%`);
  document.getElementById('size-label').textContent = `${ui.size}%`;
  document.getElementById('centre').textContent = state.boot?.centre || '';
  document.getElementById('month-title').textContent = month ? month.title : 'No rota yet';
  const phase = document.getElementById('phase');
  phase.hidden = !month?.phase;
  if (month?.phase) {
    phase.textContent = month.phase.short;
    phase.className = ['phase-line', month.phase.tone].filter(Boolean).join(' ');
    phase.title = `${month.phase.text} Click to open Publish.`;
  }
  const months = state.boot?.months || [];
  const index = months.findIndex((m) => m.id === month?.id);
  document.getElementById('prev-month').disabled = index <= 0;
  document.getElementById('next-month').disabled = index < 0 || index >= months.length - 1;
  document.getElementById('undo').disabled = !month?.history.undo;
  document.getElementById('redo').disabled = !month?.history.redo;
  document.getElementById('rail-toggle').querySelector('.nav-label').textContent = ui.rail ? 'Expand' : 'Collapse';
  const navRoute = STEPS.some((s) => s.route === route) ? 'build' : route;
  markCurrent('.nav-item[data-route]', navRoute, 'page');
  markCurrent('.step', route, 'step');
  const coach = document.getElementById('coach');
  renderCoach(coach);
  if (state.boot?.empty) coach.hidden = true;  // the first screen says where to begin: no tip on top
}

function markCurrent(selector, route, value) {
  document.querySelectorAll(selector).forEach((link) => {
    if (link.dataset.route === route) link.setAttribute('aria-current', value);
    else link.removeAttribute('aria-current');
  });
}

/** The module for a route; the routes without one are described by pages.js. */
function mountFor(route) {
  if (route === 'build' && state.month) return mountBuild;
  return SCREENS[route] || null;
}

/** Another computer's changes were written here: show them, without throwing the screen away. */
async function refreshData() {
  if (document.querySelector('.backdrop')) {
    // Something is open (a form, a choice): never under the coordinator's hands.
    toast('Newer data arrived from another computer.', { actionLabel: 'Show it', onAction: refreshData });
    return;
  }
  try {
    const boot = await api.get('/api/bootstrap');
    state.boot = boot;
    const monthId = boot.months.some((m) => m.id === state.month?.id) ? state.month.id : boot.current;
    const month = monthId ? await api.get(monthPath(monthId)) : null;
    screen = null;  // the screens load what they show afresh
    setSession({ setup: null, config: null, monthSetup: null, newMonth: null, availability: null, shortages: null,
      check: null, versions: null, schedules: null, otherMonths: {} });
    if (month) setMonth(month); else setRoute(state.route);
  } catch (error) {
    toast(error.message, { tone: 'bad' });
  }
}

const WELCOMED = 'rota-planner-welcomed';

function renderContent() {
  const content = document.getElementById('content');
  const { route } = state;
  if (state.bootError) return;  // the start-up problem stays on screen
  let welcomed = false;
  try { welcomed = Boolean(sessionStorage.getItem(WELCOMED)); } catch { /* storage may be blocked */ }
  if (state.boot?.empty && !welcomed) {
    // Nothing on this computer yet: where to begin.
    if (contentRoute !== 'welcome') {
      content.replaceChildren(welcomeScreen({ onStartEmpty: () => {
        try { sessionStorage.setItem(WELCOMED, '1'); } catch { /* storage may be blocked */ }
        setSession({});
      } }));
      screen = null;
      contentRoute = 'welcome';
    }
    return;
  }
  const mount = mountFor(route);
  if (!mount) {
    if (contentRoute !== route || screen) content.replaceChildren(pageFor(route, Boolean(state.month)));
    screen = null;
    contentRoute = route;
    return;
  }
  if (contentRoute !== route || !screen) {
    screen = mount(content);
    contentRoute = route;
    if (NEEDS_SETUP.has(route)) {
      loadSetup().catch((error) => toast(error.message, { tone: 'bad' }));  // fresh data on each visit
    }
  }
  screen.render();
}

function onKey(event) {
  const mod = event.ctrlKey || event.metaKey;
  const typing = isTyping(event.target);
  // A dialog, a menu or the tour is on top: its own keys only.
  if (document.querySelector('.backdrop, .tour-layer, .popover')) return;
  if (mod && !typing && event.key.toLowerCase() === 'z') {
    event.preventDefault();
    if (event.shiftKey) redo(); else undo();
  } else if (mod && !typing && event.key.toLowerCase() === 'y') {
    event.preventDefault();
    redo();
  } else if (mod && state.route === 'build' && ['=', '+', '-', '0'].includes(event.key)) {
    event.preventDefault();
    if (event.key === '0') setUi({ autoFit: true });
    else zoomBy(event.key === '-' ? -1 : 1);
  } else if (!mod && !typing && !event.altKey && event.key.toLowerCase() === 'f' && state.route === 'build') {
    toggleFocus();
  } else if (!mod && !typing && !event.altKey && (event.key === '?' || event.key === 'F1')) {
    event.preventDefault();
    openHelp();
  } else if (!mod && !typing && !event.altKey && event.key.toLowerCase() === 's') {
    event.preventDefault();
    openSchedules();
  } else if (event.key === 'Escape') {
    if (state.session.paint) setSession({ paint: false });
    else if (state.session.focus) toggleFocus();
  }
}

async function start() {
  shell();
  // The password screen replaces the shell: nothing renders over it.
  subscribe(() => { if (document.getElementById('content')) { renderShell(); renderContent(); } });
  window.addEventListener('hashchange', () => { closePicker(); setRoute(routeFromHash()); });
  window.addEventListener('rota:data-changed', refreshData);
  document.addEventListener('keydown', onKey);
  // A failed request that nothing else reported: say so, rather than doing nothing.
  window.addEventListener('unhandledrejection', (event) => {
    event.preventDefault();
    toast(event.reason instanceof ApiError ? event.reason.message : 'Something went wrong. Please try again.', { tone: 'bad' });
  });
  // A new window size means a new fitting zoom: render again once it settles.
  let resizeTimer = 0;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => setSession({}), 150);
  });
  state.route = routeFromHash();
  try {
    state.boot = await api.get('/api/bootstrap');
    mountSync(document.getElementById('sync-pill'), document.getElementById('sync-banner'));
    announceUpdate();
    if (state.boot.current) await loadMonth(state.boot.current);
    else setRoute(state.route);
  } catch (error) {
    const message = error instanceof ApiError && error.status === 403
      ? 'Please open Rota Planner with the link shown in the window where it was started.'
      : error.message;
    state.bootError = message;  // kept: later renders show it instead of the empty screens
    document.getElementById('content').replaceChildren(h('p', { class: 'empty' }, message));
    toast(message, { tone: 'bad' });
  }
}

start();
