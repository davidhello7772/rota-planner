// The print page. The main rota comes as "posters" (see exporters/documents.py):
// each is scaled so the grid fills its paper exactly, and A2/A1 posters are
// cut into A3 sheets that overlap, to trim and join. On screen, everything is
// shrunk to fit the window (not when printing). The Print button opens the
// browser's print dialog. Also put inside the files turned into PDF.
(() => {
  // The address carried the session token to get here: take it out of the
  // address bar (and of what the browser may print in the page's footer).
  if (window.location.protocol !== 'file:') {
    const url = new URL(window.location.href);
    if (url.searchParams.has('t')) {
      url.searchParams.delete('t');
      window.history.replaceState(null, '', url.pathname + url.search);
    }
  }
  const MM = 96 / 25.4;   // CSS pixels in a millimetre
  const LABEL_MM = 6;     // band under each A3 sheet for its label (documents.LABEL_MM)
  const SAFETY = 0.985;   // keep clear of rounding at the edges
  const TASK_COLUMN_MM = 36;  // as in print.css
  const DAY_COLUMN_MM = 14.5; // narrowest day column where "Quinn J." still fits

  /**
   * Largest scale at which the grid, laid out `width / scale` wide, is at most
   * `height` tall (mm). The layout is never narrower than what keeps names whole:
   * with few tasks the grid then simply does not fill the whole height.
   */
  function fitScale(content, width, height) {
    const days = content.querySelectorAll('thead tr.days th.day').length;
    const taskColumns = content.querySelectorAll('col.c-task').length || 1;  // two when the tasks are repeated at the end
    let low = 0.2;
    let high = Math.min(1.8, width / (taskColumns * TASK_COLUMN_MM + days * DAY_COLUMN_MM));
    for (let i = 0; i < 18; i += 1) {
      const scale = (low + high) / 2;
      content.style.setProperty('width', `${width / scale}mm`);
      if ((content.scrollHeight / MM) * scale <= height) low = scale; else high = scale;
    }
    content.style.setProperty('width', `${width / low}mm`);
    return low * SAFETY;
  }

  const mm = (value) => `${value}mm`;
  const NAMES = { '2x1': ['left', 'right'], '2x2': ['top left', 'top right', 'bottom left', 'bottom right'] };

  function tileSheets(poster, content) {
    const d = poster.dataset;
    const [cols, rows, tileW, tileH, overlap] = [d.cols, d.rows, d.tileW, d.tileH, d.overlap].map(Number);
    const count = cols * rows;
    const names = NAMES[`${cols}x${rows}`] || [];
    const group = document.createElement('div');
    group.className = 'tiles screen-fit';
    group.style.setProperty('--cols', String(cols));
    for (let row = 0; row < rows; row += 1) {
      for (let col = 0; col < cols; col += 1) {
        const number = row * cols + col + 1;
        const tile = document.createElement('section');
        tile.className = `tile page-${d.page}`;
        tile.style.setProperty('width', mm(tileW));
        tile.style.setProperty('height', mm(tileH + LABEL_MM - 0.6));
        const view = document.createElement('div');
        view.className = 'tile-view';
        view.style.setProperty('height', mm(tileH));
        const shift = document.createElement('div');
        shift.className = 'tile-shift';
        shift.style.setProperty('left', mm(-col * (tileW - overlap)));
        shift.style.setProperty('top', mm(-row * (tileH - overlap)));
        shift.append(content.cloneNode(true));
        view.append(shift);
        // Where the next sheet goes: a shaded strip with a dashed edge.
        if (col < cols - 1) view.append(mark('join join-right', { left: mm(tileW - overlap), width: mm(overlap) }));
        if (row < rows - 1) view.append(mark('join join-bottom', { top: mm(tileH - overlap), height: mm(overlap) }));
        const label = document.createElement('div');
        label.className = 'tile-label';
        label.textContent = `Sheet ${number} of ${count}${names[number - 1] ? ` · ${names[number - 1]}` : ''}`
          + (number > 1 ? ' — cut off the white margin on the side that joins, and lay it on the shaded strip of the sheet next to it.'
            : ' — the other sheets are laid on the shaded strips.');
        tile.append(view, label);
        group.append(tile);
      }
    }
    poster.classList.add('tiled');
    poster.after(group);
  }

  function mark(className, positions) {
    const div = document.createElement('div');
    div.className = className;
    for (const [key, value] of Object.entries(positions)) div.style.setProperty(key, value);
    return div;
  }

  for (const poster of document.querySelectorAll('.poster')) {
    const d = poster.dataset;
    const content = poster.querySelector('.poster-content');
    const scale = fitScale(content, Number(d.w), Number(d.h));
    content.style.setProperty('zoom', String(scale));
    if (Number(d.cols) * Number(d.rows) > 1) {
      tileSheets(poster, content);
    } else {
      poster.classList.add('single', 'screen-fit', `page-${d.page}`);
      poster.style.setProperty('width', mm(Number(d.w)));
      poster.style.setProperty('height', mm(Number(d.h)));
    }
  }

  // On screen: shrink sheets and posters to the window's width.
  const shown = [...document.querySelectorAll('.sheet, .screen-fit')];
  const unfit = () => shown.forEach((el) => el.style.removeProperty('zoom'));
  function fit() {
    unfit();
    const room = document.documentElement.clientWidth - 32;
    for (const el of shown) {
      const scale = Math.min(1, room / el.offsetWidth);
      if (scale < 1) el.style.setProperty('zoom', String(scale));
    }
  }
  window.addEventListener('resize', fit);
  window.addEventListener('beforeprint', unfit);
  window.addEventListener('afterprint', fit);
  document.getElementById('print')?.addEventListener('click', () => window.print());
  if (window.matchMedia('screen').matches) fit();
})();
