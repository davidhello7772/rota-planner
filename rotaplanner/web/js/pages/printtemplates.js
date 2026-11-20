// Publish › Main rota: saved ways of printing it (templates). Each says the
// paper (A3, A2 or A1 — those two on A3 sheets to join), which areas, and a
// few options; printing is one click per template.
import { api, monthPath, printUrl } from '../api.js';
import { chipChoice, field, formError, openDialog, segmented, select, textInput, toggle } from '../components.js';
import { h } from '../dom.js';
import { icon } from '../icons.js';
import { errorText } from '../setup.js';
import { setSession, state } from '../state.js';
import { toast } from '../ui.js';

const SIZE_CHOICES = [
  { value: 'a3', label: 'A3' }, { value: 'a2', label: 'A2' }, { value: 'a1', label: 'A1' }, { value: 'a4x2', label: 'A4 ×2' },
];
const BIG = new Set(['a2', 'a1']);

const cm = (mm) => Math.round(mm / 10);

/** What the paper will be, in plain words. */
function paperText(template, layouts) {
  const layout = layouts[template.size][BIG.has(template.size) ? template.output : 'one_page'];
  const size = `about ${cm(layout.w)} × ${cm(layout.h)} cm`;
  if (template.size === 'a4x2') return 'Two A4 pages: days 1–16, then 17–31';
  if (template.size === 'a3') return `The whole month on one A3 sheet (${size})`;
  const name = template.size.toUpperCase();
  return template.output === 'one_page' ? `One ${name} page, for a print shop (${size})`
    : `${name} on ${layout.cols * layout.rows} A3 sheets to tape together (${size})`;
}

/** A small drawing of the sheets that will come out of the printer. */
function paperDrawing(template) {
  const tiled = BIG.has(template.size) && template.output === 'a3_sheets';
  const kind = tiled ? `${template.size}-sheets` : template.size === 'a4x2' ? 'a4x2' : `${template.size}-page`;
  const count = { 'a2-sheets': 2, 'a1-sheets': 4, a4x2: 2 }[kind] || 1;
  return h('span', { class: ['paper', `paper-${kind}`], 'aria-hidden': 'true' },
    Array.from({ length: count }, () => h('span', {})),
    !tiled && BIG.has(template.size) ? h('b', {}, template.size.toUpperCase()) : null);
}

async function send(path, body, message) {
  const info = await api.post(`/api/${path}`, body);
  setSession({ exportsInfo: info });
  if (message) toast(message);
  return info;
}

export function editTemplate(template, info, areas) {
  const t = template || { name: '', size: 'a3', output: 'a3_sheets', area_ids: [], show_times: true, event_tasks: 'events',
    tasks_at_end: false, repeat_days: false };
  let size = t.size;
  let output = t.output;
  const name = textInput(t.name, { autofocus: true, placeholder: 'e.g. Kitchen + Dining room + Cafe' });
  const explain = h('p', { class: 'field-hint size-hint' });
  const outputChoice = segmented([{ value: 'a3_sheets', label: 'On A3 sheets to join' }, { value: 'one_page', label: 'One big page' }], output,
    { label: 'How to print it', onchange: (value) => { output = value; update(); } });
  const outputField = h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Printed'), outputChoice);
  const sizeChoice = segmented(SIZE_CHOICES, size, { label: 'Paper', onchange: (value) => { size = value; update(); } });
  const drawing = h('span', { class: 'paper-slot' });
  function update() {
    explain.textContent = paperText({ size, output }, info.layouts);
    outputField.hidden = !BIG.has(size);
    drawing.replaceChildren(paperDrawing({ size, output }));
  }
  const chosen = t.area_ids.length ? t.area_ids : areas.map((a) => a.id);
  const areaChips = chipChoice(areas.map((a) => ({ value: a.id, label: a.name })), chosen, { label: 'Areas' });
  const allNone = h('span', { class: 'chip-tools' },
    h('button', { class: 'link', type: 'button', onclick: () => areaChips.setValue(areas.map((a) => a.id)) }, 'All'), ' · ',
    h('button', { class: 'link', type: 'button', onclick: () => areaChips.setValue([]) }, 'None'));
  const times = toggle('Times under each task', t.show_times);
  const atEnd = toggle('Tasks and times again after the last day', t.tasks_at_end);
  const repeatDays = toggle('Day numbers again on each area', t.repeat_days);
  const eventTasks = select(Object.entries(info.event_tasks).map(([value, label]) => ({ value, label })), t.event_tasks);
  update();

  const save = async () => {
    if (!areaChips.getValue().length) {
      formError(dialog.panel.querySelector('.modal-body'), 'Choose at least one area.');
      return;
    }
    try {
      await send('print-templates/save', { template: { id: t.id, name: name.value, size, output, area_ids: areaChips.getValue(),
        show_times: times.input.checked, event_tasks: eventTasks.value, tasks_at_end: atEnd.input.checked,
        repeat_days: repeatDays.input.checked } }, template ? 'Template saved' : 'Template added');
      dialog.close();
    } catch (error) {
      formError(dialog.panel.querySelector('.modal-body'), errorText(error));
    }
  };
  const dialog = openDialog({
    title: template ? template.name : 'New print template', wide: true,
    subtitle: 'Kept for every month. The grid is sized to fill the paper exactly.',
    body: [field('Name', name),
      h('div', { class: 'size-row' }, h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Paper'), sizeChoice, explain), drawing),
      outputField,
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Areas ', allNone), areaChips),
      h('div', { class: 'form-grid' }, times, field('Tasks of events printed', eventTasks, 'Each event says whether its tasks are printed on the main rota.')),
      h('div', { class: 'form-grid' }, atEnd, repeatDays),
      h('p', { class: 'field-hint' }, 'For wide printouts read from both sides, and posters cut into sheets: every part keeps its task names and day numbers.')],
    actions: [template ? h('button', { class: 'btn ghost danger', type: 'button', onclick: async () => {
      await send('print-templates/delete', { id: template.id }, `${template.name} removed`);
      dialog.close();
    } }, 'Remove') : null,
    h('span', { class: 'spacer' }),
    h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
    h('button', { class: 'btn primary', type: 'button', onclick: save }, template ? 'Save' : 'Add')],
  });
}

/** Save a template's printout as PDF in the exports folder (when Edge/Chrome/Chromium is there). */
async function savePdf(template, button) {
  button.disabled = true;
  try {
    const result = await api.post(monthPath(state.month.id, '/export'), { doc: 'month', options: { template: template.id } });
    if (!result.saved) {
      window.open(printUrl(state.month.id, 'month', { template: template.id }), '_blank');
      toast('No Edge, Chrome or Chromium found: use Print, then “Save as PDF”.');
      return;
    }
    toast(`Saved: ${result.name}`);
  } catch (error) {
    toast(errorText(error), { tone: 'bad' });
  } finally {
    button.disabled = false;
  }
}

export function mainRotaCard(month, info) {
  const areas = month.areas;
  const areaNames = new Map(areas.map((a) => [a.id, a]));
  const row = (template) => {
    const chosen = template.area_ids.map((id) => areaNames.get(id)).filter(Boolean);
    return h('li', { class: 'template-row' },
      paperDrawing(template),
      h('div', { class: 'template-text' },
        h('strong', {}, template.name),
        h('span', { class: 'muted' }, paperText(template, info.layouts)),
        h('span', { class: 'template-areas' },
          chosen.length ? chosen.map((a) => h('span', { class: ['area-chip', `area-${a.color}`] }, a.name))
            : h('span', { class: 'faint' }, 'All areas'),
          !template.show_times ? h('span', { class: 'faint' }, ' · no times') : null,
          template.tasks_at_end ? h('span', { class: 'faint' }, ' · tasks on both sides') : null,
          template.repeat_days ? h('span', { class: 'faint' }, ' · day numbers repeated') : null,
          template.event_tasks !== 'events' ? h('span', { class: 'faint' }, ` · tasks of events: ${info.event_tasks[template.event_tasks].toLowerCase()}`) : null)),
      h('div', { class: 'template-actions' },
        h('button', { class: 'btn primary small', type: 'button',
          onclick: () => window.open(printUrl(month.id, 'month', { template: template.id }), '_blank') }, icon('maximize'), 'Open to print'),
        info.pdf ? h('button', { class: 'btn small', type: 'button', onclick: (e) => savePdf(template, e.currentTarget) }, 'Save PDF') : null,
        h('button', { class: 'btn ghost icon-only small', type: 'button', 'aria-label': `Change ${template.name}`, title: 'Change',
          onclick: () => editTemplate(template, info, areas) }, icon('sliders')),
        h('button', { class: 'btn ghost small', type: 'button', title: 'Make a copy to change',
          onclick: () => send('print-templates/duplicate', { id: template.id }, 'Template duplicated').catch((e) => toast(errorText(e), { tone: 'bad' })) }, 'Duplicate')));
  };
  return h('section', { class: 'card main-rota-card' },
    h('div', { class: 'card-title' },
      h('div', {}, h('h2', {}, 'Main rota'),
        h('p', { class: 'muted' }, 'Tasks down, days across, sized to fill the paper. Grey: not needed · yellow: please sign up.')),
      h('button', { class: 'btn', type: 'button', onclick: () => editTemplate(null, info, areas) }, icon('plus'), 'New print template')),
    info.templates.length ? h('ul', { class: 'template-list' }, info.templates.map(row))
      : h('p', { class: 'faint' }, 'No template: add one to choose the paper and the areas.'));
}
