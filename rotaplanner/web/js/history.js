// Recent changes: a month's steps, newest first, to go back several at once
// (or redo what was undone). What it says comes from the program
// (App.history_list); this only lays it out. The list follows every undo,
// redo or change while it is open (Ctrl+Z works with it open).
import { goTo, monthsOnScreen } from './actions.js';
import { api, monthPath } from './api.js';
import { changes, segmented } from './components.js';
import { h, placeNear, replaceKeepingFocus } from './dom.js';
import { state, subscribe } from './state.js';
import { openPopover, toast } from './ui.js';
import { monthTitle } from './build/range.js';

let open = null;  // { anchor, popover, monthId, months, signature, data, stop }

/** The history the screen has for a month (its view, or Tasks & Areas for that month). */
function historyOf(monthId) {
  if (state.month?.id === monthId) return state.month.history;
  if (state.session.otherMonths[monthId]) return state.session.otherMonths[monthId].history;
  if (state.session.config?.scope === monthId) return state.session.config.history;
  return undefined;
}

const signatureOf = (history) => `${history?.undo?.id ?? ''}|${history?.redo?.id ?? ''}`;

/** Open the list for `monthId` under `anchor`; in the Event view over two months, a switch chooses the month. */
export function openHistory(anchor, monthId) {
  open?.popover.close({ refocus: false });
  const onScreen = monthsOnScreen();
  const months = onScreen.length > 1 && onScreen.includes(monthId) ? onScreen : [monthId];
  const popover = openPopover(anchor, { label: 'Recent changes', className: 'history', onClose: () => {
    open?.stop();
    open = null;
  } }, h('p', { class: 'faint history-hint' }, 'Loading…'));
  popover.el.addEventListener('keydown', moveFocus);
  const stop = subscribe(follow);
  open = { anchor, popover, monthId, months, signature: signatureOf(historyOf(monthId)), data: null, stop };
  load(true);
}

/** After any change on screen: the list again, if the month's history moved (or close, if the month went). */
function follow() {
  if (!open) return;
  const history = historyOf(open.monthId);
  if (history === undefined) {
    open.popover.close({ refocus: false });
    return;
  }
  const signature = signatureOf(history);
  if (signature !== open.signature) {
    open.signature = signature;
    load(false);
  }
}

async function load(first) {
  const asked = open;
  let data;
  try {
    data = await api.get(monthPath(asked.monthId, '/history'));
  } catch (error) {
    toast(error.message, { tone: 'bad' });
    asked.popover.close();
    return;
  }
  if (open !== asked) return;  // closed, or opened again, meanwhile
  open.data = data;
  render(first);
}

function render(first) {
  const { popover, data, months, monthId } = open;
  const rows = [...data.undone.map((step, i) => row(step, 'redo', i)), ...data.steps.map((step, i) => row(step, 'undo', i))];
  const empty = !data.steps.length && !data.undone.length;
  replaceKeepingFocus(popover.el,
    h('header', { class: 'history-head' },
      h('h2', {}, data.title),
      months.length > 1 ? segmented(months.map((id) => ({ value: id, label: monthTitle(id) })), monthId, {
        label: 'Month', onchange: (id) => { open.monthId = id; open.signature = signatureOf(historyOf(id)); load(false); },
      }) : null),
    empty ? h('p', { class: 'history-empty' }, data.empty) : h('p', { class: 'faint history-hint' }, 'Click a change to go back to before it.'),
    empty ? null : h('div', { class: 'history-list', dataset: { scrollId: 'history' } },
      data.undone.length ? h('p', { class: 'history-sub' }, 'Undone · click one to redo up to it') : null,
      rows.slice(0, data.undone.length),
      h('div', { class: 'history-now', 'aria-hidden': 'true' }, h('span', {}, 'Now')),
      rows.slice(data.undone.length)),
    h('p', { class: 'faint history-note' }, data.note));
  placeNear(popover.el, open.anchor.getBoundingClientRect());
  if (first) {
    const target = popover.el.querySelector('.history-row:not(.redo)') || popover.el.querySelector('.history-row')
      || popover.el.querySelector('button');
    if (target) target.focus({ preventScroll: true });
    else { popover.el.tabIndex = -1; popover.el.focus(); }
  }
}

/** One change. Hovering or focusing it marks what a click would undo (it and every newer one) or redo. */
function row(step, direction, index) {
  const title = direction === 'undo'
    ? `Go back to before this change${step.count > 1 ? ` (${step.count} changes undone)` : ''}`
    : `Redo up to this change${step.count > 1 ? ` (${step.count} changes)` : ''}`;
  const button = h('button', {
    type: 'button', class: ['history-row', direction, step.kind === 'auto' && 'auto'], title,
    dataset: { focusId: `history-${step.id}`, index: String(index) },
    onclick: () => goTo(open.monthId, step.id),
    onmouseenter: () => mark(button), onfocus: () => mark(button),
    onmouseleave: () => mark(null), onblur: () => mark(null),
  }, h('span', { class: 'history-label' }, step.label), h('span', { class: 'history-when' }, step.when));
  return changes(button);
}

/** What will be undone (the row and every newer one) or redone (the row and every one down to Now). */
function mark(button) {
  const list = open?.popover.el;
  if (!list) return;
  list.querySelectorAll('.history-row.marked').forEach((el) => el.classList.remove('marked'));
  if (!button) return;
  const index = Number(button.dataset.index);
  const undo = button.classList.contains('undo');
  list.querySelectorAll(`.history-row.${undo ? 'undo' : 'redo'}`).forEach((el) => {
    const i = Number(el.dataset.index);
    if (undo ? i <= index : i >= index) el.classList.add('marked');
  });
}

/** Up, Down, Home and End move between the changes (Enter acts, Esc closes). */
function moveFocus(event) {
  if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
  const rows = [...open.popover.el.querySelectorAll('.history-row')].filter((el) => !el.closest('[inert]') && !el.inert);
  if (!rows.length) return;
  event.preventDefault();
  const at = rows.indexOf(document.activeElement);
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? rows.length - 1
    : at < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, at + (event.key === 'ArrowUp' ? -1 : 1)));
  rows[next].focus();
  rows[next].scrollIntoView({ block: 'nearest' });
}
