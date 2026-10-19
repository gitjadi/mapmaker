// Select by attributes – like ArcGIS's: a data layer, a field, a condition and a value (the list
// offers the layer's own values, most common first), and how it combines with the current selection.

import type { Editor } from '../editor';
import { NO_VALUE_OPS, OPS, colCounts, combine, countMask, query } from '../table';
import { dialog, el, row, select } from './dom';

let last: { layer?: string; field?: string; op: string; value: string; method: string } = { op: 'eq', value: '', method: 'new' };

export async function selectByAttributes(ed: Editor) {
  const layers = ed.doc.layers.filter(L => L.kind === 'data' && ed.doc.table(L)).reverse();
  if (!layers.length) { ed.status('Add a data layer first (Layer → Data from a file…)'); return; }
  const target = ed.dataTarget() || layers[0];
  const layerSel = select(layers.map(L => [L.id, `${L.name} (${ed.doc.table(L)!.n.toLocaleString('sv-SE')})`]), target.id, () => fillFields());
  const fieldSel = el('select', {});
  const opSel = select(OPS, last.op, () => updValue());
  const value = el('input', { type: 'text', list: 'sel-values', placeholder: 'Value' });
  value.value = last.value;
  const values = el('datalist', { id: 'sel-values' });
  const methodSel = select([['new', 'New selection'], ['add', 'Add to the selection'], ['remove', 'Remove from the selection'], ['within', 'Select within the selection']], last.method, () => {});
  const result = el('p', { class: 'prop-sub' });
  const valueRow = row('Value', value);

  function fillFields() {
    const t = ed.doc.table(ed.doc.layer(layerSel.value))!;
    fieldSel.innerHTML = '';
    for (const f of t.fields) fieldSel.append(el('option', { value: f, selected: f === last.field }, f));
    fillValues();
    const cur = countMask(ed.selectionOf(layerSel.value));
    result.textContent = cur ? `${cur.toLocaleString('sv-SE')} of ${t.n.toLocaleString('sv-SE')} selected now.` : `${t.n.toLocaleString('sv-SE')} features.`;
  }
  function fillValues() {
    const t = ed.doc.table(ed.doc.layer(layerSel.value))!;
    const c = t.cols[fieldSel.value];
    values.innerHTML = '';
    if (!c) return;
    for (const [v, n] of colCounts(c, 300)) values.append(el('option', { value: String(v), label: `${v}  (${n.toLocaleString('sv-SE')})` }));
  }
  function updValue() { valueRow.style.display = NO_VALUE_OPS.includes(opSel.value) ? 'none' : ''; }
  fieldSel.onchange = fillValues;
  fillFields();
  updValue();

  const r = await dialog('Select by attributes', [
    row('Layer', layerSel), row('Field', fieldSel), row('Condition', opSel), valueRow, values,
    row('Method', methodSel), result,
    el('p', {}, 'Text is compared without regard to case. “is one of” takes values separated by commas or semicolons.'),
  ], [{ label: 'Cancel', value: null }, { label: 'Apply', value: 'ok', primary: true }]);
  if (r !== 'ok') return;
  last = { layer: layerSel.value, field: fieldSel.value, op: opSel.value, value: value.value, method: methodSel.value };
  const L = ed.doc.layer(layerSel.value)!;
  const t = ed.doc.table(L)!;
  let m: Uint8Array;
  try { m = query(t, fieldSel.value, opSel.value, value.value); }
  catch (e: any) { ed.status(e.message); return; }
  const out = combine(ed.selectionOf(L.id), m, methodSel.value);
  if (ed.selLayer !== L.id) ed.select(L.id);
  ed.setSelection(L.id, out);
}
