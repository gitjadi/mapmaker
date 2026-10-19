// The attribute table: a data layer's features as rows under the map. Only the rows in view are
// drawn, so it stays quick with hundreds of thousands of features. Click a column header to sort,
// type to find rows, show only the selected ones; selecting rows selects the features on the map
// (Ctrl adds, Shift selects a range), double-click zooms to one.

import type { Editor } from '../editor';
import { Table, colText, countMask } from '../table';
import { check, el } from './dom';
import { icon } from './icons';

const ROW = 22;
const collator = new Intl.Collator('sv', { numeric: true, sensitivity: 'base' });

export function mountAttributeTable(ed: Editor) {
  const panel = document.getElementById('table-panel')!;
  let layerId: string | null = null;
  let order: Int32Array = new Int32Array(0);       // rows shown, in order (feature indices)
  let sort: { field: string; desc: boolean } | null = null;
  let search = '';
  let onlySelected = false;
  let lastClicked = -1;
  let widths: number[] = [];
  let builtFor = '';
  let colsTemplate = '';

  const head = el('div', { class: 'tp-head' });
  const scroller = el('div', { class: 'tp-scroll' });
  const inner = el('div', { class: 'tp-inner' });
  const header = el('div', { class: 'tp-row tp-header' });
  const rows = el('div', { class: 'tp-rows' });
  inner.append(header, rows);
  scroller.append(inner);
  const grip = el('div', { class: 'tp-grip', title: 'Drag to resize' });
  panel.append(grip, head, scroller);

  // resize by dragging the top edge
  grip.addEventListener('pointerdown', e => {
    grip.setPointerCapture(e.pointerId);
    const y0 = e.clientY, h0 = panel.offsetHeight;
    const move = (ev: PointerEvent) => { panel.style.height = Math.max(120, Math.min(window.innerHeight * 0.75, h0 - (ev.clientY - y0))) + 'px'; };
    const up = () => { grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up); };
    grip.addEventListener('pointermove', move);
    grip.addEventListener('pointerup', up);
  });

  const table = (): Table | null => ed.doc.table(ed.doc.layer(layerId));

  function open(id?: string | null) {
    const L = id ? ed.doc.layer(id) : ed.dataTarget();
    if (!L || !ed.doc.table(L)) { ed.status('Choose a data layer to see its attribute table'); return; }
    layerId = L.id;
    panel.classList.add('open');
    rebuild();
    ed.resize();
  }
  function close() { panel.classList.remove('open'); ed.resize(); }
  const isOpen = () => panel.classList.contains('open');

  /** The rows to show: search and "only selected", then the sort order. */
  function computeOrder() {
    const t = table();
    if (!t) { order = new Int32Array(0); return; }
    const sel = ed.selectionOf(layerId);
    const ids: number[] = [];
    const q = search.trim().toLocaleLowerCase();
    // for text columns, which distinct values match – once per value, not per row
    const catHits = q ? t.fields.map(f => { const c = t.cols[f]; return c.kind === 'cat' ? Uint8Array.from(c.cats, v => +v.toLocaleLowerCase().includes(q)) : null; }) : null;
    for (let i = 0; i < t.n; i++) {
      if (onlySelected && !(sel && sel[i])) continue;
      if (q) {
        let hit = false;
        for (let j = 0; j < t.fields.length && !hit; j++) {
          const c = t.cols[t.fields[j]];
          if (c.kind === 'cat') hit = c.codes[i] >= 0 && !!catHits![j]![c.codes[i]];
          else hit = colText(c, i).includes(q);
        }
        if (!hit) continue;
      }
      ids.push(i);
    }
    if (sort && t.cols[sort.field]) {
      const c = t.cols[sort.field];
      const dir = sort.desc ? -1 : 1;
      if (c.kind === 'num') {
        const v = c.values;
        ids.sort((a, b) => {
          const x = v[a], y = v[b];
          if (!Number.isFinite(x)) return Number.isFinite(y) ? 1 : a - b;
          if (!Number.isFinite(y)) return -1;
          return (x - y) * dir || a - b;
        });
      } else {
        // rank the distinct values once, then sort the rows by rank
        const rank = new Int32Array(c.cats.length);
        const byVal = c.cats.map((_, i) => i).sort((a, b) => collator.compare(c.cats[a], c.cats[b]));
        byVal.forEach((ci, r) => { rank[ci] = r; });
        ids.sort((a, b) => {
          const x = c.codes[a], y = c.codes[b];
          if (x < 0) return y < 0 ? a - b : 1;
          if (y < 0) return -1;
          return (rank[x] - rank[y]) * dir || a - b;
        });
      }
    }
    order = Int32Array.from(ids);
  }

  function measure(t: Table) {
    const probe = document.createElement('canvas').getContext('2d')!;
    probe.font = '11.5px "Segoe UI", system-ui, sans-serif';
    widths = t.fields.map(f => {
      let w = probe.measureText(f).width + 28;
      const c = t.cols[f];
      for (let i = 0; i < Math.min(t.n, 300); i++) w = Math.max(w, probe.measureText(colText(c, i)).width + 16);
      return Math.min(280, Math.max(60, Math.ceil(w)));
    });
  }

  function rebuild() {
    const t = table();
    const L = ed.doc.layer(layerId);
    if (!t || !L) { close(); return; }
    const key = `${layerId}|${L.table}`;
    if (key !== builtFor) { builtFor = key; measure(t); sort = null; lastClicked = -1; }
    computeOrder();
    renderHead();
    const numW = 18 + String(t.n).length * 7.5;
    const total = numW + widths.reduce((a, b) => a + b, 0);
    inner.style.width = total + 'px';
    header.innerHTML = '';
    header.style.gridTemplateColumns = `${numW}px ${widths.map(w => w + 'px').join(' ')}`;
    colsTemplate = header.style.gridTemplateColumns;
    header.append(el('div', { class: 'tp-cell tp-num' }, '#'));
    t.fields.forEach(f => {
      const arrow = sort?.field === f ? (sort.desc ? ' ▼' : ' ▲') : '';
      header.append(el('div', { class: 'tp-cell tp-h', title: `Sort by ${f}`, onclick: () => {
        sort = sort?.field === f ? (sort.desc ? null : { field: f, desc: true }) : { field: f, desc: false };
        computeOrder(); rebuildRowsOnly();
      } }, f + arrow));
    });
    rows.style.height = order.length * ROW + 'px';
    paintRows();
  }

  function rebuildRowsOnly() { rebuild(); }

  function renderHead() {
    const t = table()!;
    const L = ed.doc.layer(layerId)!;
    const nsel = countMask(ed.selectionOf(layerId));
    const searchBox = el('input', { type: 'search', placeholder: 'Find…', value: search, class: 'tp-search' });
    searchBox.oninput = () => { search = searchBox.value; computeOrder(); rows.style.height = order.length * ROW + 'px'; paintRows(); countText.textContent = counts(); };
    const counts = () => `${order.length.toLocaleString('sv-SE')} shown · ${countMask(ed.selectionOf(layerId)).toLocaleString('sv-SE')} selected of ${t.n.toLocaleString('sv-SE')}`;
    const countText = el('span', { class: 'tp-count' }, counts());
    const layerSel = el('select', { class: 'tp-layer', onchange: () => { layerId = layerSel.value; search = ''; rebuild(); } });
    for (const x of [...ed.doc.layers].reverse()) if (x.kind === 'data' && ed.doc.table(x)) layerSel.append(el('option', { value: x.id, selected: x.id === L.id }, x.name));
    const b = (label: string, f: () => void, dis = false) => el('button', { class: 'btn tp-btn', disabled: dis, onclick: f }, label);
    head.innerHTML = '';
    head.append(
      el('span', { class: 'tp-title' }, 'Attribute table'), layerSel, searchBox,
      check('Only selected', onlySelected, v => { onlySelected = v; rebuild(); }),
      countText, el('span', { class: 'spacer' }),
      b('Zoom to selected', () => ed.zoomToSelected(), !nsel),
      b('Clear selection', () => ed.setSelection(L.id, null), !nsel),
      b('New layer from selection', () => ed.layerFromSelection(), !nsel),
      el('button', { class: 'ico tp-close', title: 'Close (Ctrl+T)', html: icon('close'), onclick: close }),
    );
    if (document.activeElement?.classList.contains('tp-search')) { /* keep focus below */ }
  }

  function paintRows() {
    const t = table();
    if (!t) return;
    const sel = ed.selectionOf(layerId);
    const top = scroller.scrollTop;
    const h = scroller.clientHeight;
    const first = Math.max(0, Math.floor((top - ROW) / ROW) - 5);
    const last = Math.min(order.length, Math.ceil((top + h) / ROW) + 5);
    const cols = colsTemplate;
    const frag = document.createDocumentFragment();
    for (let r = first; r < last; r++) {
      const i = order[r];
      const div = el('div', { class: 'tp-row' + (sel && sel[i] ? ' sel' : '') + (ed.selFeature?.layerId === layerId && ed.selFeature?.index === i ? ' cur' : ''), 'data-r': String(r) });
      div.style.top = r * ROW + 'px';
      div.style.gridTemplateColumns = cols;
      div.append(el('div', { class: 'tp-cell tp-num' }, String(i + 1)));
      for (const f of t.fields) {
        const tx = colText(t.cols[f], i);
        div.append(el('div', { class: 'tp-cell' + (t.cols[f].kind === 'num' ? ' n' : ''), title: tx.length > 30 ? tx.slice(0, 500) : '' }, tx));
      }
      frag.append(div);
    }
    rows.replaceChildren(frag);
  }

  scroller.addEventListener('scroll', () => requestAnimationFrame(paintRows));

  rows.addEventListener('click', e => {
    const rowEl = (e.target as HTMLElement).closest('.tp-row') as HTMLElement | null;
    const t = table();
    if (!rowEl || !t || !layerId) return;
    const r = Number(rowEl.dataset.r), i = order[r];
    const cur = ed.selectionOf(layerId);
    let m: Uint8Array;
    if (e.shiftKey && lastClicked >= 0) {
      m = cur ? cur.slice() : new Uint8Array(t.n);
      const [a, b] = [Math.min(lastClicked, r), Math.max(lastClicked, r)];
      for (let j = a; j <= b; j++) m[order[j]] = 1;
    } else if (e.ctrlKey || e.metaKey) {
      m = cur ? cur.slice() : new Uint8Array(t.n);
      m[i] ^= 1;
      lastClicked = r;
    } else {
      m = new Uint8Array(t.n); m[i] = 1;
      lastClicked = r;
    }
    ed.setSelection(layerId, m, true);
  });
  rows.addEventListener('dblclick', e => {
    const rowEl = (e.target as HTMLElement).closest('.tp-row') as HTMLElement | null;
    const t = table();
    if (!rowEl || !t || !layerId) return;
    const i = order[Number(rowEl.dataset.r)];
    ed.zoomToFeatures(t, [i], 15);
    ed.selLayer = layerId; ed.selFeature = { layerId, index: i };
    ed.redraw(); ed.emit('selection');
  });

  ed.on('data-selection', () => {
    if (!isOpen()) return;
    if (onlySelected) { rebuild(); return; }
    renderHead(); paintRows();
  });
  ed.on('change', () => {
    if (!isOpen()) return;
    const L = ed.doc.layer(layerId);
    if (!L || !ed.doc.table(L)) { close(); return; }
    if (`${layerId}|${L.table}` !== builtFor) rebuild();
  });
  ed.on('feature', () => {
    if (!isOpen() || ed.selFeature?.layerId !== layerId) return;
    const r = order.indexOf(ed.selFeature.index);
    if (r >= 0 && (r * ROW < scroller.scrollTop || r * ROW > scroller.scrollTop + scroller.clientHeight - ROW)) scroller.scrollTop = r * ROW - scroller.clientHeight / 2;
    paintRows();
  });
  ed.on('selection', () => {
    // follow the data layer picked in the Layers panel
    const L = ed.selectedLayer();
    if (isOpen() && L?.kind === 'data' && L.id !== layerId && ed.doc.table(L)) { layerId = L.id; search = ''; rebuild(); }
  });

  return { open, close, toggle: () => (isOpen() ? close() : open()), isOpen };
}
