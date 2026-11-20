// A short guided tour of a screen: a soft spotlight glides from one part of
// the screen to the next, with a small card saying what it is for. Offered on
// the first visit ("Show me") and from the Help panel; never started unasked.
// The steps are in help-content.js (TOURS). Positions are set through CSSOM,
// as the page's security policy requires; motion is reduced when the system
// asks for it (see help.css).
import { h } from './dom.js';
import { TOURS } from './help-content.js';
import { icon } from './icons.js';
import { state } from './state.js';

const PAD = 6;       // room around the part shown, in pixels
const GAP = 12;      // between the spotlight and the card
const MARGIN = 12;   // card kept this far from the window's edges

let running = null;

export const hasTour = (route) => Boolean(TOURS[route]);

function visible(element) {
  if (!element) return false;
  const box = element.getBoundingClientRect();
  return box.width > 0 && box.height > 0;
}

/** The part of the screen a step points at, as it is now (screens redraw themselves). */
const partOf = (step) => [...document.querySelectorAll(step.target)].find(visible) || null;

/** The steps whose part of the screen is there right now. */
const stepsFor = (route) => (TOURS[route] || []).filter(partOf);

export function endTour() {
  if (!running) return;
  running.layer.remove();
  document.removeEventListener('keydown', running.onKey, true);
  window.removeEventListener('resize', running.place);
  const back = running.returnTo;
  running = null;
  if (back?.isConnected) back.focus({ preventScroll: true });
}

/** Start the tour of a screen (the current one by default). Resolves nothing; ends by itself. */
export function startTour(route = state.route) {
  endTour();
  const steps = stepsFor(route);
  if (!steps.length) return false;
  let index = 0;

  const spot = h('div', { class: 'tour-spot', 'aria-hidden': 'true' });
  const title = h('strong', { class: 'tour-title' });
  const text = h('p', { class: 'tour-text' });
  const dots = h('span', { class: 'tour-dots', 'aria-hidden': 'true' });
  const back = h('button', { class: 'btn small ghost', type: 'button', onclick: () => show(index - 1) }, 'Back');
  const next = h('button', { class: 'btn small primary', type: 'button', onclick: () => (index === steps.length - 1 ? endTour() : show(index + 1)) });
  const card = h('div', { class: 'tour-card', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Guided tour' },
    h('button', { class: 'btn ghost icon-only small tour-close', type: 'button', 'aria-label': 'End the tour', onclick: endTour }, icon('close')),
    title, text,
    h('div', { class: 'tour-foot' }, dots, h('span', { class: 'spacer' }), back, next));
  // The layer catches clicks, so nothing is changed by accident during the tour.
  const layer = h('div', { class: 'tour-layer', onmousedown: (event) => { if (event.target === layer) endTour(); } }, spot, card);

  function place() {
    const element = partOf(steps[index]);
    if (!element) { endTour(); return; }
    const box = element.getBoundingClientRect();
    const top = Math.max(4, box.top - PAD);
    const left = Math.max(4, box.left - PAD);
    const width = Math.min(window.innerWidth - left - 4, box.width + 2 * PAD);
    const height = Math.min(window.innerHeight - top - 4, box.height + 2 * PAD);
    for (const [key, value] of Object.entries({ top, left, width, height })) spot.style.setProperty(key, `${value}px`);
    // The card goes under the part shown, else above, else beside it.
    const cw = card.offsetWidth;
    const ch = card.offsetHeight;
    let cardTop = top + height + GAP;
    if (cardTop + ch > window.innerHeight - MARGIN) cardTop = top - GAP - ch;
    let cardLeft = left + width / 2 - cw / 2;
    if (cardTop < MARGIN) {  // no room above or below: beside
      cardTop = Math.min(Math.max(MARGIN, top), window.innerHeight - ch - MARGIN);
      cardLeft = left + width + GAP + cw < window.innerWidth - MARGIN ? left + width + GAP : left - GAP - cw;
    }
    cardLeft = Math.min(Math.max(MARGIN, cardLeft), window.innerWidth - cw - MARGIN);
    card.style.setProperty('top', `${cardTop}px`);
    card.style.setProperty('left', `${cardLeft}px`);
  }

  function show(to) {
    index = Math.min(Math.max(0, to), steps.length - 1);
    const step = steps[index];
    partOf(step)?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    title.textContent = step.title;
    text.textContent = step.text;
    dots.replaceChildren(...steps.map((_, i) => h('i', { class: i === index && 'on' })));
    back.hidden = index === 0;
    next.textContent = index === steps.length - 1 ? 'Done' : 'Next';
    place();
    next.focus({ preventScroll: true });
  }

  const onKey = (event) => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); endTour(); }
    if (event.key === 'ArrowRight') { event.preventDefault(); if (index < steps.length - 1) show(index + 1); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); show(index - 1); }
    if (event.key === 'Tab') {  // keep the focus in the card
      const buttons = [...card.querySelectorAll('button:not([hidden])')];
      const at = buttons.indexOf(document.activeElement);
      event.preventDefault();
      buttons[(at + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length].focus();
    }
  };

  running = { layer, onKey, place, returnTo: document.activeElement };
  document.body.append(layer);
  document.addEventListener('keydown', onKey, true);
  window.addEventListener('resize', place);
  show(0);
  return true;
}
