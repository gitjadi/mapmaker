// The Properties panel: shows what is selected – a label, a symbol, a line, an area, a layer –
// or the map itself when nothing is.

import type { Editor } from '../editor';
import { Item, Layer, KIND_LABELS, WEB_KINDS, itemTitle, textStyle } from '../doc';
import { BASEMAPS } from '../shared/basemaps';
import { DASHES, FONTS, QUALITATIVE, SEQUENTIAL, SWATCHES, TEXT_PRESETS } from '../shared/styles';
import { AREA, LINE, POINT, Table, colCounts, colText, makeCategories, makeGraduated, numericValues } from '../table';
const colCountsN = (t: Table, f: string) => colCounts(t.cols[f], 61).length;
import { SYMBOLS, SYMBOL_CATEGORIES } from '../shared/symbols';
import { symbolSvg } from '../render';
import { check, colorInput, el, row, select, slider } from './dom';

export function mountProperties(ed: Editor) {
  const box = document.getElementById('properties')!;
  let shownFor = '';
  const key = () => `${ed.selLayer}|${ed.selItem}|${ed.selFeature?.index ?? ''}`;

  /** A change to the document, merged into one undo step while it keeps coming (typing, sliders). */
  const change = (label: string, key: string, f: () => void) => {
    ed.doc.checkpoint(label, key);
    f();
    ed.doc.changed();
  };

  function render() {
    shownFor = key();
    box.innerHTML = '';
    const it = ed.selectedItem();
    const L = ed.selectedLayer();
    if (it && L) itemProps(L, it);
    else if (L && ed.selFeature?.layerId === L.id && ed.doc.table(L)) featureProps(L, ed.doc.table(L)!, ed.selFeature.index);
    else if (L) layerProps(L);
    else mapProps();
  }

  // ---------------------------------------------------------------- the map
  function mapProps() {
    const title = el('input', { type: 'text', value: ed.doc.title });
    title.oninput = () => change('Rename map', 'title', () => { ed.doc.title = title.value; });
    box.append(
      el('div', { class: 'prop-title' }, 'Map'),
      el('div', { class: 'prop-sub' }, 'Select a layer or something on the map to change it.'),
      row('Title', title),
    );
  }

  // ---------------------------------------------------------------- layers
  function layerProps(L: Layer) {
    const name = el('input', { type: 'text', value: L.name });
    name.oninput = () => change('Rename layer', 'name:' + L.id, () => { L.name = name.value; });
    box.append(el('div', { class: 'prop-title' }, L.name), el('div', { class: 'prop-sub' }, KIND_LABELS[L.kind] || L.kind), row('Name', name));
    if (!WEB_KINDS.includes(L.kind)) {
      box.append(el('div', { class: 'note' }, 'This kind of layer isn’t shown in the browser version yet. It stays in the map and is saved back with it, so you can keep working on it in the desktop app.'));
      return;
    }
    if (L.kind === 'color') {
      box.append(row('Colour', colorInput(L.color, v => change('Colour', 'color:' + L.id, () => { L.color = v; }))));
    }
    if (L.kind === 'basemap') {
      const src = BASEMAPS.find(b => b.id === L.source);
      box.append(row('Map', select(BASEMAPS.map(b => [b.id, b.name]), L.source, v => change('Background map', 'src', () => {
        const old = BASEMAPS.find(b => b.id === L.source);
        if (!old || L.name === old.name) L.name = BASEMAPS.find(b => b.id === v)?.name || v;
        L.source = v;
      }))));
      if (src?.labels?.length) box.append(row('', el('div', { class: 'checks' }, check('Place names on top', !!L.labels, v => change('Place names', 'labels', () => { L.labels = v; })))));
      if (src?.note) box.append(el('div', { class: 'note' }, src.note));
      if (src?.attribution) box.append(el('div', { class: 'prop-sub' }, src.attribution));
    }
    if (L.kind === 'service') {
      const s = L.source || {};
      const url = el('input', { type: 'url', value: (s.urls || [''])[0] });
      url.onchange = () => change('Service address', 'url:' + L.id, () => { s.urls = [url.value.trim()]; });
      box.append(row('Address', url));
      if (s.kind === 'wms') {
        const lay = el('input', { type: 'text', value: s.wms_layers || '' });
        lay.onchange = () => change('WMS layers', 'wms:' + L.id, () => { s.wms_layers = lay.value.trim(); });
        box.append(row('WMS layers', lay));
      }
      const att = el('input', { type: 'text', value: s.attribution || '' });
      att.oninput = () => change('Credits', 'att:' + L.id, () => { s.attribution = att.value; });
      box.append(row('Credits', att));
    }
    if (L.kind === 'data') {
      const t = ed.doc.table(L);
      if (!t) { box.append(el('div', { class: 'note' }, 'The features of this layer couldn’t be read.')); return; }
      dataProps(L, t);
    }
    if (L.kind === 'annotations') {
      const n = (L.items || []).length;
      box.append(el('div', { class: 'prop-sub' }, n ? `${n} item${n === 1 ? '' : 's'} – listed under the layer in the Layers panel.` : 'Empty – use the Text, Symbol, Line and Area tools to add to it.'));
      if (n) box.append(el('div', { class: 'btn-row' }, el('button', { class: 'btn', onclick: () => ed.zoomToSelection() }, 'Zoom to layer')));
    }
  }

  // ---------------------------------------------------------------- data layers
  function dataProps(L: Layer, t: Table) {
    const st = L.style ||= {};
    const kinds = new Set<number>();
    for (let i = 0; i < t.n && kinds.size < 3; i++) kinds.add(t.kind[i]);
    const kindText = [kinds.has(POINT) && 'points', kinds.has(LINE) && 'lines', kinds.has(AREA) && 'areas'].filter(Boolean).join(', ');
    box.append(el('div', { class: 'prop-sub' }, `${t.n.toLocaleString('sv-SE')} features (${kindText}) · ${t.fields.length} fields`));
    const cls = st.classify && st.classify.mode !== 'single' ? st.classify : null;
    const mode = cls ? cls.mode : 'single';
    const fieldOpts: [string, string][] = t.fields.map(f => [f, f]);
    const numFields: [string, string][] = t.fields.filter(f => {
      const v = numericValues(t, f); for (let i = 0; i < Math.min(v.length, 2000); i++) if (Number.isFinite(v[i])) return true; return false;
    }).map(f => [f, f]);

    box.append(el('div', { class: 'prop-group' }, 'Colour'));
    box.append(row('Colour by', select([['single', 'One colour'], ['categories', 'Categories (one per value)'], ['graduated', 'Graduated (number classes)']], mode, v => {
      if (v === 'single') return change('One colour', 'cls', () => { delete st.classify; render(); });
      // a sensible first field: for categories one with few distinct values (a type, not an id or a coordinate)
      const catField = t.fields.find(fl => { const c = t.cols[fl]; return c.kind === 'cat' && c.cats.length >= 2 && c.cats.length <= 60; })
        || t.fields.find(fl => colCountsN(t, fl) <= 60) || fieldOpts[0]?.[0];
      const f = v === 'graduated' ? (numFields.find(([n]) => !/^(lat|lon|lng|x|y|n|e|id|fid|objectid)$/i.test(n)) || numFields[0])?.[0] : (st.classify?.field || catField);
      if (!f) return render();
      try { change('Colour by', 'cls', () => { st.classify = v === 'graduated' ? makeGraduated(t, f) : makeCategories(t, f); render(); }); }
      catch (e: any) { render(); box.prepend(el('div', { class: 'note' }, e.message)); }
    })));
    if (cls) {
      const f = select(cls.mode === 'graduated' ? numFields : fieldOpts, cls.field, v => change('Field', 'cls', () => {
        st.classify = cls.mode === 'graduated' ? makeGraduated(t, v, cls.classes.length, cls.method, cls.scheme) : makeCategories(t, v, cls.scheme); render();
      }));
      box.append(row('Field', f));
      const schemes = Object.keys(cls.mode === 'graduated' ? SEQUENTIAL : QUALITATIVE).map(k => [k, k] as [string, string]);
      box.append(row('Colours', select(schemes, cls.scheme, v => change('Colours', 'cls', () => {
        st.classify = cls.mode === 'graduated' ? makeGraduated(t, cls.field, cls.classes.length, cls.method, v) : makeCategories(t, cls.field, v); render();
      }))));
      if (cls.mode === 'graduated') {
        box.append(row('Classes', select([2, 3, 4, 5, 6, 7, 8, 9].map(n => [String(n), String(n)]), String(cls.classes.length), v => change('Classes', 'cls', () => {
          st.classify = makeGraduated(t, cls.field, Number(v), cls.method, cls.scheme); render();
        }))));
        box.append(row('Breaks', select([['quantile', 'Equal counts (quantiles)'], ['equal', 'Equal steps']], cls.method || 'quantile', v => change('Breaks', 'cls', () => {
          st.classify = makeGraduated(t, cls.field, cls.classes.length, v, cls.scheme); render();
        }))));
      }
      const list = el('div', { class: 'class-list' });
      const classRow = (c: any, label: string) => {
        const on = el('input', { type: 'checkbox', checked: c.show !== false, title: 'Show' });
        on.onchange = () => change('Show class', 'cls-show', () => { c.show = on.checked; });
        const col = el('input', { type: 'color', value: c.color });
        col.oninput = () => change('Class colour', 'cls-col', () => { c.color = col.value; });
        const name = el('input', { type: 'text', value: c.label ?? label });
        name.oninput = () => change('Legend text', 'cls-lab', () => { c.label = name.value; });
        return el('div', { class: 'class-row' }, on, col, name, el('span', { class: 'badge' }, (c.count ?? '').toLocaleString('sv-SE')));
      };
      for (const c of cls.classes) list.append(classRow(c, String(c.value ?? '')));
      if (cls.mode === 'categories' && (cls.other?.count || 0) > 0) list.append(classRow(cls.other, 'Other'));
      box.append(list);
    }

    if (kinds.has(POINT)) {
      box.append(el('div', { class: 'prop-group' }, 'Points'));
      const markers: [string, string][] = [['circle', 'Circle'], ...Object.entries(SYMBOLS).filter(([k]) => k !== 'circle').map(([k, v]) => [k, v.label] as [string, string])];
      box.append(row('Marker', select(markers, st.marker || 'circle', v => change('Marker', 'mk:' + L.id, () => { st.marker = v; }))));
      if (!cls) colorRow('Colour', st.color, v => change('Colour', 'pc:' + L.id, () => { st.color = v; }));
      box.append(row('Size', slider(st.size ?? 7, 1, 40, 0.5, v => change('Size', 'ps:' + L.id, () => { st.size = v; }))));
      box.append(row('Outline', colorInput(st.outline || '#ffffff', v => change('Outline', 'po:' + L.id, () => { st.outline = v; }))));
      box.append(row('Outline width', slider(st.outline_width ?? 1, 0, 4, 0.1, v => change('Outline width', 'pow:' + L.id, () => { st.outline_width = v; }), v => v.toFixed(1))));
    }
    if (kinds.has(LINE)) {
      box.append(el('div', { class: 'prop-group' }, 'Lines'));
      if (!cls) colorRow('Colour', st.line_color, v => change('Line colour', 'lc:' + L.id, () => { st.line_color = v; }));
      box.append(row('Width', slider(st.line_width ?? 1.6, 0.2, 12, 0.1, v => change('Line width', 'lw:' + L.id, () => { st.line_width = v; }), v => v.toFixed(1))));
      box.append(row('Dashes', select(Object.entries(DASHES), st.dash || 'solid', v => change('Dashes', 'ld:' + L.id, () => { st.dash = v; }))));
    }
    if (kinds.has(AREA)) {
      box.append(el('div', { class: 'prop-group' }, 'Areas'));
      if (!cls) colorRow('Fill', st.fill, v => change('Fill', 'af:' + L.id, () => { st.fill = v; }));
      box.append(row('Fill opacity %', slider(Math.round((st.fill_opacity ?? 0.25) * 100), 0, 100, 1, v => change('Fill opacity', 'afo:' + L.id, () => { st.fill_opacity = v / 100; }))));
      if (!cls) colorRow('Outline', st.stroke, v => change('Outline', 'as:' + L.id, () => { st.stroke = v; }));
      box.append(row('Outline width', slider(st.stroke_width ?? 1.2, 0, 8, 0.1, v => change('Outline width', 'asw:' + L.id, () => { st.stroke_width = v; }), v => v.toFixed(1))));
    }

    const lab = L.label ||= { field: null, style: textStyle('feature') };
    box.append(el('div', { class: 'prop-group' }, 'Labels'));
    box.append(row('Label with', select([['', 'No labels'], ...fieldOpts], lab.field || '', v => change('Labels', 'lab:' + L.id, () => { lab.field = v || null; render(); }))));
    if (lab.field) textStyleRows(lab.style ||= textStyle('feature'), L.id + ':lab', true);
    box.append(el('div', { class: 'prop-group' }, 'Legend'),
      row('', el('div', { class: 'checks' }, check('Show in the legend', L.legend !== false, v => change('Legend', 'leg:' + L.id, () => { L.legend = v; })))));
    box.append(el('div', { class: 'btn-row' }, el('button', { class: 'btn', onclick: () => ed.zoomToSelection() }, 'Zoom to layer')));
  }

  function featureProps(L: Layer, t: Table, i: number) {
    const kind = ['Point', 'Line', 'Area'][t.kind[i]] || 'Feature';
    const nameField = t.fields.find(f => /^(name|namn|title|benamning|benämning)$/i.test(f));
    const title = nameField ? colText(t.cols[nameField], i) : '';
    box.append(el('div', { class: 'prop-title' }, title || `${kind} ${i + 1}`), el('div', { class: 'prop-sub' }, `${kind} in “${L.name}” – feature ${i + 1} of ${t.n.toLocaleString('sv-SE')}`));
    const tbl = el('table', { class: 'attrs' });
    for (const f of t.fields) {
      const v = colText(t.cols[f], i);
      tbl.append(el('tr', {}, el('th', {}, f), el('td', {}, v || '—')));
    }
    if (Number.isFinite(t.x[i])) tbl.append(el('tr', {}, el('th', {}, 'Position'), el('td', {}, `${t.y[i].toFixed(5)}° N, ${t.x[i].toFixed(5)}° E`)));
    box.append(tbl);
    box.append(el('div', { class: 'btn-row' },
      el('button', { class: 'btn', onclick: () => ed.zoomToSelection() }, 'Zoom to'),
      el('button', { class: 'btn', onclick: () => ed.select(L.id) }, 'Layer style')));
  }

  // ---------------------------------------------------------------- items
  function itemProps(L: Layer, it: Item) {
    const head = { text: 'Text', symbol: 'Symbol', line: 'Line', area: 'Area' }[it.type as string] || 'Item';
    box.append(el('div', { class: 'prop-title' }, itemTitle(it)), el('div', { class: 'prop-sub' }, `${head} in “${L.name}”`));
    if (it.type === 'text') {
      const t = el('textarea', { id: 'prop-text', rows: 2 });
      t.value = it.text || '';
      t.oninput = () => change('Edit text', 'text:' + it.id, () => { it.text = t.value; });
      box.append(row('Text', t));
      box.append(row('Type style', select(Object.entries(TEXT_PRESETS).map(([k, v]) => [k, v.label]), it.preset || 'place', v =>
        change('Type style', 'preset:' + it.id, () => { it.preset = v; it.style = { ...textStyle(v), angle: it.style?.angle || 0 }; render(); }))));
      textStyleRows(it.style, it.id);
    } else if (it.type === 'symbol') {
      box.append(symbolPicker(it.symbol, v => change('Symbol', 'sym:' + it.id, () => { it.symbol = v; render(); })));
      colorRow('Colour', it.color, v => change('Colour', 'col:' + it.id, () => { it.color = v; }));
      box.append(row('Size', slider(it.size ?? 22, 8, 80, 1, v => change('Size', 'size:' + it.id, () => { it.size = v; }))));
      box.append(row('', el('div', { class: 'checks' }, check('In a circle (badge)', !!it.badge, v => change('Badge', 'badge:' + it.id, () => { it.badge = v; })))));
      const lab = el('input', { type: 'text', value: it.label || '', placeholder: 'Name next to the symbol' });
      lab.oninput = () => change('Label', 'label:' + it.id, () => { it.label = lab.value; });
      box.append(el('div', { class: 'prop-group' }, 'Label'), row('Text', lab));
      if (it.label) textStyleRows(it.label_style ||= textStyle('place'), it.id + ':l', true);
      legendRows(it);
    } else if (it.type === 'line') {
      const st = it.style ||= {};
      colorRow('Colour', st.color, v => change('Colour', 'col:' + it.id, () => { st.color = v; }));
      box.append(row('Width', slider(st.width ?? 2.5, 0.3, 16, 0.1, v => change('Width', 'w:' + it.id, () => { st.width = v; }), v => v.toFixed(1))));
      box.append(row('Dashes', select(Object.entries(DASHES), st.dash || 'solid', v => change('Dashes', 'dash:' + it.id, () => { st.dash = v; }))));
      box.append(row('Opacity %', slider(Math.round((st.opacity ?? 1) * 100), 5, 100, 1, v => change('Opacity', 'op:' + it.id, () => { st.opacity = v / 100; }))));
      box.append(row('', el('div', { class: 'checks' }, check('Arrow at the end', !!st.arrow, v => change('Arrow', 'arrow:' + it.id, () => { st.arrow = v; })))));
      legendRows(it);
    } else if (it.type === 'area') {
      const st = it.style ||= {};
      colorRow('Fill', st.fill, v => change('Fill', 'fill:' + it.id, () => { st.fill = v; }));
      box.append(row('Fill opacity %', slider(Math.round((st.fill_opacity ?? 0.22) * 100), 0, 100, 1, v => change('Fill opacity', 'fo:' + it.id, () => { st.fill_opacity = v / 100; }))));
      colorRow('Outline', st.stroke, v => change('Outline', 'stroke:' + it.id, () => { st.stroke = v; }));
      box.append(row('Outline width', slider(st.width ?? 1.8, 0, 12, 0.1, v => change('Outline width', 'w:' + it.id, () => { st.width = v; }), v => v.toFixed(1))));
      box.append(row('Dashes', select(Object.entries(DASHES), st.dash || 'solid', v => change('Dashes', 'dash:' + it.id, () => { st.dash = v; }))));
      legendRows(it);
    }
    box.append(el('div', { class: 'btn-row' },
      el('button', { class: 'btn', onclick: () => ed.zoomToSelection() }, 'Zoom to'),
      el('button', { class: 'btn', onclick: () => ed.duplicate() }, 'Duplicate'),
      el('button', { class: 'btn danger', onclick: () => ed.deleteSelection() }, 'Delete'),
    ));
  }

  function textStyleRows(st: Record<string, any>, key: string, isLabel = false) {
    const fonts: [string, string][] = Object.values(FONTS).flat().map(f => [f, f]);
    if (st.font && !fonts.some(f => f[0] === st.font)) fonts.unshift([st.font, st.font]);
    box.append(row('Font', select(fonts, st.font || 'Segoe UI', v => change('Font', 'font:' + key, () => { st.font = v; }))));
    box.append(row('Size (pt)', slider(st.size ?? 10, 5, 72, 0.5, v => change('Text size', 'tsize:' + key, () => { st.size = v; }))));
    colorRow('Colour', st.color, v => change('Text colour', 'tcol:' + key, () => { st.color = v; }));
    box.append(row('', el('div', { class: 'checks' },
      check('Bold', !!st.bold, v => change('Bold', 'b:' + key, () => { st.bold = v; })),
      check('Italic', !!st.italic, v => change('Italic', 'i:' + key, () => { st.italic = v; })),
      check('Capitals', !!st.caps, v => change('Capitals', 'caps:' + key, () => { st.caps = v; })),
    )));
    box.append(row('Letter spacing', slider(st.spacing ?? 0, 0, 80, 1, v => change('Letter spacing', 'sp:' + key, () => { st.spacing = v; }))));
    if (!isLabel) {
      box.append(row('Angle °', slider(st.angle ?? 0, -180, 180, 1, v => change('Angle', 'ang:' + key, () => { st.angle = v; }))));
      box.append(row('Align', select([['center', 'Centre'], ['left', 'Left'], ['right', 'Right']], st.align || 'center', v => change('Align', 'al:' + key, () => { st.align = v; }))));
    }
    const haloOn = !!st.halo && !st.box;
    box.append(el('div', { class: 'prop-group' }, 'Halo or box'));
    box.append(row('Background', select([['halo', 'Halo (outline)'], ['box', 'Box'], ['none', 'None']], st.box ? 'box' : st.halo ? 'halo' : 'none', v =>
      change('Halo', 'halo:' + key, () => {
        if (v === 'box') { st.box = st.box || '#ffffff'; }
        else if (v === 'halo') { st.box = ''; st.halo = st.halo || '#ffffff'; }
        else { st.box = ''; st.halo = ''; }
        render();
      }))));
    if (haloOn) {
      colorRow('Halo colour', st.halo, v => change('Halo colour', 'hc:' + key, () => { st.halo = v; }));
      box.append(row('Halo width', slider(st.halo_width ?? 1.4, 0.2, 5, 0.1, v => change('Halo width', 'hw:' + key, () => { st.halo_width = v; }), v => v.toFixed(1))));
    }
    if (st.box) colorRow('Box colour', st.box, v => change('Box colour', 'bc:' + key, () => { st.box = v; }));
  }

  function legendRows(it: Item) {
    const lt = el('input', { type: 'text', value: it.legend_text || '', placeholder: itemTitle(it) });
    lt.oninput = () => change('Legend text', 'lt:' + it.id, () => { it.legend_text = lt.value; });
    box.append(el('div', { class: 'prop-group' }, 'Legend'),
      row('', el('div', { class: 'checks' }, check('Show in the legend', !!it.legend, v => change('Legend', 'leg:' + it.id, () => { it.legend = v; })))),
      row('Legend text', lt));
  }

  function colorRow(label: string, value: string, onInput: (v: string) => void) {
    const c = colorInput(value, onInput);
    const sw = el('div', { class: 'swatches' }, ...(SWATCHES as string[][]).map(([name, hex]) =>
      el('button', { title: name, style: { background: hex }, onclick: (e: Event) => { e.preventDefault(); c.value = hex; onInput(hex); } })));
    box.append(row(label, el('div', {}, c, sw)));
  }

  render();
  ed.on('selection', render);
  ed.on('change', () => {
    // rebuild only when it isn't being edited (keeps focus while typing and dragging sliders)
    if (box.contains(document.activeElement) && shownFor === key()) {
      const t = box.querySelector('.prop-title');
      const it = ed.selectedItem(), L = ed.selectedLayer();
      if (t) t.textContent = it ? itemTitle(it) : L ? L.name : 'Map';
      return;
    }
    render();
  });
  ed.on('edit-text' as any, () => {
    // after the click that placed the label has finished (it would take the focus back)
    setTimeout(() => {
      const t = document.getElementById('prop-text') as HTMLTextAreaElement | null;
      if (t) { t.focus(); t.select(); }
    }, 0);
  });
}

/** A grid of map symbols by category. */
export function symbolPicker(value: string, onPick: (key: string) => void) {
  const wrap = el('div', {});
  for (const cat of SYMBOL_CATEGORIES) {
    wrap.append(el('div', { class: 'prop-group' }, cat));
    const g = el('div', { class: 'symbols' });
    for (const [k, s] of Object.entries(SYMBOLS)) {
      if (s.category !== cat) continue;
      const img = el('img', { alt: s.label, src: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(symbolSvg(k, '#e6e6e6', null)) });
      g.append(el('button', { class: k === value ? 'on' : '', title: s.label, onclick: () => onPick(k) }, img));
    }
    wrap.append(g);
  }
  return wrap;
}
