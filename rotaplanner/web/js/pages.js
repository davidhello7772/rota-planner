// The page shown when there is no rota yet (and for an unknown screen).
import { h } from './dom.js';

export function pageFor(route, hasMonth) {
  if (route === 'build' && !hasMonth) {
    return h('div', { class: 'page' }, h('div', { class: 'empty' },
      h('h1', {}, 'No rota yet'),
      h('p', {}, 'Add the people and the tasks, then create the first month.'),
      h('p', {}, h('a', { class: 'btn primary', href: '#/people' }, 'Add people'), ' ',
        h('a', { class: 'btn', href: '#/tasks' }, 'Add tasks'))));
  }
  return h('div', { class: 'page' }, h('p', { class: 'empty' }, 'Nothing to show here.'));
}
