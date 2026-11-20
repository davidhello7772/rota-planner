// The page shown when there is no rota yet (and for an unknown screen).
import { h } from './dom.js';

export function pageFor(route, hasMonth) {
  if (route === 'build' && !hasMonth) {
    return h('div', { class: 'page' }, h('div', { class: 'empty' },
      h('h1', {}, 'No rota yet'),
      h('p', {}, 'Start by importing your current Google Sheets workbook, or add people and tasks by hand.'),
      h('p', {}, h('a', { class: 'btn primary', href: '#/settings' }, 'Import the current workbook'), ' ',
        h('a', { class: 'btn', href: '#/people' }, 'Add people'))));
  }
  return h('div', { class: 'page' }, h('p', { class: 'empty' }, 'Nothing to show here.'));
}
