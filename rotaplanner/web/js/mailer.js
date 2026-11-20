// Optional: e-mailing each person their schedule (mail.py decides everything;
// this shows it). The account card for Settings, and the send window for Publish.
// The list of addresses is only held while the window is open, and is sent to
// the program on this computer only.
import { api, monthPath } from './api.js';
import { badge, field, formError, openDialog, readFileBase64, segmented, textInput } from './components.js';
import { fill, h } from './dom.js';
import { icon } from './icons.js';
import { errorText } from './setup.js';
import { setSession, state } from './state.js';
import { toast } from './ui.js';

/** Whether e-mail is set up, for the screens that offer it (asked once per visit). */
export async function loadMailInfo() {
  try {
    setSession({ mailInfo: await api.get('/api/mail') });
  } catch {
    setSession({ mailInfo: { configured: false } });
  }
}

// ---- Settings › E-mail (optional) ---------------------------------------------

export function mailCard() {
  const card = h('section', { class: 'card' }, h('h2', {}, 'E-mail (optional)'), h('p', { class: 'muted' }, 'Loading…'));

  const show = (info, problem = '') => {
    setSession({ mailInfo: info });
    if (info.elsewhere) {
      fill(card, h('h2', {}, 'E-mail (optional)'),
        h('p', { class: 'muted' }, 'Sending schedules by e-mail is set up and used on the computer where Rota Planner runs.'));
      return;
    }
    const address = textInput(info.address, { type: 'email', placeholder: 'rota@example.org', autocomplete: 'off' });
    const name = textInput(info.name, { placeholder: 'Rota – name of the centre', autocomplete: 'off' });
    const password = h('input', { class: 'input', type: 'password', autocomplete: 'off',
      placeholder: info.has_password ? 'Kept · paste a new one to replace it' : 'abcd efgh ijkl mnop' });
    const host = textInput(info.host, { autocomplete: 'off', spellcheck: false });
    const port = h('input', { class: 'input num', type: 'number', min: 1, max: 65535, value: info.port });
    const result = h('div', { class: 'form-part' });
    const account = () => ({ address: address.value, name: name.value, password: password.value, host: host.value, port: port.value });
    const run = async (path, body, done) => {
      try {
        const answer = await api.post(path, body);
        done(answer);
      } catch (error) {
        formError(result, errorText(error));
      }
    };
    const test = h('button', { class: 'btn primary', type: 'button', onclick: async () => {
      test.disabled = true;
      fill(result, h('p', { class: 'muted' }, 'Connecting to the mail server…'));
      try {
        await api.post('/api/mail/save', { account: account() });
        const answer = await api.post('/api/mail/test', {});
        toast(`It works: a test message was sent to ${answer.sent_to}`);
        show(answer);
      } catch (error) {
        // The account may be saved even though the test failed: show it as it is (with Remove), and why.
        let now = info;
        try { now = await api.get('/api/mail'); } catch { /* keep what was shown */ }
        show(now, errorText(error));
      }
    } }, info.configured ? 'Save and test again' : 'Save and send a test');
    fill(card, h('h2', {}, 'E-mail (optional)'),
      info.configured
        ? h('p', { class: 'ok-text' }, `On: schedules can be sent from ${info.address} (Publish › For each person).`)
        : h('p', { class: 'muted' }, 'Off. Rota Planner can send each person their schedule, with their calendar file, from a Gmail or Google Workspace account. '
          + 'Without it, nothing changes: you copy each schedule into your own e-mail as before.'),
      !info.configured ? h('ol', { class: 'steps-list' },
        h('li', {}, 'The account needs 2-Step Verification.'),
        h('li', {}, 'Signed in to it, open myaccount.google.com/apppasswords and create an app password (a 16-letter code). '
          + 'If that page is not offered, the organisation does not allow app passwords: e-mail then stays off.'),
        h('li', {}, 'Type the address and paste the code here, then “Save and send a test”.')) : null,
      h('div', { class: 'form-grid' },
        field('Address that sends', address), field('Name shown to people', name)),
      field('App password', password, 'Kept on this computer only, never shown again, never in exports or the shared folder.'),
      h('details', { class: 'version-history' }, h('summary', {}, 'Mail server (only for another provider than Gmail)'),
        h('div', { class: 'form-grid' }, field('Server', host), field('Port', port, '587, or 465'))),
      h('p', { class: 'faint' }, 'When you send, names and shifts travel through this mail provider. Addresses of people are never kept: you give the list each time.'),
      result,
      !info.configured && info.has_password ? h('p', { class: 'warn-text' }, 'An account is saved but has not worked yet: test it again, or remove it.') : null,
      h('div', { class: 'card-actions' }, test,
        info.has_password ? h('button', { class: 'btn ghost danger', type: 'button',
          onclick: () => run('/api/mail/remove', {}, (answer) => { toast('E-mail turned off'); show(answer); }) }, 'Remove') : null));
    if (problem) formError(result, problem);
  };

  api.get('/api/mail').then((info) => show(info)).catch((error) => formError(card, errorText(error)));
  return card;
}

// ---- Publish › E-mail everyone their schedule ------------------------------------

const MATCH_WORDS = {
  recognised: ['Recognised', 'ok'], name: ['By name', 'ok'], check: ['Check', 'warn'],
  unknown: ['Not found', 'bad'], duplicate: ['Twice', ''],
};
const WHO = [
  { value: 'all', label: 'Everyone with shifts' },
  { value: 'changed', label: 'Only where shifts changed' },
  { value: 'new', label: 'Only those not e-mailed yet' },
];

export function openMailer() {
  const month = state.month;
  const body = h('div', { class: 'mailer' });
  const actions = h('div', { class: 'mailer-actions' });
  let timer = 0;
  let sending = false;
  const dialog = openDialog({
    title: `E-mail the schedules of ${month.title}`, wide: true,
    subtitle: 'One message per person, with their calendar file. Nothing is sent before the last step.',
    body: [body], actions: [actions],
    onClose: () => { clearInterval(timer); if (sending) toast('The sending goes on; see Publish for the result.'); },
  });
  const buttons = (...items) => fill(actions, h('span', { class: 'spacer' }), ...items);
  const cancel = () => h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Close');

  // Step 1: the list of names and addresses
  const text = h('textarea', { class: 'input textarea', rows: 7,
    placeholder: 'Paste names and addresses: a table copied from a sheet (the availability form’s replies work as they are),\nor one per line, such as: Ana Berg <ana@example.org>' });
  const file = h('input', { type: 'file', accept: '.csv,.tsv,.txt,.xlsx', class: 'file' });
  let list = null;  // what was given, sent again with the last step

  const stepList = () => {
    fill(body,
      h('p', { class: 'muted' }, 'Rota Planner keeps no address. Give the list now: each address is recognised by its fingerprint, used to send, then forgotten.'),
      field('Names and addresses', text), field('Or a file (CSV or Excel)', file));
    buttons(cancel(), h('button', { class: 'btn primary', type: 'button', onclick: check }, 'Check the list'));
  };

  const check = async () => {
    list = { text: text.value };
    if (file.files[0]) {
      list.file = await readFileBase64(file.files[0]);
      list.filename = file.files[0].name;
    }
    try {
      stepCheck(await api.post(monthPath(month.id, '/mail/preview'), list));
    } catch (error) {
      formError(body, errorText(error));
    }
  };

  // Step 2: who each address is, and who to write to
  const stepCheck = (preview, kept = null) => {
    const rows = kept || preview.rows.map((row) => ({ ...row }));  // coming back keeps what was chosen
    let who = 'all';
    const summary = h('div', { class: 'mailer-summary' });
    const go = h('button', { class: 'btn primary', type: 'button' });

    const picked = () => rows.filter((r) => r.send && r.person_id).map((r) => ({ row: r.row, person_id: r.person_id, remember: Boolean(r.remember) }));
    // How many messages and who is missing are worked out by the program, for what is ticked now.
    let asked = 0;
    const refresh = async () => {
      const mine = (asked += 1);
      go.disabled = true;
      let plan;
      try {
        plan = await api.post(monthPath(month.id, '/mail/plan'), { rows: picked(), who });
      } catch (error) {
        if (mine === asked) fill(summary, h('p', { class: 'form-error' }, errorText(error)));
        return;
      }
      if (mine !== asked) return;  // a newer choice is being worked out
      fill(summary,
        h('p', {}, h('strong', {}, `${plan.count} message${plan.count === 1 ? '' : 's'} to send`),
          plan.no_shift ? h('span', { class: 'muted' }, ` · ${plan.no_shift} in the list have no shift this month`) : null),
        plan.missing.length ? h('p', { class: 'warn-text' }, `Not in the list (${plan.missing.length}): ${plan.missing.slice(0, 30).join(', ')}${plan.missing.length > 30 ? '…' : ''}. `
          + 'Add them to the list, or send theirs by hand.') : h('p', { class: 'ok-text' }, 'Everyone concerned is in the list.'));
      go.textContent = `Next: send ${plan.count} message${plan.count === 1 ? '' : 's'}`;
      go.disabled = !plan.count;
      go.onclick = () => stepConfirm(preview, rows, who, plan.count);
    };

    const personSelect = (row, cell, keep) => h('select', { class: 'select', 'aria-label': `Person for ${row.name || row.address}`,
      onchange: (e) => {
        row.person_id = e.target.value || null;
        row.send = Boolean(row.person_id);
        row.remember = false;  // a person chosen by hand: remembering the address is a choice to make
        cell.checked = row.send;
        keep.checked = false;
        refresh();
      } },
    h('option', { value: '' }, '— nobody —'),
    row.suggestions.length ? h('optgroup', { label: 'Suggested' }, row.suggestions.map((id) => h('option', { value: id, selected: id === row.person_id },
      preview.everyone.find((p) => p.id === id)?.name || id))) : null,
    h('optgroup', { label: 'Everyone' }, preview.everyone.map((p) => h('option', { value: p.id, selected: !row.suggestions.length && p.id === row.person_id }, p.name))));

    const counts = { recognised: 0, name: 0, check: 0, unknown: 0, duplicate: 0 };
    rows.forEach((r) => { counts[r.match] += 1; });
    const attention = rows.filter((r) => r.match !== 'recognised');
    const table = (list_) => h('div', { class: 'table-scroll' }, h('table', { class: 'table compact' },
      h('thead', {}, h('tr', {}, h('th', {}, 'Send'), h('th', {}, 'In the list'), h('th', {}, 'Address'), h('th', {}, 'Person'), h('th', {}, 'Remember the address'))),
      h('tbody', {}, list_.map((row) => {
        const tick = h('input', { type: 'checkbox', checked: row.send, 'aria-label': `Send to ${row.name || row.address}`,
          onchange: (e) => { row.send = e.target.checked && Boolean(row.person_id); e.target.checked = row.send; refresh(); } });
        const keep = h('input', { type: 'checkbox', checked: row.remember,
          'aria-label': `Remember the address of ${row.name || row.address}`, onchange: (e) => { row.remember = e.target.checked; } });
        return h('tr', {},
          h('td', {}, tick),
          h('td', {}, row.name || '—', ' ', badge(...MATCH_WORDS[row.match])),
          h('td', { class: 'muted' }, row.address),
          h('td', {}, row.match === 'recognised' ? preview.everyone.find((p) => p.id === row.person_id)?.name : personSelect(row, tick, keep)),
          h('td', {}, row.match === 'recognised' ? h('span', { class: 'faint' }, 'Known') : keep));
      }))));

    fill(body,
      h('p', { class: 'muted' }, `${rows.length} addresses: ${counts.recognised} recognised, ${counts.name} found by name, ${counts.check} to check, ${counts.unknown} not found`
        + (counts.duplicate ? `, ${counts.duplicate} given twice` : '') + '.'),
      attention.length ? [h('h3', { class: 'form-section' }, 'To look at'), table(attention)] : null,
      counts.recognised ? h('details', { class: 'version-history' }, h('summary', {}, `The ${counts.recognised} recognised`), table(rows.filter((r) => r.match === 'recognised'))) : null,
      h('h3', { class: 'form-section' }, 'Who to write to'),
      segmented(WHO, who, { label: 'Who to write to', onchange: (value) => { who = value; refresh(); } }),
      summary);
    buttons(h('button', { class: 'btn', type: 'button', onclick: stepList }, 'Back'), cancel(), go);
    refresh();
  };

  // Step 3: the last look before sending
  const stepConfirm = (preview, rows, who, count) => {
    const { phase } = preview;
    fill(body,
      h('p', {}, h('strong', {}, `${count} message${count === 1 ? '' : 's'}`), ` will be sent from ${state.session.mailInfo?.address || 'the account'}, each with the person’s shifts and their calendar file.`),
      !phase.published ? h('div', { class: 'notice warn' }, icon('alert'),
        h('span', {}, `The rota is not published yet (printouts say “${phase.print_says}”): what people receive may still change.`)) : null,
      phase.published && phase.changes ? h('p', { class: 'muted' }, `${phase.changes} change${phase.changes === 1 ? '' : 's'} since version ${phase.version}: changed shifts are marked “(changed)” in the messages.`) : null,
      h('p', { class: 'muted' }, 'A sent message cannot be taken back.'));
    buttons(h('button', { class: 'btn', type: 'button', onclick: () => stepCheck(preview, rows) }, 'Back'), cancel(),
      h('button', { class: 'btn primary', type: 'button', onclick: async (e) => {
        e.currentTarget.disabled = true;
        try {
          const sendRows = rows.filter((r) => r.send && r.person_id).map((r) => ({ row: r.row, person_id: r.person_id, remember: Boolean(r.remember) }));
          const status = await api.post(monthPath(month.id, '/mail/send'), { ...list, rows: sendRows, who });
          list = null;
          text.value = '';
          stepProgress(status);
        } catch (error) {
          e.currentTarget.disabled = false;
          formError(body, errorText(error));
        }
      } }, icon('message'), `Send ${count} message${count === 1 ? '' : 's'} now`));
  };

  // Step 4: progress, then the report
  const stepProgress = (status) => {
    sending = true;
    const draw = (s) => {
      const finished = s.sent + s.failed.length;
      fill(body,
        h('p', {}, s.done ? h('strong', {}, `${s.sent} of ${s.total} sent.`) : `Sending… ${finished} of ${s.total}`),
        h('div', { class: 'progress', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': String(s.total), 'aria-valuenow': String(finished) },
          h('span', { style: { width: `${s.total ? Math.round((finished / s.total) * 100) : 0}%` } })),
        s.problem ? h('p', { class: 'form-error' }, s.problem) : null,
        s.done && s.stopped ? h('p', { class: 'warn-text' }, `Stopped: ${s.total - finished} not sent.`) : null,
        s.failed.length ? [h('h3', { class: 'form-section' }, `Not sent (${s.failed.length})`),
          h('ul', { class: 'summary-list' }, s.failed.map((f) => h('li', {}, h('strong', {}, f.name), ` · ${f.reason}`)))] : null,
        s.done && !s.failed.length && !s.problem && !s.stopped ? h('p', { class: 'ok-text' }, 'Everyone in the list has their schedule.') : null);
      if (s.done) {
        sending = false;
        clearInterval(timer);
        buttons(cancel());
        loadMailInfo();
      }
    };
    buttons(h('button', { class: 'btn', type: 'button', onclick: () => api.post('/api/mail/stop', {}).catch(() => {}) }, 'Stop'));
    draw(status);
    timer = setInterval(async () => {
      try {
        draw(await api.get('/api/mail/status'));
      } catch { /* the next tick tries again */ }
    }, 700);
  };

  stepList();
}
