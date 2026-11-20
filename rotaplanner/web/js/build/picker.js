// The candidate picker: who could take this shift, best first, and why.
import {
  acceptProposals, assign, clearSlot, discardProposals, monthOf, parseSlotKey, reportError, setHeadcount, setNote, setTime, setWanted, swap,
} from '../actions.js';
import { changes, field, formError, numberInput, openDialog, segmented, textInput } from '../components.js';
import { api, monthPath } from '../api.js';
import { formatDate, formatHours, h, placeNear } from '../dom.js';
import { icon } from '../icons.js';
import { monthViewFor, readOnly, state } from '../state.js';

const GROUPS = [
  { id: 'best', title: 'Best matches' },
  { id: 'warn', title: 'Possible, with a warning' },
  { id: 'blocked', title: 'Away, busy or not trained' },
];
const BLOCKED_PREVIEW = 3;

let current = null;

export function closePicker() {
  if (!current) return;
  current.el.remove();
  document.removeEventListener('mousedown', current.outside, true);
  const anchor = current.anchor;
  current = null;
  if (anchor?.isConnected) anchor.focus({ preventScroll: true });
}

function loadBar(hours, target) {
  const ratio = target > 0 ? hours / target : 0;
  return h('span', { class: ['load', ratio > 1 && 'over', ratio < 0.5 && 'under'] },
    h('span', { style: { width: `${Math.min(100, Math.round(ratio * 100))}%` } }));
}

function reasons(candidate) {
  const parts = [];
  for (const finding of candidate.findings) {
    if (finding.severity !== 'note' || candidate.group === 'best') {
      parts.push(h('span', { class: finding.severity }, finding.message));
    }
  }
  if (candidate.same_day.length) parts.push(h('span', {}, `Also that day: ${candidate.same_day.join(', ')}`));
  if (!parts.length) parts.push(h('span', {}, 'Free that day'));
  return parts.flatMap((part, i) => (i ? [' · ', part] : [part]));
}

function candidateRow(candidate, onChoose) {
  const star = candidate.preference >= 4 ? h('span', { class: 'star', title: `Likes this area (${candidate.preference}/5)` }, '★') : null;
  const row = h('button', {
    class: ['cand', candidate.group === 'blocked' && 'blocked'], role: 'option', dataset: { id: candidate.id },
    title: candidate.note || undefined,
    onclick: () => onChoose(candidate, row),
  },
  h('span', { class: 'cand-name' }, candidate.name, star),
  h('span', { class: 'cand-load' }, loadBar(candidate.hours, candidate.target), `${formatHours(candidate.hours)} / ${formatHours(candidate.target)}`),
  h('span', { class: 'cand-why' }, reasons(candidate)));
  row.classList.add('quiet');  // still read while another computer edits: only choosing is off
  return changes(row);
}

function forceForm(candidate, row, key, query) {
  row.querySelector('.force')?.remove();
  const reason = h('input', { class: 'input', placeholder: 'Reason (optional)', maxlength: 200,
    onclick: (e) => e.stopPropagation(),
    onkeydown: (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') go();
      if (e.key === 'Escape') { form.remove(); query.focus(); }
    } });
  const go = async () => {
    closePicker();
    await assign(key, { personId: candidate.id }, { force: true, reason: reason.value.trim() });
  };
  const form = h('span', { class: 'force', onclick: (e) => e.stopPropagation() },
    reason, h('button', { class: 'btn small primary', onclick: go }, 'Assign anyway'));
  row.append(form);
  reason.focus();
}

function render(data, key) {
  const { taskId, date, position } = parseSlotKey(key);
  const query = h('input', { class: 'input', type: 'search', placeholder: 'Type a name…', 'aria-label': 'Search people' });
  const list = h('div', { class: 'picker-list', role: 'listbox', 'aria-label': 'People' });
  let showAllBlocked = false;
  let active = 0;

  const choose = async (candidate, row) => {
    if (candidate.group === 'blocked') { forceForm(candidate, row, key, query); return; }
    closePicker();
    await assign(key, { personId: candidate.id });
  };

  const rows = () => [...list.querySelectorAll('.cand')];
  const setActive = (index) => {
    const all = rows();
    if (!all.length) return;
    active = (index + all.length) % all.length;
    all.forEach((r, i) => r.classList.toggle('active', i === active && !readOnly()));  // nothing can be chosen while looking
    all[active].scrollIntoView({ block: 'nearest' });
  };

  const fill = () => {
    const text = query.value.trim().toLowerCase();
    const matches = data.candidates.filter((c) => !text || c.name.toLowerCase().includes(text) || c.full.toLowerCase().includes(text));
    list.replaceChildren();
    if (!text && data.ideas.length) list.append(ideasBlock(data.ideas, key));
    for (const group of GROUPS) {
      let members = matches.filter((c) => c.group === group.id);
      if (!members.length) continue;
      const hidden = group.id === 'blocked' && !text && !showAllBlocked ? Math.max(0, members.length - BLOCKED_PREVIEW) : 0;
      if (hidden) members = members.slice(0, BLOCKED_PREVIEW);
      list.append(h('div', { class: 'picker-group' }, h('span', {}, group.title), h('span', {}, String(members.length + hidden))));
      for (const candidate of members) list.append(candidateRow(candidate, choose));
      if (hidden) {
        list.append(h('button', { class: 'btn ghost small', onclick: () => { showAllBlocked = true; fill(); } }, `Show ${hidden} more`));
      }
    }
    if (!matches.length) list.append(h('p', { class: 'picker-empty' }, 'Nobody matches. Add them as someone not on the list below.'));
    setActive(0);
  };

  query.addEventListener('input', fill);
  query.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); setActive(active + 1); }
    if (event.key === 'ArrowUp') { event.preventDefault(); setActive(active - 1); }
    if (event.key === 'Enter') { event.preventDefault(); if (!readOnly()) rows()[active]?.click(); }
  });

  // Someone not on the list: a volunteer or a guest, whose name can come later.
  const known = data.current_guest;
  let guestKind = known?.kind || 'volunteer';
  const kinds = segmented([{ value: 'volunteer', label: 'Volunteer' }, { value: 'guest', label: 'Guest' }], guestKind,
    { label: 'Not on the list', onchange: (value) => { guestKind = value; } });
  const guestInput = h('input', { class: 'input', placeholder: 'Name, if known (can be added later)', maxlength: 60, value: known?.name || '',
    onkeydown: (e) => { if (e.key === 'Enter') addGuest(); } });
  const addGuest = async () => {
    closePicker();
    await assign(key, { guest: guestInput.value.trim(), guestKind });
  };

  const currentName = data.current
    ? (data.candidates.find((c) => c.id === data.current)?.name || `${data.current}${known?.name ? ` (${known.kind})` : ' — name to come'}`)
    : null;

  // What can be done with the slot itself, rather than with a person.
  const cell = monthViewFor(date)?.cells[`${taskId}|${date}`];
  const slot = cell?.slots[position];
  const slotActions = changes(h('div', { class: 'picker-actions' },
    slot?.open ? h('button', { class: 'btn small', 'aria-pressed': slot.wanted ? 'true' : 'false',
      title: 'Mark this empty shift as one to fill when possible (key H)',
      onclick: () => { closePicker(); setWanted(key, !slot.wanted); } }, slot.wanted ? '◆ Highlighted' : '◆ Highlight') : null,
    slot?.open && cell.required > 0 ? h('button', { class: 'btn small', title: 'One person fewer is needed on this day',
      onclick: () => { closePicker(); setHeadcount(taskId, date, cell.required - 1); } },
    cell.required === 1 ? 'Not needed this day' : 'One fewer needed') : null,
    cell ? h('button', { class: 'btn small', title: 'One more person is needed on this day',
      onclick: () => { closePicker(); setHeadcount(taskId, date, cell.required + 1); } }, 'One more needed') : null,
    h('button', { class: 'btn small', title: 'This task is at other times on this day (a special meal…)',
      onclick: () => { closePicker(); changeTime(data, taskId, date); } }, '◷ Change the time this day'),
    cell?.adjusted ? h('button', { class: 'btn small ghost', title: `Usually ${cell.usual}`,
      onclick: () => { closePicker(); setHeadcount(taskId, date, null); } }, `Back to usual (${cell.usual})`) : null));

  const el = h('div', { class: 'popover picker', role: 'dialog', tabindex: '-1', 'aria-label': `Choose someone for ${data.task.name}`,
    onkeydown: (e) => { if (e.key === 'Escape') { e.stopPropagation(); closePicker(); } } },
  h('div', { class: 'picker-head' },
    h('div', { class: 'picker-title' },
      h('div', {},
        h('strong', {}, data.task.name),
        h('span', { class: 'muted' }, [formatDate(data.date), data.task.time, data.task.hours ? `${data.task.hours} h` : null, position > 0 ? `position ${position + 1}` : null].filter(Boolean).join(' · ')),
        data.task.usual_time ? h('span', { class: 'retimed-note' }, `◷ Other time this day · usually ${data.task.usual_time}`) : null),
      h('button', { class: 'btn ghost icon-only small', 'aria-label': 'Close', onclick: closePicker }, icon('close'))),
    currentName ? h('div', { class: 'picker-current' }, h('span', { class: 'muted' }, 'Now:'), h('strong', {}, currentName), h('span', { class: 'spacer' }),
      changes(h('button', { class: 'btn small danger', onclick: () => { closePicker(); clearSlot(key); } }, 'Leave open'))) : null,
    currentName ? changes(noteLine(slot, key)) : null,
    slot?.proposal ? changes(proposalLine(slot.proposal, data, key)) : null,
    slotActions.childElementCount ? slotActions : null,
    // Another computer edits: who could take it is shown, nobody can be chosen.
    readOnly() ? h('p', { class: 'picker-look', role: 'status' }, icon('eye'), state.session.pen?.text || 'Another computer is editing: you can look, not change.') : null,
    h('div', { class: 'search' }, icon('search'), query)),
  list,
  changes(h('div', { class: 'picker-foot' }, kinds, guestInput, h('button', { class: 'btn small', onclick: addGuest }, known ? 'Save' : 'Add'))));

  fill();
  return { el, focus: () => query.focus() };
}

/** A short note on the person's place: shown with the name, and printed. */
function noteLine(slot, key) {
  const before = slot?.note || '';
  const input = h('input', { class: 'input', maxlength: 60, value: before, placeholder: 'Note, e.g. arrives at 10 · with a trainee',
    'aria-label': 'Note on this place',
    onkeydown: (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') save();
      if (e.key === 'Escape') closePicker();
    } });
  const save = () => {
    const text = input.value.trim();
    closePicker();
    if (text !== before) setNote(key, text);
  };
  return h('div', { class: 'picker-note' },
    h('div', { class: 'picker-note-row' }, input, h('button', { class: 'btn small', type: 'button', onclick: save }, before ? 'Save' : 'Add note')),
    h('span', { class: 'faint' }, 'Printed with the name. Practical notes only: nothing about health or private matters.'));
}

/** The suggestion waiting on this shift, with a quick way to accept it. */
function proposalLine(proposal, data, key) {
  const person = data.candidates.find((c) => c.id === proposal.person);
  return h('div', { class: 'picker-proposal' },
    h('span', {}, proposal.replaces ? 'Suggested move to ' : 'Suggested: ', h('strong', {}, person?.name || '?'),
      h('span', { class: 'faint' }, ` · ${proposal.why}`)),
    h('button', { class: 'btn small primary', type: 'button', onclick: () => { closePicker(); acceptProposals([key]); } }, 'Accept'),
    h('button', { class: 'btn small ghost', type: 'button', onclick: () => { closePicker(); discardProposals([key]); } }, 'Not this'));
}

/** Ways out when nobody can simply take the shift. */
function ideasBlock(ideas, key) {
  return h('div', { class: 'ideas' },
    h('div', { class: 'picker-group' }, h('span', {}, 'Ideas'), h('span', {}, '')),
    h('ul', {}, ideas.map((idea) => h('li', { class: `idea-${idea.kind}` },
      h('span', {}, idea.text),
      idea.kind === 'swap' ? changes(h('button', { class: 'btn small primary', type: 'button', onclick: () => {
        closePicker();
        swap(key, idea.action.person_id, idea.action.from, idea.action.replacement);
      } }, 'Do it')) : null))));
}

/** A small form for the task's times on one day. */
function changeTime(data, taskId, date) {
  const start = h('input', { class: 'input', type: 'time', value: data.task.start || '', autofocus: true });
  const end = h('input', { class: 'input', type: 'time', value: data.task.end || '' });
  const hours = numberInput(data.task.usual_time ? data.task.hours : '', { min: 0, max: 24, step: 0.25, placeholder: 'From the times' });
  const label = textInput(data.task.usual_time ? data.task.label : '', { placeholder: 'e.g. Christmas meal' });
  const save = async () => {
    try {
      await setTime(taskId, date, { start: start.value, end: end.value, hours: hours.value, label: label.value });
      dialog.close();
    } catch (error) {
      formError(dialog.panel.querySelector('.modal-body'), error.message);
    }
  };
  const dialog = openDialog({ changes: true,
    title: `${data.task.name} — ${formatDate(date)}`,
    subtitle: `Usually ${data.task.usual_time || data.task.time || 'no set time'}. Only this day changes.`,
    body: [h('div', { class: 'form-grid' }, field('Starts', start), field('Ends', end)),
      h('div', { class: 'form-grid' }, field('Hours counted', hours, 'Empty: worked out from the times'), field('Note', label))],
    actions: [
      data.task.usual_time ? h('button', { class: 'btn ghost', type: 'button', onclick: async () => {
        await setTime(taskId, date, null);
        dialog.close();
      } }, 'Back to the usual time') : null,
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: save }, 'Save'),
    ],
  });
}

let opening = 0;  // only the last click opens a picker

export async function openPicker(anchor, key) {
  closePicker();
  const ticket = ++opening;
  const { taskId, date, position } = parseSlotKey(key);
  let data;
  try {
    data = await api.get(monthPath(monthOf(date), `/candidates?task=${encodeURIComponent(taskId)}&date=${date}&position=${position}`));
  } catch (error) {
    reportError(error);
    return;
  }
  if (ticket !== opening) return;
  // The grid may have been drawn again meanwhile: the same shift, as it is now.
  if (!anchor.isConnected) anchor = document.querySelector(`.chip[data-key="${CSS.escape(key)}"]`);
  if (!anchor) return;
  closePicker();
  const { el, focus } = render(data, key);
  const outside = (event) => { if (!el.contains(event.target)) closePicker(); };
  current = { el, anchor, outside };
  document.body.append(el);
  placeNear(el, anchor.getBoundingClientRect());
  document.addEventListener('mousedown', outside, true);
  focus();
}
