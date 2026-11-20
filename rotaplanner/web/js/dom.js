// Tiny DOM helpers. Text is always inserted as text nodes, never as HTML,
// so names and comments typed by people can never inject markup.

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === 'class') el.className = Array.isArray(value) ? value.filter(Boolean).join(' ') : value;
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key === 'style') for (const [name, v] of Object.entries(value)) el.style.setProperty(name, v);
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

/**
 * Replace an element's content; null, false and nested arrays are handled like in h().
 * Scrolling areas marked `data-scroll-id` keep their scroll position across the redraw.
 */
export function fill(el, ...children) {
  const scrolled = [...el.querySelectorAll('[data-scroll-id]')].map((s) => [s.dataset.scrollId, s.scrollTop, s.scrollLeft]);
  append(clear(el), children);
  for (const [id, top, left] of scrolled) {
    const again = el.querySelector(`[data-scroll-id="${id}"]`);
    if (again) { again.scrollTop = top; again.scrollLeft = left; }
  }
  return el;
}

/** What identifies a control across redraws when it has no `data-focus-id`. */
const controlKey = (el) => `${el.tagName}|${el.getAttribute('aria-label') || ''}|${el.textContent.trim().slice(0, 60)}`;

/** Place a fixed-position element next to an anchor, inside the viewport. */
export function placeNear(el, anchorRect, gap = 6) {
  const margin = 8;
  const { innerWidth: vw, innerHeight: vh } = window;
  const box = el.getBoundingClientRect();
  let left = anchorRect.left;
  let top = anchorRect.bottom + gap;
  if (left + box.width > vw - margin) left = Math.max(margin, anchorRect.right - box.width);
  if (top + box.height > vh - margin) {
    const above = anchorRect.top - gap - box.height;
    top = above >= margin ? above : Math.max(margin, vh - margin - box.height);
  }
  el.style.setProperty('left', `${Math.round(left)}px`);
  el.style.setProperty('top', `${Math.round(top)}px`);
}

/**
 * Re-render a region without losing the keyboard focus: the element marked
 * `data-focus-id` (or the chip with the same `data-key`) gets it back.
 */
export function replaceKeepingFocus(container, ...children) {
  const active = document.activeElement;
  const inside = active && container.contains(active);
  const focusId = inside ? active.dataset.focusId : null;
  const key = inside ? active.dataset.key : null;
  const same = inside && !focusId && !key ? controlKey(active) : null;
  const caret = inside && 'selectionStart' in active ? active.selectionStart : null;
  fill(container, ...children);
  if (!inside) return;
  const selector = focusId ? `[data-focus-id="${focusId}"]` : key ? `[data-key="${CSS.escape(key)}"]` : null;
  const target = selector ? container.querySelector(selector)
    : [...container.querySelectorAll(active.tagName)].find((el) => controlKey(el) === same);
  if (!target) return;
  if (key) {
    container.querySelectorAll('.chip[tabindex="0"]').forEach((c) => { c.tabIndex = -1; });
    target.tabIndex = 0;
  }
  target.focus({ preventScroll: true });
  if (caret !== null && typeof target.setSelectionRange === 'function') target.setSelectionRange(caret, caret);
}

export const isTyping = (target) =>
  target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));

export function formatHours(value) {
  const rounded = Math.round(value * 100) / 100;
  return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(2).replace(/0$/, '')} h`;
}

const DATE_FMT = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
export const formatDate = (iso) => DATE_FMT.format(new Date(`${iso}T12:00:00`));
