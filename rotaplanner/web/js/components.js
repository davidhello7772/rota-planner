// Form building blocks and overlays shared by the set-up screens.
import { fill, h } from './dom.js';
import { icon } from './icons.js';
import { readOnly } from './state.js';

let fieldCounter = 0;

/** A labelled form control. `control` is an input/select/etc. element. */
export function field(label, control, hint) {
  const id = control.id || `f${(fieldCounter += 1)}`;
  control.id = id;
  return h('div', { class: 'field' },
    h('label', { class: 'field-label', for: id }, label),
    control,
    hint ? h('span', { class: 'field-hint' }, hint) : null);
}

export function textInput(value, props = {}) {
  return h('input', { class: 'input', type: 'text', value: value ?? '', ...props });
}

export function numberInput(value, props = {}) {
  return h('input', { class: 'input num', type: 'number', inputmode: 'decimal', value: value ?? '', ...props });
}

export function select(options, value, props = {}) {
  return h('select', { class: 'select', ...props },
    options.map((o) => h('option', { value: o.value, selected: String(o.value) === String(value ?? '') }, o.label)));
}

/** An on/off switch with its label. Returns the label element; `.input` is the checkbox. */
export function toggle(label, checked, onchange) {
  const input = h('input', { type: 'checkbox', role: 'switch', checked: Boolean(checked), onchange });
  const el = h('label', { class: 'toggle' }, input, h('span', { class: 'toggle-track', 'aria-hidden': 'true' }), h('span', {}, label));
  el.input = input;
  return el;
}

/** Mutually exclusive choices. `getValue()` returns the current one. */
export function segmented(options, value, { label, onchange } = {}) {
  let current = value;
  const buttons = options.map((o) => h('button', {
    type: 'button', 'aria-pressed': String(o.value === current), dataset: { value: o.value },
    onclick: () => {
      current = o.value;
      buttons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.value === current)));
      onchange?.(current);
    },
  }, o.label));
  const el = h('div', { class: 'segmented', role: 'group', 'aria-label': label }, buttons);
  el.getValue = () => current;
  return el;
}

export function badge(text, tone = '') {
  return h('span', { class: ['badge', tone] }, text);
}

/**
 * Checkbox chips for picking several items. `getValue()` returns the chosen
 * ids, `setValue(ids)` changes them; `onchange(ids, clicked)` follows clicks.
 */
export function chipChoice(items, chosen, { label, onchange } = {}) {
  let selected = new Set(chosen);
  const buttons = items.map((item) => h('button', {
    type: 'button', class: 'choice', dataset: { value: item.value },
    onclick: () => {
      if (selected.has(item.value)) selected.delete(item.value); else selected.add(item.value);
      show();
      onchange?.(el.getValue(), item.value);
    },
  }, item.label));
  const show = () => buttons.forEach((b) => b.setAttribute('aria-pressed', String(selected.has(b.dataset.value))));
  const el = h('div', { class: 'chip-choice', role: 'group', 'aria-label': label }, buttons);
  el.getValue = () => items.map((i) => i.value).filter((v) => selected.has(v));
  el.setValue = (ids) => { selected = new Set(ids); show(); };
  show();
  return el;
}

// ---- Overlays -------------------------------------------------------------

const openOverlays = [];  // a drawer can open over a dialog: Escape closes the top one only

// ---- Looking only: another computer edits (sync.js) ----------------------------

let makeLookBar = () => null;  // set by sync.js: why this computer only looks, and the way back to editing
export function setLookBar(make) { makeLookBar = make; }

/**
 * Mark a control that changes the shared data: while another computer edits it
 * stays visible, greyed, and cannot be used (by mouse or keyboard).
 */
export function changes(el) {
  el.classList.add('changes');
  el.inert = readOnly();
  return el;
}

/** After the editing moved to or from this computer: every marked control, form and window follows. */
export function applyReadOnly() {
  const looking = readOnly();
  document.body.classList.toggle('read-only', looking);
  for (const el of document.querySelectorAll('.changes')) el.inert = looking;
  for (const set of document.querySelectorAll('fieldset.change-fields')) set.disabled = looking;
  for (const slot of document.querySelectorAll('.look-slot')) fill(slot, looking ? makeLookBar() : null);
}

/**
 * `changes`: the window changes the shared data. While another computer edits,
 * its form cannot be typed in, its buttons are hidden, and it says why.
 */
function overlay(kind, { title, subtitle, body, actions, onClose, wide, changes: changing = false }) {
  const previous = document.activeElement;
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    backdrop.remove();
    openOverlays.splice(openOverlays.indexOf(backdrop), 1);
    document.removeEventListener('keydown', onKey, true);
    // Back to what opened it; if a redraw replaced it, to the overlay below or the page.
    const below = openOverlays.at(-1);
    const target = previous?.isConnected ? previous : below?.querySelector('button, input, select') || document.getElementById('content');
    target?.focus({ preventScroll: true });
    onClose?.();
  };
  const onKey = (event) => {
    if (openOverlays.at(-1) !== backdrop) return;
    if (event.key === 'Escape' && !event.defaultPrevented) {
      if (document.querySelector('.popover')) return;  // a menu or a hint on top closes first
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === 'Tab') {  // keep the focus inside
      const focusable = [...panel.querySelectorAll('button, input, select, textarea, a[href], summary, [tabindex="0"]')]
        .filter((el) => !el.matches(':disabled') && el.offsetParent !== null);  // also off inside a disabled fieldset
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      else if (!panel.contains(document.activeElement)) { event.preventDefault(); first.focus(); }
    }
  };
  const panel = h('section', { class: [kind, wide && 'wide'], role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('header', { class: `${kind}-head` },
      h('div', {}, h('h2', {}, title), subtitle ? h('p', { class: 'muted' }, subtitle) : null),
      h('button', { class: 'btn ghost icon-only', type: 'button', 'aria-label': 'Close', onclick: close }, icon('close'))),
    h('div', { class: `${kind}-body` }, changing
      ? [h('div', { class: 'look-slot' }, readOnly() ? makeLookBar() : null),
        h('fieldset', { class: 'change-fields', disabled: readOnly() }, body)]
      : body),
    actions ? h('footer', { class: [`${kind}-foot`, changing && 'change-foot'] }, actions) : null);
  const backdrop = h('div', { class: ['backdrop', `backdrop-${kind}`],
    onmousedown: (e) => { if (e.target === backdrop) close(); } }, panel);
  // A second click on Save/Add while the first is being saved does nothing (no double events).
  panel.addEventListener('click', (event) => {
    const button = event.target.closest?.('footer .btn.primary');
    if (!button) return;
    if (button.dataset.busy) { event.stopImmediatePropagation(); event.preventDefault(); return; }
    button.dataset.busy = '1';
    setTimeout(() => { delete button.dataset.busy; }, 1200);
  }, true);
  document.body.append(backdrop);
  openOverlays.push(backdrop);
  document.addEventListener('keydown', onKey, true);
  (panel.querySelector('[autofocus]:enabled') || panel.querySelector(':is(input, select, textarea, button:not(.icon-only)):enabled'))?.focus();
  return { close, panel };
}

/** A panel sliding in from the right, for editing one thing. */
export const openDrawer = (options) => overlay('drawer', options);

/** A centred dialog. */
export const openDialog = (options) => overlay('modal', options);

/** Shows a form error inside an overlay, next to its buttons. */
export function formError(container, message) {
  let box = container.querySelector('.form-error');
  if (!box) {
    box = h('p', { class: 'form-error', role: 'alert' });
    container.prepend(box);
  }
  box.textContent = message;
}

/** Read a file chosen by the user as base64 (without the data: prefix). */
export function readFileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

// ---- Settings: a feature on one line, and plain-words explanations ----------

/**
 * One feature on a card: an icon, its name with its state, one sentence, its
 * buttons, and optionally more below (`extra`). `state` is [text, tone].
 */
export function statusLine({ iconName, title, state, text, actions = [], extra = null }) {
  return h('div', { class: 'status-line' },
    icon(iconName),
    h('div', { class: 'status-main' },
      h('h3', { class: 'status-title' }, title, state ? badge(state[0], state[1] || '') : null),
      text ? h('p', { class: 'status-text' }, text) : null,
      actions.length ? h('div', { class: 'status-actions' }, actions) : null,
      extra));
}

/** A question about a technical word, answered in plain words when opened. */
export function explain(question, ...answer) {
  return h('details', { class: 'explain' },
    h('summary', {}, question),
    answer.map((part) => (typeof part === 'string' ? h('p', {}, part) : part)));
}
