// Shared interface pieces: toasts, a confirmation dialog, small menus.
import { h, placeNear } from './dom.js';
import { setSession } from './state.js';

let toastBox = null;

export function toast(message, { actionLabel, onAction, tone = '' } = {}) {
  if (!toastBox) {
    toastBox = h('div', { class: 'toasts', role: 'status', 'aria-live': 'polite' });
    document.body.append(toastBox);
  }
  const item = h('div', { class: ['toast', tone] }, h('span', {}, message));
  let timer = 0;
  const dismiss = () => { clearTimeout(timer); item.remove(); };
  if (actionLabel) {
    item.append(h('button', { class: 'btn small', onclick: () => { dismiss(); onAction(); } }, actionLabel));
  }
  item.append(h('button', { class: 'btn small icon-only', 'aria-label': 'Dismiss', onclick: dismiss }, '×'));
  toastBox.replaceChildren(item);
  timer = setTimeout(dismiss, tone === 'bad' ? 8000 : 5000);
}

/** Copies a text and says so, or says how to do it by hand. True when it was copied. */
export async function copyText(text, done = 'Copied') {
  try {
    await navigator.clipboard.writeText(text);
    toast(done);
    return true;
  } catch {
    toast('Could not copy: select the text and press Ctrl C', { tone: 'bad' });
    return false;
  }
}

export function findingsList(findings) {
  return h('ul', { class: 'findings' },
    findings.map((f) => h('li', { class: f.severity }, h('span', { 'aria-hidden': 'true' }, f.severity === 'block' ? '✕' : '!'), f.message)));
}

/** Ask whether to break a rule. Resolves with the reason typed, or null. */
export function confirmForce({ title, findings }) {
  return new Promise((resolve) => {
    const previous = document.activeElement;
    const reason = h('input', { class: 'input', placeholder: 'Reason (optional), e.g. agreed by phone', maxlength: 200 });
    const done = (value) => {
      backdrop.remove();
      if (previous?.isConnected) previous.focus({ preventScroll: true });
      resolve(value);
    };
    const backdrop = h('div', { class: 'backdrop', onmousedown: (e) => { if (e.target === backdrop) done(null); } },
      h('div', { class: 'dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': title,
        onkeydown: (e) => {
          e.stopPropagation();
          if (e.key === 'Escape') done(null);
          if (e.key === 'Enter' && e.target === reason) done(reason.value.trim());
        } },
        h('h2', {}, title),
        findingsList(findings),
        reason,
        h('div', { class: 'dialog-actions' },
          h('button', { class: 'btn', onclick: () => done(null) }, 'Cancel'),
          h('button', { class: 'btn primary', onclick: () => done(reason.value.trim()) }, 'Assign anyway'))));
    document.body.append(backdrop);
    reason.focus();
  });
}

/**
 * Something small anchored to a button (a menu, the list of recent changes).
 * Closes on Esc and on a click outside; the focus goes back to the anchor.
 * Returns { el, close }.
 */
export function openPopover(anchor, { label, className = '', role = 'dialog', onClose } = {}, content = []) {
  let closed = false;
  const close = ({ refocus = true } = {}) => {
    if (closed) return;
    closed = true;
    el.remove();
    document.removeEventListener('mousedown', outside, true);
    if (refocus && anchor.isConnected) anchor.focus({ preventScroll: true });
    onClose?.();
  };
  const outside = (e) => { if (!el.contains(e.target)) close({ refocus: false }); };
  const el = h('div', { class: ['popover', className], role, 'aria-label': label,
    onkeydown: (e) => { if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); close(); } } }, content);
  document.body.append(el);
  placeNear(el, anchor.getBoundingClientRect());
  document.addEventListener('mousedown', outside, true);
  return { el, close };
}

/** A small menu of choices anchored to a button. */
export function openMenu(anchor, items, onPick) {
  const { el, close } = openPopover(anchor, { className: 'menu', role: 'menu' },
    items.map((item) => h('button', { role: 'menuitemradio', 'aria-checked': item.checked ? 'true' : 'false',
      onclick: () => { close(); onPick(item.value); } }, item.label)));
  (el.querySelector('[aria-checked="true"]') || el.querySelector('button'))?.focus();
}

/**
 * A link to one part of Settings ('message', 'mail', 'sync', 'backup'): the
 * page opens, scrolled to that part, which is briefly marked.
 */
export function settingsLink(part, label, className = 'link') {
  return h('a', { class: className, href: '#/settings', onclick: () => setSession({ settingsFocus: part }) }, label);
}
