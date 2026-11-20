// The month strip: one small bar per day showing how well it is staffed.
// It doubles as navigation: click a day to bring it into view.
import { h } from '../dom.js';

const LONG = new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long' });

export function buildStrip(month, visible, onPick) {
  const shown = new Set(visible.map((d) => d.date));
  return h('nav', { class: 'strip', 'aria-label': 'Days of the month' },
    month.days.map((day) => {
      const ratio = day.required ? day.filled / day.required : 1;
      const open = day.required - day.filled;
      const level = ratio >= 1 ? '' : ratio >= 0.85 ? 'partial' : 'low';
      const label = `${LONG.format(new Date(`${day.date}T12:00:00`))}: ${day.required ? `${day.filled} of ${day.required} filled` : 'nothing to staff'}`
        + (day.conflicts ? `, ${day.conflicts} to fix` : '');
      return h('button', {
        class: ['strip-day', level, day.weekend && 'weekend', shown.has(day.date) && visible.length < month.days.length && 'in-range', day.conflicts && 'conflict'],
        title: label + (open > 0 ? ` (${open} open)` : ''),
        'aria-label': label,
        onclick: () => onPick(day.date),
      },
      h('span', {}, day.day),
      h('span', { class: 'strip-bar' }, h('span', { class: 'strip-fill', style: { width: `${Math.round(Math.min(1, ratio) * 100)}%` } })));
    }));
}
