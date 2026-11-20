// The password screen, shown instead of the program when a password is set
// and this browser has no session (or its session is over).
import { api, ApiError, setToken } from './api.js';
import { h } from './dom.js';

let shown = false;

export function showLogin(note = '') {
  if (shown) return;
  shown = true;
  const app = document.getElementById('app');
  const input = h('input', { class: 'input', type: 'password', id: 'login-password', autocomplete: 'current-password',
    required: true, 'aria-describedby': 'login-message' });
  const message = h('p', { class: 'login-message', id: 'login-message', role: 'alert' }, note);
  const button = h('button', { class: 'btn primary', type: 'submit' }, 'Open');
  const form = h('form', { class: 'login-card', onsubmit: async (event) => {
    event.preventDefault();
    button.disabled = true;
    message.textContent = '';
    try {
      const answer = await api.post('/api/login', { password: input.value });
      setToken(answer.session);
      window.location.reload();
    } catch (error) {
      message.textContent = error instanceof ApiError ? error.message : 'Something went wrong. Please try again.';
      input.select();
      button.disabled = false;
    }
  } },
  h('div', { class: 'brand' }, h('span', { class: 'brand-mark', 'aria-hidden': 'true' }, 'R'),
    h('span', { class: 'brand-name' }, 'Rota Planner')),
  h('label', { class: 'field-label', for: 'login-password' }, 'Password'),
  input, message,
  h('div', { class: 'card-actions' }, button),
  h('p', { class: 'faint' }, 'Forgotten? On the computer where Rota Planner runs, start it from a terminal with: python3 run.py --reset-access'));
  app.classList.remove('rail', 'focus');
  app.replaceChildren(h('main', { class: 'login-page' }, form));
  input.focus();
}
