// Publish: print or save each document of the month. Every document opens as
// a page that prints well (Print → Save as PDF works everywhere); when Edge,
// Chrome or Chromium is installed, PDFs can also be saved in one click.
import { api, monthPath, printUrl } from '../api.js';
import { changes, chipChoice, formError, openDialog, select, toggle } from '../components.js';
import { fill, formatDate, h, replaceKeepingFocus } from '../dom.js';
import { hint } from '../help.js';
import { icon } from '../icons.js';
import { errorText } from '../setup.js';
import { setMonth, setSession, state } from '../state.js';
import { schedulesFinder } from '../schedules.js';
import { loadMailInfo, openMailer } from '../mailer.js';
import { settingsLink, toast } from '../ui.js';
import { mainRotaCard } from './printtemplates.js';
import { changed, needPen } from '../actions.js';

let checking = false;

async function loadExportsInfo() {
  if (checking) return;
  checking = true;
  try {
    setSession({ exportsInfo: await api.get('/api/exports') });
  } finally {
    checking = false;
  }
}

async function savePdf(doc, options, button) {
  button.disabled = true;
  const label = button.textContent;
  button.textContent = 'Saving…';
  try {
    const result = await api.post(monthPath(state.month.id, '/export'), { doc, options });
    if (!result.saved) {
      window.open(printUrl(state.month.id, doc, options), '_blank');
      toast('No Edge, Chrome or Chromium found: use Print, then “Save as PDF”.');
      return;
    }
    toast(`Saved: ${result.name}`, { actionLabel: 'Open the folder', onAction: openFolder });
  } catch (error) {
    toast(errorText(error), { tone: 'bad' });
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

async function saveFile(doc, button) {
  button.disabled = true;
  try {
    const result = await api.post(monthPath(state.month.id, '/export'), { doc });
    toast(`Saved: ${result.name}`, { actionLabel: 'Open the folder', onAction: openFolder });
  } catch (error) {
    toast(errorText(error), { tone: 'bad' });
  } finally {
    button.disabled = false;
  }
}

/** What there is to print or send, grouped by who it is for. */
const TABS = [
  { id: 'wall', label: 'For the wall', icon: 'calendar', title: 'What is printed and put up',
    lead: 'What is printed and put up. Each document opens ready to print; in the print window, “Save as PDF” makes a file to send (to the manager, for example).' },
  { id: 'people', label: 'For each person', icon: 'users', title: 'Each person’s own schedule',
    lead: 'Each person’s own schedule: as a message, on paper, or in their calendar.' },
  { id: 'files', label: 'Files', icon: 'download', title: 'The month as files',
    lead: 'The month as a spreadsheet, and where saved files are.' },
];

// ---- Versions ---------------------------------------------------------------

let loadingVersions = null;

/** Versions and changes since, for the month as it is now (reloaded after every change). */
async function loadVersions() {
  const month = state.month;
  if (loadingVersions === month) return;
  loadingVersions = month;
  try {
    const info = await api.get(monthPath(month.id, '/versions'));
    setSession({ versions: { of: month, ...info } });
  } finally {
    loadingVersions = null;
  }
}

const AUDIENCE_WORDS = { manager: 'given to the manager to check', residents: 'published for the residents' };

/** A new version: for the manager to check, or published for the residents. */
function publishVersion(info, audience) {
  const next = info.phase.next_number;
  const forManager = audience === 'manager';
  // Comments are still needed while the manager checks; once published, the rota is done.
  const forget = info.comments ? toggle(`Also delete the ${info.comments} availability comment${info.comments > 1 ? 's' : ''} now: they can mention health`
    + (forManager ? '' : ', and the rota is done'), !forManager) : null;
  const dialog = openDialog({ changes: true,
    title: forManager ? `Give version ${next} to the manager to check?` : `Publish version ${next} for the residents?`,
    subtitle: forManager
      ? `Printouts will say “Version ${next} to check”. What you change afterwards is marked ●, so the manager only looks at what moved.`
      : `Printouts will say “Version ${next}”. This is the rota to print and put up; later adjustments are marked ● until the next version.`,
    body: [h('p', {}, forManager ? 'Then print or send the documents below to the manager.' : 'Then print the documents below, and send each person their schedule.'), forget],
    actions: [h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: async () => {
        try {
          needPen();  // another computer edits: say so, send nothing
          await api.post(monthPath(state.month.id, '/versions/publish'), { audience, forget_comments: Boolean(forget?.input.checked) });
          setSession({ availability: null });
          const month = await api.get(monthPath(state.month.id));
          setMonth(month);  // its undo now includes this; versions reload
          dialog.close();
          changed(month.history, month.id, `Version ${next} ${AUDIENCE_WORDS[audience]}${forget?.input.checked ? ' · comments deleted' : ''}`);
        } catch (error) {
          formError(dialog.panel.querySelector('.modal-body'), errorText(error));
        }
      } }, forManager ? `Give version ${next} to the manager` : `Publish version ${next}`)],
  });
}

const STEP_MARK = { done: '✓', skipped: '–' };

/** Where the rota stands, as steps: draft, manager's check, corrections, published. */
function phaseSteps(phase) {
  return h('ol', { class: 'phase-steps', 'aria-label': 'Where the rota stands' }, phase.steps.map((step, i) => h('li', {
    class: ['phase-step', step.status, step.status === 'current' && phase.tone], 'aria-current': step.status === 'current' ? 'step' : null,
  },
  h('span', { class: 'phase-num', 'aria-hidden': 'true' }, STEP_MARK[step.status] || i + 1),
  h('span', { class: 'phase-label' }, step.label,
    step.status === 'skipped' ? h('span', { class: 'faint' }, ' (skipped)') : null,
    step.status === 'done' ? h('span', { class: 'visually-hidden' }, ' (done)') : null))));
}

/** How ready the rota is, in one line: what is filled, open, and still to fix (opens Check). */
function readiness(summary) {
  const coverage = Math.round(summary.coverage * 100);
  const part = (value, label, tone) => h('span', { class: ['ready-part', tone] }, h('strong', {}, value), ` ${label}`);
  return h('a', { class: 'readiness', href: '#/check', title: 'Open Check' },
    part(`${coverage}%`, 'filled', coverage >= 100 ? 'ok' : ''),
    part(String(summary.open), 'open', summary.open ? 'warn' : 'ok'),
    part(String(summary.conflicts), 'to fix', summary.conflicts ? 'bad' : 'ok'),
    h('span', { class: 'ready-go' }, 'Check', icon('right')));
}

/**
 * Where the rota stands: the steps, what it means, the two moves (give to the
 * manager, publish), and how ready it is. Details (changes, versions) fold away.
 */
function versionsCard(info, summary) {
  const { phase } = info;
  // The expected next move is the strong button; the other stays possible.
  const managerNext = phase.state === 'draft';
  const again = phase.state === 'corrections' || phase.state === 'checking';
  const button = (audience, label, primary) => changes(h('button', { class: ['btn', primary && 'primary'], type: 'button',
    onclick: () => publishVersion(info, audience) }, label));
  const count = info.changes.length;
  return h('section', { class: 'card versions-card' },
    h('div', { class: 'card-title' }, h('h2', {}, 'Where the rota stands', hint('versions')), readiness(summary)),
    phaseSteps(phase),
    h('p', { class: ['phase-text', phase.tone === 'warn' && 'warn-text', phase.tone === 'ok' && 'ok-text'] }, phase.text),
    h('div', { class: 'card-actions' },
      button('manager', again ? `Give to the manager again (version ${phase.next_number})…` : `Give to the manager to check (version ${phase.next_number})…`, managerNext),
      button('residents', phase.published ? `Publish an updated version (version ${phase.next_number})…` : `Publish for the residents (version ${phase.next_number})…`,
        !managerNext && phase.state !== 'published')),
    count || info.versions.length ? h('div', { class: 'phase-details' },
      count ? h('details', { class: 'version-history', open: count <= 6 },
        h('summary', {}, `${count} change${count > 1 ? 's' : ''} since version ${phase.version}, marked ● on printouts`),
        h('ul', { class: 'change-list' }, info.changes.slice(0, 40).map((c) => h('li', {},
          h('strong', {}, `${formatDate(c.date)} · ${c.task}`), ' ',
          h('span', {}, c.gone ? `${c.before || 'open'} → no longer needed` : `${c.before || 'open'} → ${c.after || 'open'}`))),
        count > 40 ? h('li', { class: 'faint' }, `and ${count - 40} more`) : null)) : null,
      info.versions.length ? h('details', { class: 'version-history' },
        h('summary', {}, `Versions so far (${info.versions.length})`),
        h('ul', {}, info.versions.map((v) => h('li', {}, `Version ${v.number} · ${AUDIENCE_WORDS[v.audience] || v.audience} · ${formatDate(v.published)}`)))) : null) : null);
}

function openFolder() {
  api.post(monthPath(state.month.id, '/exports/open'), {}).catch((error) => toast(errorText(error), { tone: 'bad' }));
}

/** One document: what it is, its options, and how to get it. */
function docCard({ title, text, doc, options, controls = [], disabled = '' }) {
  const canSave = state.session.exportsInfo?.pdf;
  const saveButton = h('button', { class: 'btn', type: 'button', disabled: Boolean(disabled),
    onclick: (e) => savePdf(doc, options(), e.currentTarget) }, 'Save PDF');
  return h('section', { class: 'card doc-card' },
    h('h2', {}, title),
    h('p', { class: 'muted' }, text),
    controls.length ? h('div', { class: 'doc-options' }, controls) : null,
    disabled ? h('p', { class: 'faint' }, disabled) : null,
    h('div', { class: 'card-actions' },
      h('button', { class: 'btn primary', type: 'button', disabled: Boolean(disabled),
        onclick: () => window.open(printUrl(state.month.id, doc, options()), '_blank') }, icon('maximize'), 'Open to print'),
      canSave ? saveButton : null));
}

/** The option controls of the document cards, for one month. */
function makeControls(month, shape) {
  // Event rotas (an event can run into the next month: it is printed whole)
  const events = [...month.events].sort((a, b) => Number(b.big) - Number(a.big) || a.start.localeCompare(b.start));
  const withShifts = month.people.filter((p) => p.hours > 0 || p.active).sort((a, b) => a.name.localeCompare(b.name));
  return {
    shape,
    eventChoice: events.length ? select(events.map((e) => ({ value: e.id,
      label: `${e.name} · ${formatDate(e.start)}${e.end !== e.start ? ` – ${formatDate(e.end)}` : ''}` })), events[0].id) : null,
    perPage: toggle('One day per page', true),
    eventTimes: toggle('Times', true),
    areas: chipChoice(month.areas.map((a) => ({ value: a.id, label: a.name })), month.areas.map((a) => a.id), { label: 'Areas' }),
    weekTimes: toggle('Times under each task', true),
    onlyWanted: toggle('Only the highlighted shifts', false),
    who: select([{ value: '', label: 'Everyone with shifts' }, ...withShifts.map((p) => ({ value: p.id, label: p.name }))], ''),
    perPerson: toggle('One person per page', false),
  };
}

export function mountPublish(container) {
  const root = h('div', { class: 'page setup-page publish-page' });
  container.replaceChildren(root);
  const finder = schedulesFinder();  // kept across redraws: what was typed stays
  let controls = null;
  if (!state.session.exportsInfo) loadExportsInfo().catch(() => {});
  loadMailInfo();  // optional e-mail: is it set up?

  function render() {
    const month = state.month;
    if (!month) {
      fill(root, h('p', { class: 'empty' }, 'Create the month first, in Set up.'));
      return;
    }
    const s = month.summary;

    // The options of each document are made once per month (and its events,
    // areas, people): what was chosen stays when the screen is drawn again.
    const shape = [month.id, month.events.map((e) => e.id), month.areas.map((a) => a.id), month.people.length].join('|');
    if (!controls || controls.shape !== shape) controls = makeControls(month, shape);
    const { eventChoice, perPage, eventTimes, areas, weekTimes, onlyWanted, who, perPerson } = controls;
    const flag = (on) => (on ? '1' : '0');

    if (state.session.versions?.of !== month) loadVersions().catch(() => {});
    const versionsInfo = state.session.versions?.of.id === month.id ? state.session.versions : null;

    finder.refresh();
    const tab = TABS.some((t) => t.id === state.session.publishTab) ? state.session.publishTab : 'wall';
    const phase = versionsInfo?.phase || month.phase;
    // Said on every tab: what the printouts will carry, so a check copy is never taken for the final one.
    const stampNote = !phase ? null : h('p', { class: ['stamp-note', !phase.published && 'warn'] }, icon('info'),
      `Printed now, it says “${phase.print_says}”`,
      phase.changes ? `, with ● on the ${phase.changes} change${phase.changes > 1 ? 's' : ''} made since.` : '.',
      !phase.published ? ' It is not the published rota yet.' : '');

    const wall = () => [
      state.session.exportsInfo ? mainRotaCard(month, state.session.exportsInfo) : null,
      h('div', { class: 'cards doc-cards' },
        docCard({ title: 'Event rota', doc: 'event',
          text: 'One event day by day, grouped by area, with its own tasks. An event running into the next month is printed whole.',
          controls: eventChoice ? [eventChoice, perPage, eventTimes] : [], disabled: eventChoice ? '' : 'No event this month.',
          options: () => ({ event: eventChoice?.value || '', per_page: flag(perPage.input.checked), times: flag(eventTimes.input.checked) }) }),
        docCard({ title: 'Weekly sheets by area', doc: 'weekly',
          text: 'One page per area and week, in large print, to pin up where the work is done.',
          controls: [areas, weekTimes],
          options: () => ({ areas: areas.getValue().length === month.areas.length ? '' : areas.getValue().join(','), times: flag(weekTimes.input.checked) }) }),
        docCard({ title: 'Shifts to sign up for', doc: 'open',
          text: `The ${s.open} open shifts, day by day, with lines to write a name on.`,
          controls: [onlyWanted], options: () => ({ wanted: flag(onlyWanted.input.checked) }) }))];

    const mailInfo = state.session.mailInfo;
    const people = () => [
      phase && !phase.published ? h('div', { class: 'notice warn' }, icon('alert'),
        h('span', {}, 'The rota is not published yet: what you send or hand out now may still change.')) : null,
      mailInfo?.configured ? h('section', { class: 'card mail-card' },
        h('div', { class: 'card-title' },
          h('div', {}, h('h2', {}, 'E-mail everyone their schedule'),
            h('p', { class: 'muted' }, `From ${mailInfo.address}: one message per person, with their shifts and their calendar file. You give the list of addresses; none is kept.`)),
          changes(h('button', { class: 'btn primary', type: 'button', onclick: openMailer }, icon('message'), 'Send by e-mail…'))))
        : h('p', { class: 'faint mail-off' }, ['Sending by e-mail from Rota Planner is not set up (optional). ', settingsLink('mail', 'Settings › Schedules by e-mail')]),
      h('section', { class: 'card schedules-card' },
        h('div', { class: 'card-title' },
          h('div', {}, h('h2', {}, mailInfo?.configured ? 'Or one person at a time' : 'Send each person their schedule'),
            h('p', { class: 'muted' }, 'Type a few letters, Enter copies the text, paste it in your e-mail; then the next name. Also from the top bar (key S). ', settingsLink('message', 'Change the words')))),
        finder),
      h('div', { class: 'cards doc-cards' },
        docCard({ title: 'Personal rotas', doc: 'people',
          text: 'Each person’s own shifts with times and hours, several to a page: to hand out.',
          controls: [who, perPerson], options: () => ({ person: who.value, per_page: flag(perPerson.input.checked) }) }),
        h('section', { class: 'card doc-card' },
          h('h2', {}, 'Calendar files'),
          h('p', { class: 'muted' }, 'One file per person with their shifts, to open on a phone or computer: the shifts go into their own calendar.'),
          h('div', { class: 'card-actions' },
            h('button', { class: 'btn primary', type: 'button', onclick: (e) => saveFile('calendars', e.currentTarget) }, icon('download'), 'Save all calendar files'))))];

    const files = () => [
      h('div', { class: 'cards doc-cards' },
        h('section', { class: 'card doc-card' },
          h('h2', {}, 'Excel file'),
          h('p', { class: 'muted' }, 'The month as a spreadsheet, like the sheet used so far: grey where nobody is needed. In Google Sheets: File › Import › Upload.'),
          h('div', { class: 'card-actions' },
            h('button', { class: 'btn primary', type: 'button', onclick: (e) => saveFile('xlsx', e.currentTarget) }, icon('download'), 'Save the Excel file'))),
        h('section', { class: 'card doc-card' },
          h('h2', {}, 'Saved files'),
          h('p', { class: 'muted' }, 'PDFs, the Excel file and calendar files you save go in the exports folder of the data folder, on this computer.'),
          h('div', { class: 'card-actions' },
            h('button', { class: 'btn', type: 'button', onclick: openFolder }, 'Open the folder'))))];

    const content = { wall, people, files }[tab]();
    replaceKeepingFocus(root,
      h('header', { class: 'page-head compact' },
        h('h1', {}, `Publish ${month.title}`),
        h('div', { class: 'page-actions' }, h('a', { class: 'btn', href: '#/check' }, icon('check'), 'Check first'))),
      versionsInfo ? versionsCard(versionsInfo, s) : null,
      h('div', { class: 'publish-bar' },
        h('div', { class: 'segmented page-tabs publish-tabs', role: 'tablist', 'aria-label': 'What to print or send' },
          TABS.map((t) => h('button', { type: 'button', role: 'tab', 'aria-selected': String(t.id === tab), 'aria-pressed': String(t.id === tab),
            title: t.title, onclick: () => setSession({ publishTab: t.id }) }, icon(t.icon), t.label))),
        stampNote),
      h('p', { class: 'muted tab-lead' }, TABS.find((t) => t.id === tab).lead),
      content);
  }

  return { render };
}
