// The Properties panel: shows what is selected – a label, a symbol, a line, an area, a layer –
// or the map itself when nothing is.

import type { Editor } from '../editor';
import { Item, Layer, KIND_LABELS, WEB_KINDS, itemTitle, textStyle } from '../doc';
import { BASEMAPS } from '../shared/basemaps';
import { DASHES, FONTS, HEAT_RAMPS, LEGEND_STYLES, NORTH_STYLES, QUALITATIVE, SEQUENTIAL, SWATCHES, TEXT_PRESETS, THEMES } from '../shared/styles';
import { PAPERS, design } from '../page';
import { AREA, LINE, POINT, Table, colCounts, colText, makeCategories, makeGraduated, numericValues } from '../table';
const colCountsN = (t: Table, f: string) => colCounts(t.cols[f], 61).length;
import { SYMBOLS, SYMBOL_CATEGORIES } from '../shared/symbols';
import { symbolSvg } from '../render';
import { check, colorInput, editingIn, el, row, select, slider } from './dom';

export function mountProperties(ed: Editor) {
  const box = document.getElementById('properties')!;
  let shownFor = '';
  const key = () => `${ed.selLayer}|${ed.selItem}|${ed.selFeature?.index ?? ''}|${ed.selElement ?? ''}`;

  /** A change to the document, merged into one undo step while it keeps coming (typing, sliders). */
  const change = (label: string, key: string, f: () => void) => {
    ed.doc.checkpoint(label, key);
    f();
    ed.doc.changed();
  };

  function render() {
    // keep the scroll position when the same thing is shown again (after a change)
    const same = shownFor === key();
    const scroll = box.scrollTop;
    shownFor = key();
    box.innerHTML = '';
    const it = ed.selectedItem();
    const L = ed.selectedLayer();
    if (ed.selElement && ed.pageMode) pageProps(ed.selElement);
    else if (it && L) itemProps(L, it);
    else if (L && ed.selFeature?.layerId === L.id && ed.doc.table(L)) featureProps(L, ed.doc.table(L)!, ed.selFeature.index);
    else if (L) layerProps(L);
    else mapProps();
    if (same) box.scrollTop = scroll;
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

    const nsel = ed.selectedCount(L.id);
    if (nsel) box.append(el('div', { class: 'note' }, `${nsel.toLocaleString('sv-SE')} selected – Select menu for more.`));

    // show as points (or lines and areas), a heat map, or clusters
    if (kinds.has(POINT)) {
      box.append(row('Show as', select([['points', 'Features'], ['heatmap', 'Heat map (density)'], ['clusters', 'Clusters with counts']], st.render || 'points', v =>
        change('Show as', 'render:' + L.id, () => { st.render = v; render(); }))));
      if (st.render === 'heatmap') {
        box.append(el('div', { class: 'prop-group' }, 'Heat map'));
        box.append(row('Radius (px)', slider(st.heat_radius ?? 20, 3, 150, 1, v => change('Heat radius', 'hr:' + L.id, () => { st.heat_radius = v; }))));
        box.append(row('Intensity', slider(st.heat_intensity ?? 1, 0.2, 5, 0.1, v => change('Heat intensity', 'hi:' + L.id, () => { st.heat_intensity = v; }), v => v.toFixed(1))));
        box.append(row('Colours', select(Object.keys(HEAT_RAMPS).map(k => [k, k[0].toUpperCase() + k.slice(1)]), st.heat_ramp || 'heat', v => change('Heat colours', 'hc:' + L.id, () => { st.heat_ramp = v; }))));
        box.append(row('Weight', select([['', 'Each feature counts 1'], ...numFields], st.heat_weight || '', v => change('Heat weight', 'hw:' + L.id, () => { st.heat_weight = v || null; }))));
      } else if (st.render === 'clusters') {
        box.append(el('div', { class: 'prop-group' }, 'Clusters'));
        box.append(row('Cluster size', slider(st.cluster_px ?? 56, 20, 200, 2, v => change('Cluster size', 'cp:' + L.id, () => { st.cluster_px = v; }))));
        colorRow('Colour', st.color, v => change('Colour', 'pc:' + L.id, () => { st.color = v; }));
      }
    }
    if (st.render === 'heatmap' || st.render === 'clusters') {
      box.append(el('div', { class: 'btn-row' }, el('button', { class: 'btn', onclick: () => ed.zoomToSelection() }, 'Zoom to layer')));
      return;
    }

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

  // ---------------------------------------------------------------- the page
  function pageProps(elk: string) {
    const page = ed.doc.page;
    const d = design(page);
    const setD = (k: string, label: string) => (v: any) => change(label, 'd:' + k, () => { page.design = { ...(page.design || {}), [k]: v }; });
    const sub = (name: string, label: string, k: string) => (v: any) => change(label, name + ':' + k, () => { page[name] = { ...(page[name] || {}), [k]: v }; });
    const head = (t: string, s = '') => box.append(el('div', { class: 'prop-title' }, t), el('div', { class: 'prop-sub' }, s));

    if (elk.startsWith('image:')) {
      const im = (page.images || []).find((x: any) => 'image:' + x.id === elk);
      if (!im) return;
      head('Picture', 'Drag it on the page; drag its lower right corner to resize.');
      box.append(row('Width (mm)', slider(im.w ?? 30, 3, 300, 0.5, v => change('Picture size', 'imw:' + im.id, () => { im.w = v; }))));
      box.append(row('Opacity %', slider(Math.round((im.opacity ?? 1) * 100), 5, 100, 1, v => change('Picture opacity', 'imo:' + im.id, () => { im.opacity = v / 100; }))));
      box.append(el('div', { class: 'btn-row' }, el('button', { class: 'btn danger', onclick: () => ed.deleteSelection() }, 'Remove picture')));
      return;
    }
    if (elk === 'legend') {
      const lg = page.legend || {};
      head('Legend', 'Lists the visible data layers, and labels and symbols marked “Show in the legend”. Drag it to move it.');
      box.append(row('', el('div', { class: 'checks' }, check('Show the legend', lg.show !== false, sub('legend', 'Legend', 'show')))));
      const t = el('input', { type: 'text', value: lg.title || '', placeholder: 'No heading (recommended)' });
      t.oninput = () => sub('legend', 'Legend heading', 'title')(t.value);
      box.append(row('Heading', t));
      box.append(row('Style', select(Object.entries(LEGEND_STYLES), d.legend_style, setD('legend_style', 'Legend style'))));
      box.append(row('Columns', select(['1', '2', '3', '4'].map(n => [n, n]), String(d.legend_columns || 1), v => setD('legend_columns', 'Legend columns')(Number(v)))));
      box.append(row('Text size (pt)', slider(Number(d.legend_text_size), 6, 16, 0.5, setD('legend_text_size', 'Legend text size'))));
      colorRow('Background', d.legend_bg, setD('legend_bg', 'Legend background'));
      box.append(row('Opacity %', slider(Math.round(Number(d.legend_opacity) * 100), 0, 100, 1, v => setD('legend_opacity', 'Legend opacity')(v / 100))));
      return;
    }
    if (elk === 'scalebar') {
      const sb = page.scalebar || {};
      head('Scale bar', 'A round-numbered bar, at most about a fifth of the map’s width. Drag it to move it.');
      box.append(row('', el('div', { class: 'checks' }, check('Show the scale bar', sb.show !== false, sub('scalebar', 'Scale bar', 'show')))));
      box.append(row('Style', select([['bar', 'Alternating bar'], ['hollow', 'Half filled'], ['line', 'Line with ticks']], sb.style || 'bar', sub('scalebar', 'Scale bar style', 'style'))));
      box.append(row('Units', select([['metric', 'Metres / kilometres'], ['imperial', 'Feet / miles']], sb.units || 'metric', sub('scalebar', 'Scale bar units', 'units'))));
      box.append(row('', el('div', { class: 'checks' }, check('Also write the scale (1 : 50 000)', !!d.scale_ratio, setD('scale_ratio', 'Scale ratio')))));
      box.append(row('Text size (pt)', slider(Number(d.scale_text_size), 5, 14, 0.5, setD('scale_text_size', 'Scale text size'))));
      return;
    }
    if (elk === 'north') {
      const nr = page.north || {};
      head('North arrow', 'Simple and small – north is up on these maps. Drag it to move it.');
      box.append(row('', el('div', { class: 'checks' }, check('Show the north arrow', nr.show !== false, sub('north', 'North arrow', 'show')))));
      box.append(row('Style', select(Object.entries(NORTH_STYLES), d.north_style, setD('north_style', 'North arrow style'))));
      colorRow('Colour', d.north_color, setD('north_color', 'North arrow colour'));
      return;
    }
    if (elk === 'credits') {
      const c = page.credits || {};
      head('Sources and credits', 'A small line under the map.');
      box.append(row('', el('div', { class: 'checks' }, check('Show the line', c.show !== false, sub('credits', 'Credits', 'show')))));
      const t = el('input', { type: 'text', value: c.text || '', placeholder: 'e.g. Map: your name, 2026' });
      t.oninput = () => sub('credits', 'Credits text', 'text')(t.value);
      box.append(row('Text', t));
      box.append(row('', el('div', { class: 'checks' }, check('Add the map sources automatically', c.auto !== false, sub('credits', 'Credits', 'auto')))));
      box.append(row('Text size (pt)', slider(Number(d.credits_size), 4, 12, 0.5, setD('credits_size', 'Credits size'))));
      return;
    }
    // the title, or the page as a whole
    const isTitle = elk === 'title';
    head(isTitle ? 'Title' : 'Page', isTitle ? 'Double-click the title on the page to edit it here.' : 'Paper, title and the map elements. Click an element on the page to change it.');
    const title = el('input', { type: 'text', id: 'prop-page-title', value: page.title || '', placeholder: 'A title for the map' });
    title.oninput = () => change('Title', 'ptitle', () => { page.title = title.value; });
    const subt = el('input', { type: 'text', value: page.subtitle || '', placeholder: 'Subtitle (optional)' });
    subt.oninput = () => change('Subtitle', 'psub', () => { page.subtitle = subt.value; });
    box.append(row('Title', title), row('Subtitle', subt));
    const ts = page.title_style || {};
    const setT = (k: string, label: string) => (v: any) => change(label, 'ts:' + k, () => { page.title_style = { ...(page.title_style || {}), [k]: v }; });
    const fonts: [string, string][] = Object.values(FONTS).flat().map(f => [f, f]);
    box.append(row('Title font', select(fonts, ts.font || 'Georgia', setT('font', 'Title font'))));
    box.append(row('Title size (pt)', slider(Number(ts.size ?? 22), 10, 60, 1, setT('size', 'Title size'))));
    colorRow('Title colour', ts.color || '#1d1d1d', setT('color', 'Title colour'));
    box.append(row('', el('div', { class: 'checks' }, check('Bold', ts.bold ?? true, setT('bold', 'Bold')), check('Italic', !!ts.italic, setT('italic', 'Italic')))));
    box.append(row('Align', select([['left', 'Left'], ['center', 'Centre']], page.title_align || 'left', v => change('Title align', 'talign', () => { page.title_align = v; }))));
    box.append(row('Position', select([['above', 'Above the map'], ['inside', 'Inside the map, top left']], d.title_position, setD('title_position', 'Title position'))));
    if (isTitle) return;

    box.append(el('div', { class: 'prop-group' }, 'Paper'));
    box.append(row('Size', select(Object.keys(PAPERS).map(k => [k, k]), page.paper || 'A4', v => change('Paper', 'paper', () => { page.paper = v; ed.fitPage(); }))));
    box.append(row('Orientation', select([['landscape', 'Landscape'], ['portrait', 'Portrait']], page.landscape !== false ? 'landscape' : 'portrait', v => change('Orientation', 'orient', () => { page.landscape = v === 'landscape'; ed.fitPage(); }))));
    box.append(row('Margin (mm)', slider(Number(page.margin_mm ?? 12), 0, 40, 1, v => change('Margin', 'margin', () => { page.margin_mm = v; }))));
    colorRow('Paper colour', page.background || '#ffffff', v => change('Paper colour', 'pbg', () => { page.background = v; }));

    box.append(el('div', { class: 'prop-group' }, 'Design'));
    const themes = el('div', { class: 'swatches', style: { gap: '6px' } }, ...Object.entries(THEMES).map(([k, th]: [string, any]) =>
      el('button', { class: 'btn' + (d.theme === k ? ' primary' : ''), style: { width: 'auto', height: 'auto', padding: '4px 8px' }, title: 'Apply this theme (also restyles labels and grey background maps)', onclick: () => ed.applyTheme(k) }, th.label)));
    box.append(row('Theme', themes));
    box.append(row('Font', select(fonts, d.font, setD('font', 'Element font'))));
    colorRow('Map background', d.map_bg, setD('map_bg', 'Map background'));
    box.append(row('', el('div', { class: 'checks' }, check('Frame line around the map', page.neatline !== false, v => change('Frame', 'neat', () => { page.neatline = v; })))));
    box.append(row('Frame', select([['single', 'Single line'], ['double', 'Double line']], d.frame_style, setD('frame_style', 'Frame style'))));
    colorRow('Frame colour', d.frame_color, setD('frame_color', 'Frame colour'));

    box.append(el('div', { class: 'prop-group' }, 'Map elements'));
    const elems: [string, string][] = [['legend', 'Legend'], ['scalebar', 'Scale bar'], ['north', 'North arrow'], ['credits', 'Sources and credits']];
    box.append(el('div', { class: 'elem-list' }, ...elems.map(([k, label]) => el('div', { class: 'checks', style: { justifyContent: 'space-between' } },
      check(label, (page[k] || {}).show !== false, sub(k, label, 'show')),
      el('button', { class: 'btn', style: { padding: '2px 8px' }, onclick: () => ed.selectElement(k) }, 'Settings…')))));
    box.append(el('div', { class: 'btn-row' },
      el('button', { class: 'btn', onclick: () => ed.insertPicture() }, 'Insert picture or logo…'),
      el('button', { class: 'btn primary', onclick: () => ed.exportDialog() }, 'Export…')));
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
    if (editingIn(box) && shownFor === key()) {
      const t = box.querySelector('.prop-title');
      const it = ed.selectedItem(), L = ed.selectedLayer();
      if (t) t.textContent = it ? itemTitle(it) : L ? L.name : 'Map';
      return;
    }
    render();
  });
  ed.on('page', render);
  ed.on('edit-title', () => {
    ed.selectElement('title');
    setTimeout(() => { const t = document.getElementById('prop-page-title') as HTMLInputElement | null; if (t) { t.focus(); t.select(); } }, 0);
  });
  ed.on('data-selection', () => { if (ed.selectedLayer()?.kind === 'data' && !ed.selItem && !editingIn(box)) render(); });
  ed.on('edit-text' as any, () => {
    // at once (so no keystroke is lost), and again after the click has finished in case it took the focus
    const go = () => {
      const t = document.getElementById('prop-text') as HTMLTextAreaElement | null;
      if (t && document.activeElement !== t) { t.focus(); t.select(); }
    };
    go();
    setTimeout(go, 0);
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
