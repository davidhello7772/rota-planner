// Help, kept out of the way until wanted:
//  - the Help panel ("?" in the top bar, or the ? key): what to do on this
//    screen, the likely next step, and answers to "How do I…";
//  - hint(): a small ⓘ next to a setting that needs a word of explanation;
//  - the first-visit tip: one line per screen, dismissed once and for all.
// The words themselves are in help-content.js.
import { openDrawer } from './components.js';
import { h, placeNear } from './dom.js';
import { HINTS, QUESTIONS, SCREENS, SHORTCUTS } from './help-content.js';
import { icon } from './icons.js';
import { setUi, state } from './state.js';
import { hasTour, startTour } from './tour.js';

const go = (route) => { window.location.hash = `#/${route}`; };

/** What would most usefully be done next, from where the month stands. */
export function nextStep() {
  const month = state.month;
  if (!month) return { text: 'There is no month yet: create the first one.', route: 'setup' };
  const active = month.people.filter((p) => p.active);
  const s = month.summary;
  if (active.length && !active.some((p) => p.replied)) {
    return { text: 'Nobody’s availability is known yet: bring in the replies of the form.', route: 'availability' };
  }
  if (s.conflicts) {
    return { text: `${s.conflicts} assignment${s.conflicts > 1 ? 's break' : ' breaks'} a rule: see which, and fix or keep on purpose.`, route: 'check' };
  }
  if (s.open) {
    return { text: `${s.open} shift${s.open > 1 ? 's are' : ' is'} still open: Prefill can suggest people, or highlight those to sign up for.`, route: 'build' };
  }
  return { text: 'Every shift is filled: have a last look in Check, then give it to the manager or publish it.', route: 'publish' };
}

function matches(item, words) {
  const text = `${item.q} ${item.find} ${item.a}`.toLowerCase();
  return words.every((word) => text.includes(word));
}

function question(item, close, open = false) {
  const here = item.go === state.route;
  return h('details', { class: 'help-q', open },
    h('summary', {}, item.q),
    h('p', {}, item.a),
    here ? null : h('button', { class: 'link', type: 'button', onclick: () => { close(); go(item.go); } },
      `Go to ${SCREENS[item.go].title}`, icon('right')));
}

/** The Help panel. `about`: a question to open straight away (its exact title). */
export function openHelp(about = '') {
  if (document.querySelector('.help-panel')) return;
  const screen = SCREENS[state.route];
  const search = h('input', { class: 'input', type: 'search', placeholder: 'How do I…', 'aria-label': 'Search the help', autofocus: !about });
  const results = h('div', { class: 'help-results' });
  const close = () => drawer.close();

  function show() {
    const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
    if (words.length) {
      const found = QUESTIONS.filter((item) => matches(item, words));
      results.replaceChildren(found.length
        ? h('div', { class: 'help-list' }, found.map((item) => question(item, close, found.length === 1)))
        : h('p', { class: 'faint' }, 'Nothing about that. Try another word, such as “print”, “event” or “away”.'));
      return;
    }
    const step = nextStep();
    const here = QUESTIONS.filter((item) => item.go === state.route);
    const elsewhere = QUESTIONS.filter((item) => item.go !== state.route);
    results.replaceChildren(
      h('section', { class: 'help-next' },
        h('span', { class: 'help-kicker' }, 'Next step'),
        h('p', {}, step.text),
        step.route === state.route ? null
          : h('button', { class: 'btn small primary', type: 'button', onclick: () => { close(); go(step.route); } }, `Go to ${SCREENS[step.route].title}`, icon('right'))),
      screen ? h('section', {},
        h('h3', {}, `On this screen: ${screen.title}`),
        h('ul', { class: 'help-points' }, screen.points.map((point) => h('li', {}, point))),
        hasTour(state.route) ? h('button', { class: 'btn small help-tour', type: 'button',
          onclick: () => { close(); startTour(); } }, icon('sparkles'), 'Show me around this screen') : null) : null,
      h('section', {},
        h('h3', {}, 'How do I…'),
        h('div', { class: 'help-list' },
          here.map((item) => question(item, close, item.q === about)),
          elsewhere.map((item) => question(item, close, item.q === about)))),
      h('section', {},
        h('h3', {}, 'Keyboard'),
        h('dl', { class: 'help-keys' }, SHORTCUTS.map(([keys, what]) => [h('dt', {}, h('kbd', {}, keys)), h('dd', {}, what)]))),
      h('section', { class: 'help-foot' },
        h('p', { class: 'faint' }, 'Everything stays on this computer: nothing is sent over the internet.'),
        h('button', { class: 'link', type: 'button', onclick: () => { setUi({ helpSeen: {} }); close(); } }, 'Show the first-time tips again')));
  }

  search.addEventListener('input', show);
  const drawer = openDrawer({
    title: 'Help', subtitle: screen ? `You are on ${screen.title}` : '',
    body: [h('label', { class: 'search help-search' }, icon('search'), search), results],
  });
  drawer.panel.classList.add('help-panel');
  show();
  results.querySelector('details[open]')?.scrollIntoView({ block: 'center' });
}

// ---- Hints: a small ⓘ that explains one thing -----------------------------------

let shown = null;  // { pop, button, pinned }

function hideHint() {
  if (!shown) return;
  shown.pop.remove();
  shown.button.setAttribute('aria-expanded', 'false');
  document.removeEventListener('mousedown', shown.outside, true);
  document.removeEventListener('keydown', shown.onKey, true);
  shown = null;
}

function showHint(button, text, pinned) {
  if (shown?.button === button) {
    shown.pinned = shown.pinned || pinned;
    return;
  }
  hideHint();
  const pop = h('div', { class: 'popover hint-pop', role: 'note' }, text);
  const outside = (event) => { if (!pop.contains(event.target) && event.target !== button && !button.contains(event.target)) hideHint(); };
  const onKey = (event) => { if (event.key === 'Escape') { event.stopPropagation(); hideHint(); button.focus(); } };
  shown = { pop, button, pinned, outside, onKey };
  document.body.append(pop);
  placeNear(pop, button.getBoundingClientRect(), 6);
  button.setAttribute('aria-expanded', 'true');
  document.addEventListener('mousedown', outside, true);
  document.addEventListener('keydown', onKey, true);
}

/**
 * A discreet ⓘ button. `about` is a key of HINTS (or the text itself).
 * Hovering shows the explanation; clicking keeps it until clicked away.
 */
export function hint(about) {
  const text = HINTS[about] || about;
  const button = h('button', { class: 'hint', type: 'button', 'aria-label': 'What is this?', 'aria-expanded': 'false',
    onclick: (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (shown?.button === button && shown.pinned) hideHint(); else showHint(button, text, true);
    },
    onmouseenter: () => showHint(button, text, false),
    onmouseleave: () => { if (shown?.button === button && !shown.pinned) hideHint(); },
    onblur: () => { if (shown?.button === button) hideHint(); },
  }, icon('info'));
  return button;
}

// ---- First-visit tip: one line per screen, until "Got it" -------------------------

/** Fill `slot` with the tip of the current screen, or nothing once seen. */
export function renderCoach(slot) {
  const screen = SCREENS[state.route];
  const seen = state.ui.helpSeen || {};
  const wanted = screen && !seen[state.route] && !state.session.focus && Boolean(state.boot);
  const current = slot.dataset.route || '';
  if (!wanted) {
    if (current) { slot.replaceChildren(); slot.dataset.route = ''; slot.hidden = true; }
    return;
  }
  if (current === state.route) return;
  slot.dataset.route = state.route;
  slot.hidden = false;
  const seenIt = () => setUi({ helpSeen: { ...(state.ui.helpSeen || {}), [state.route]: true } });
  slot.replaceChildren(h('div', { class: 'coach', role: 'note' },
    icon('info'),
    h('span', { class: 'coach-text' }, h('strong', {}, `${screen.title}. `), screen.tip),
    hasTour(state.route) ? h('button', { class: 'btn small primary show-me', type: 'button',
      onclick: () => { if (startTour()) seenIt(); } }, icon('sparkles'), 'Show me') : null,
    h('button', { class: 'link', type: 'button', onclick: () => openHelp() }, 'More help'),
    h('button', { class: 'btn small', type: 'button', onclick: seenIt }, 'Got it')));
}
