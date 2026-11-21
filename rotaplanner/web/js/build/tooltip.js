// Hover details for chips and task names: whatever the zoom level, the
// full information is one hover away.
import { parseSlotKey, taskById } from '../actions.js';
import { formatDate, h, placeNear } from '../dom.js';
import { monthViewFor, peopleById } from '../state.js';

const DELAY_MS = 350;
const SOURCE_WORDS = { regular: 'Regular', manual: '', auto: 'Suggested' };
let tip = null;
let timer = 0;

function hide() {
  clearTimeout(timer);
  tip?.remove();
  tip = null;
}

function cellOf(key) {
  const { taskId, date, position } = parseSlotKey(key);
  return { cell: monthViewFor(date)?.cells[`${taskId}|${date}`], taskId, date, position };
}

function offLines(chip) {
  const [taskId, date] = chip.dataset.off.split('|');
  const task = taskById(taskId, date);
  const cell = monthViewFor(date)?.cells[chip.dataset.off];
  return [h('strong', {}, 'Not needed this day'),
    h('span', { class: 'tt-line' }, `${task?.name || ''} · ${formatDate(date)}`),
    h('span', { class: 'tt-line' }, `Usually ${cell?.usual} · click to bring it back`)];
}

function chipLines(chip) {
  if (chip.dataset.off) return offLines(chip);
  if (!chip.dataset.key) return null;
  const { cell, taskId, date, position } = cellOf(chip.dataset.key);
  const slot = cell?.slots[position];
  const task = taskById(taskId, date);
  const people = peopleById(monthViewFor(date));
  if (!slot || !task) return null;
  const time = cell.time || task.time;
  const where = h('span', { class: 'tt-line' }, `${task.name} · ${formatDate(date)}${time ? ` · ${time}` : ''}${cell.time ? ' (other time this day)' : ''}`);
  const suggested = slot.proposal && people.get(slot.proposal.person);
  if (slot.open && suggested) {
    return [h('strong', {}, `Suggested: ${suggested.full}`), where, h('span', { class: 'tt-line' }, slot.proposal.why),
      h('span', { class: 'tt-line' }, 'Accept them in the Assistant, or click to choose someone else')];
  }
  if (slot.open) {
    const title = slot.wanted ? '◆ Highlighted: to fill when possible'
      : slot.cover_for ? `Cover needed — ${slot.cover_for} usually does this` : 'Open shift';
    return [h('strong', {}, title), where,
      slot.wanted && slot.cover_for ? h('span', { class: 'tt-line' }, `${slot.cover_for} usually does this`) : null,
      h('span', { class: 'tt-line' }, slot.wanted ? 'Click to choose someone · H removes the highlight' : 'Click to choose someone · H highlights it')];
  }
  const person = people.get(slot.person);
  const lines = [h('strong', {}, person ? person.full : slot.unnamed ? `${slot.guest} — name to come` : `${slot.guest} (${slot.guest_kind})`), where];
  // The person as the month of this day has them (an event can show two months): words from the program.
  if (person) {
    const day = monthViewFor(date)?.days.find((d) => d.date === date);
    const week = day ? person.by_week?.[day.week] : null;
    lines.push(h('span', { class: 'tt-line' }, person.week_text),
      h('span', { class: 'tt-line' }, week ? `${week.that_week_text} · ${person.month_text}` : person.month_text));
  }
  if (slot.note) lines.push(h('span', { class: 'tt-line' }, `✎ ${slot.note}`));
  const source = SOURCE_WORDS[slot.source];
  if (source) lines.push(h('span', { class: 'tt-line' }, `↻ ${source}`));
  for (const finding of slot.findings) {
    lines.push(h('span', { class: ['tt-line', finding.severity === 'block' ? 'tt-bad' : 'tt-warn'] }, `! ${finding.message}`));
  }
  if (slot.forced !== null && slot.forced !== undefined) {
    lines.push(h('span', { class: 'tt-line' }, `⚑ Assigned anyway${slot.forced ? `: ${slot.forced}` : ''}`));
  }
  if (suggested) lines.push(h('span', { class: 'tt-line tt-warn' }, `→ Suggested move to ${suggested.full}: ${slot.proposal.why}`));
  return lines;
}

function labelLines(th) {
  const task = taskById(th.dataset.task);
  if (!task) return null;
  return [h('strong', {}, task.name),
    h('span', { class: 'tt-line' }, [task.time, task.hours ? `${task.hours} h` : 'not counted in hours'].filter(Boolean).join(' · ')),
    h('span', { class: 'tt-line' }, `Skill: ${task.skill}`),
    task.priority !== 'normal' ? h('span', { class: 'tt-line' }, task.priority === 'critical' ? 'Essential' : 'Optional') : null];
}

export function attachTooltips(root) {
  root.addEventListener('mouseover', (event) => {
    const target = event.target.closest('.chip, .task-label');
    if (!target) return;
    hide();
    timer = setTimeout(() => {
      const lines = target.classList.contains('chip') ? chipLines(target) : labelLines(target);
      if (!lines || !target.isConnected) return;
      tip = h('div', { class: 'tooltip', role: 'tooltip' }, lines);
      document.body.append(tip);
      placeNear(tip, target.getBoundingClientRect(), 4);
    }, DELAY_MS);
  });
  root.addEventListener('mouseout', (event) => {
    if (!event.relatedTarget || !event.relatedTarget.closest?.('.chip, .task-label')) hide();
  });
  root.addEventListener('mousedown', hide);
  root.addEventListener('scroll', hide, true);
}

export { hide as hideTooltip };
