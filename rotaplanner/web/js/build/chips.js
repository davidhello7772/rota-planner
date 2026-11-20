// A chip is one position of a task on a day: a person, a guest, or an open
// slot. The grid and the day view build chips the same way; CSS decides
// which text to show at each zoom level.
import { slotKey } from '../actions.js';
import { formatHours, h } from '../dom.js';

const FLAGS = { warn: '!', conflict: '!', forced: '⚑' };
const STATE_WORDS = {
  ok: '', warn: 'warning', conflict: 'breaks a rule', forced: 'assigned anyway', guest: 'guest',
};

function guestInitials(name) {
  const words = name.replace(/\(.*?\)/g, '').trim().split(/\s+/);
  return (words[0]?.[0] || '?').toUpperCase() + (words[1]?.[0] || '').toUpperCase();
}

/** A shift switched off by hand for one day; clicking it offers to restore it. */
export function offChip(cell, ctx) {
  return h('button', {
    class: 'chip off', tabindex: '-1',
    dataset: { off: `${ctx.taskId}|${ctx.date}`, col: ctx.col ?? '', pos: 0 },
    'aria-label': `Not needed this day (usually ${cell.usual})`,
  }, h('span', { class: 't-plus', 'aria-hidden': 'true' }, '–'), h('span', { class: 't-open' }, 'Not needed'));
}

/** In "All tasks" mode: a day the task is not needed, where a shift can be asked for. */
export function addChip(ctx) {
  return h('button', {
    class: 'chip add', tabindex: '-1', dataset: { add: `${ctx.taskId}|${ctx.date}`, col: ctx.col ?? '', pos: 0 },
    'aria-label': 'Not usually needed this day: ask for a shift',
  }, h('span', { class: 't-plus', 'aria-hidden': 'true' }, '+'), h('span', { class: 't-open' }, 'Add'));
}

/** Chips for one task on one day: one per position, or the "not needed" / "add" mark. */
export function cellChips(cell, ctx) {
  if (!cell || (!cell.slots.length && !cell.closed && !(cell.adjusted && cell.usual > 0))) {
    return ctx.showEmpty ? [addChip(ctx)] : [];
  }
  if (!cell.slots.length && cell.adjusted && cell.usual > 0) return [offChip(cell, ctx)];
  return cell.slots.map((slot, position) => chipFor(slot, { ...ctx, position }));
}

export function chipFor(slot, ctx) {
  const key = slotKey(ctx.taskId, ctx.date, ctx.position);
  const dataset = { key, col: ctx.col ?? '', pos: ctx.position };

  if (slot.open && slot.proposal) {
    // A suggestion not yet accepted: the person's name, dashed.
    const person = ctx.people.get(slot.proposal.person);
    dataset.person = slot.proposal.person;
    return h('button', { class: ['chip', 'proposed'], dataset, tabindex: '-1',
      'aria-label': `Suggested: ${person?.full || ''}` },
    h('span', { class: 't-init' }, person?.initials || '?'),
    h('span', { class: 't-short' }, person?.name || '?'),
    h('span', { class: 't-full' }, person?.full || '?'),
    h('span', { class: 't-sub' }, 'suggested'));
  }
  if (slot.open) {
    const eligible = ctx.eligible?.[key];
    const [glyph, text] = slot.wanted ? ['◆', '◆ To fill'] : slot.cover_for ? ['↻', '↻ Cover'] : ['+', 'Open'];
    return h('button', {
      class: ['chip', 'open', ctx.priority === 'optional' && 'optional', slot.cover_for && 'cover',
        slot.wanted && 'wanted', eligible && `elig-${eligible}`],
      dataset,
      tabindex: '-1',
      'aria-label': ['Open shift', slot.wanted && 'highlighted: to fill when possible',
        slot.cover_for && `${slot.cover_for} usually does it`].filter(Boolean).join(', '),
    },
    h('span', { class: 't-plus', 'aria-hidden': 'true' }, glyph),
    h('span', { class: 't-open' }, text));
  }

  const person = slot.person ? ctx.people.get(slot.person) : null;
  const short = person ? person.name : slot.guest;
  const full = person ? person.full : slot.unnamed ? `${slot.guest}, name to come` : `${slot.guest} (${slot.guest_kind})`;
  const initials = person ? person.initials : slot.unnamed ? (slot.guest_kind === 'volunteer' ? 'Vo?' : 'Gu?') : guestInitials(slot.guest);
  dataset.person = slot.person || '';
  const highlighted = ctx.highlight && slot.person === ctx.highlight;

  return h('button', {
    class: ['chip', `s-${slot.state}`, slot.source === 'regular' && 'regular', highlighted && 'hl', slot.proposal && 'moving', slot.unnamed && 'unnamed',
      slot.note && 'noted'],
    dataset,
    tabindex: '-1',
    'aria-label': [full, STATE_WORDS[slot.state]].filter(Boolean).join(', '),
  },
  h('span', { class: 't-init' }, initials),
  h('span', { class: 't-short' }, short),
  h('span', { class: 't-full' }, full),
  slot.source === 'regular' ? h('span', { class: 'reg', title: 'Regular', 'aria-hidden': 'true' }, '↻') : null,
  FLAGS[slot.state] ? h('span', { class: 'flag', 'aria-hidden': 'true' }, FLAGS[slot.state]) : null,
  person ? h('span', { class: 't-sub' }, `${formatHours(person.hours)} of ${formatHours(person.target)}`) : null);
}
