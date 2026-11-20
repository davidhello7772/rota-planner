// Settings: one page, three cards. What is adjusted from time to time stays
// on the page as a plain form (the hours, the words of the schedule message);
// what is set up once (sending by e-mail, several computers, a backup copy) is
// one line each, saying whether it is on, and its set-up opens in a window.
// Four cards: Centre and hours, Schedules by e-mail, Your data, The program.
import { formError, field, numberInput, textInput, toggle } from '../components.js';
import { fill, h } from '../dom.js';
import { mailLine } from '../mailer.js';
import { syncLine } from '../syncsetup.js';
import { backupLine } from '../backup.js';
import { updateLine } from '../updates.js';
import { api } from '../api.js';
import { errorText, saveSetup } from '../setup.js';
import { setSession, state } from '../state.js';

/**
 * Save for one card: it turns into the main button only once something was
 * changed, and says "Not saved yet" until it is.
 */
function saveBar(inputs, save) {
  const value = (el) => (el.type === 'checkbox' ? String(el.checked) : el.value);
  let saved = inputs.map(value);
  const note = h('span', { class: 'save-note', role: 'status' });
  const button = h('button', { class: 'btn', type: 'button' }, 'Save');
  const bar = h('div', { class: 'save-bar' }, note, button);
  const changed = () => inputs.some((el, i) => value(el) !== saved[i]);
  const show = () => {
    const dirty = changed();
    button.classList.toggle('primary', dirty);
    note.textContent = dirty ? 'Not saved yet' : '';
  };
  inputs.forEach((el) => { el.addEventListener('input', show); el.addEventListener('change', show); });
  button.onclick = async () => {
    bar.querySelector('.form-error')?.remove();
    if (!changed()) { note.textContent = 'Nothing to save'; return; }
    button.disabled = true;
    try {
      await save();
      saved = inputs.map(value);
      show();
      note.textContent = 'Saved';
    } catch (error) {
      formError(bar, errorText(error));
    } finally {
      button.disabled = false;
    }
  };
  return bar;
}

function card(id, title, lead, ...content) {
  return h('section', { class: 'card settings-card', id: `settings-${id}`, 'aria-labelledby': `settings-${id}-title` },
    h('header', { class: 'settings-card-head' },
      h('h2', { id: `settings-${id}-title` }, title),
      h('p', { class: 'muted' }, lead)),
    content);
}

function hoursCard(s, centreChanged) {
  const target = numberInput(s.default_target_hours, { min: 0, max: 300, step: 0.5 });
  const daily = numberInput(s.default_max_hours_per_day, { min: 0, max: 24, step: 0.5 });
  const prorate = toggle('Lower the target of someone away part of the month', s.prorate_target);
  const centre = textInput(s.centre_name, { maxlength: 120 });
  return card('hours', 'Centre and hours', 'What the program expects from each person. The suggestions and the checks use it.',
    h('div', { class: 'form-grid' },
      field('Target hours a month', target, 'For everyone without a target of their own (set in People).'),
      field('Most hours in one day', daily, 'Going over it shows a warning. A personal limit set in People blocks, unless you assign anyway.')),
    h('div', { class: 'field' }, prorate,
      h('span', { class: 'field-hint toggle-hint' }, 'Someone away for a third of the month is then expected to give a third less.')),
    field('Name of the centre', centre, 'Shown at the top left, in each schedule message and in calendar files.'),
    saveBar([target, daily, prorate.input, centre], async () => {
      await saveSetup('settings/save', { settings: { centre_name: centre.value, default_target_hours: target.value,
        default_max_hours_per_day: daily.value, prorate_target: prorate.input.checked } });
      if (state.boot) state.boot.centre = centre.value.trim();
      centreChanged();
      setSession({ schedules: null });  // the messages name the centre
    }));
}

function messageCard(s) {
  const greeting = textInput(s.message_greeting, { placeholder: 'Hello {name},', maxlength: 120 });
  const closing = textInput(s.message_closing, { placeholder: 'Thank you!', maxlength: 200 });
  const subject = textInput(s.message_subject, { placeholder: 'Your shifts for {month}', maxlength: 120 });
  const previewSubject = h('div', { class: 'mail-preview-subject' });
  const previewBody = h('div', { class: 'mail-preview-body' });
  // The example is written by the program, exactly as a real message would be.
  let wait = 0;
  let asked = 0;
  const show = async () => {
    const mine = (asked += 1);
    try {
      const answer = await api.post('/api/settings/message/preview',
        { message_greeting: greeting.value, message_closing: closing.value, message_subject: subject.value });
      if (mine !== asked) return;  // a later keystroke was answered first
      previewSubject.textContent = answer.subject;
      previewBody.textContent = answer.text;
    } catch { /* the example simply stays as it was */ }
  };
  const later = () => { clearTimeout(wait); wait = setTimeout(show, 250); };
  [greeting, closing, subject].forEach((el) => el.addEventListener('input', later));
  show();
  const element = card('message', 'Schedules by e-mail', 'The message each person gets with their shifts. You copy it into your own e-mail, or Rota Planner sends it.',
    h('div', { class: 'message-layout' },
      h('div', { class: 'form-part' },
        field('First line', greeting, '{name} becomes the person’s first name.'),
        field('Last words', closing),
        field('Subject', subject, '{month} becomes the month. Used when Rota Planner sends the messages.')),
      h('figure', { class: 'mail-preview', 'aria-label': 'Example of the message' },
        h('figcaption', {}, 'What Ana will read'), previewSubject, previewBody)),
    saveBar([greeting, closing, subject], async () => {
      await saveSetup('settings/save', { settings: { message_greeting: greeting.value, message_closing: closing.value, message_subject: subject.value } });
      setSession({ schedules: null });  // the messages are made again with the new words
    }),
    h('div', { class: 'settings-feature', id: 'settings-mail' }, mailLine()));
  element.refresh = show;  // the centre's name is in the example
  return element;
}

function programCard() {
  return card('program', 'The program', 'Which version of Rota Planner this computer has, and getting a newer one.',
    h('div', { class: 'settings-feature', id: 'settings-update' }, updateLine()));
}

function dataCard() {
  return card('data', 'Your data', 'The rota is kept on this computer. Here is how to have it on other computers too, or to keep a copy.',
    h('div', { class: 'settings-feature', id: 'settings-sync' }, syncLine()),
    h('div', { class: 'settings-feature', id: 'settings-backup' }, backupLine()));
}

/** Bring a part into view when a link elsewhere asked for it (settingsLink: 'message', 'mail', 'sync', 'backup', 'update'). */
function focusPart(root) {
  const part = state.session.settingsFocus;
  if (!part) return;
  state.session.settingsFocus = null;  // once only; no redraw needed for this
  const target = root.querySelector(`#settings-${part}`);
  if (!target) return;
  requestAnimationFrame(() => {
    target.scrollIntoView({ block: 'center' });
    target.classList.remove('flash');
    void target.offsetWidth;  // start the mark again if it was just shown
    target.classList.add('flash');
  });
}

export function mountSettings(container) {
  const root = h('div', { class: 'page setup-page settings-page' });
  container.replaceChildren(root);
  let shown = null;

  function render() {
    const setup = state.session.setup;
    // Forms keep what is being typed: only build them once per visit.
    if (setup && !shown) {
      shown = setup;
      const message = messageCard(setup.settings);
      fill(root,
        h('header', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Settings'))),
        hoursCard(setup.settings, () => message.refresh()), message, dataCard(), programCard());
    }
    if (shown) focusPart(root);
  }

  return { render };
}
