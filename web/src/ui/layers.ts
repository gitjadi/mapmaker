// The Layers panel: layers top to bottom, with each annotation layer's items listed under it.
// Eye = show/hide, padlock = lock, drag to reorder (items can be dragged into another layer),
// double-click to rename. Opacity and blend mode for the selected layer sit above the list.

import type { Editor } from '../editor';
import { BLENDS, Item, Layer, WEB_KINDS, itemTitle } from '../doc';
import { BASEMAPS } from '../shared/basemaps';
import { el, select, slider } from './dom';
import { icon } from './icons';
import { openMenuAt } from './menus';

export function mountLayers(ed: Editor) {
  const list = document.getElementById('layers')!;
  const controls = document.getElementById('layer-controls')!;
  const buttons = document.getElementById('layer-buttons')!;
  const collapsed = new Set<string>();
  let renaming: string | null = null;
  let dragged: { kind: 'layer' | 'item'; id: string } | null = null;

  function renderControls() {
    controls.innerHTML = '';
    const L = ed.selectedLayer();
    const dis = !L;
    const op = slider(Math.round((L?.opacity ?? 1) * 100), 0, 100, 1, v => {
      const S = ed.selectedLayer();
      if (!S) return;
      ed.doc.checkpoint('Opacity', 'opacity:' + S.id);
      S.opacity = v / 100;
      ed.doc.changed();
    });
    const bl = select(Object.entries(BLENDS), L?.blend || 'normal', v => {
      const S = ed.selectedLayer();
      if (!S) return;
      ed.doc.checkpoint('Blend mode');
      S.blend = v;
      ed.doc.changed();
    });
    op.querySelectorAll('input').forEach(i => (i.disabled = dis));
    bl.disabled = dis;
    controls.append(el('label', {}, 'Blend', bl), el('label', {}, 'Opacity %', op));
  }

  function renderButtons() {
    buttons.innerHTML = '';
    const L = ed.selectedLayer();
    const i = L ? ed.doc.index(L.id) : -1;
    const b = (name: string, title: string, f: (e: MouseEvent) => void, disabled = false) =>
      el('button', { class: 'ico', title, 'aria-label': title, html: icon(name), disabled, onclick: f });
    buttons.append(
      b('plus', 'Add layer', e => openMenuAt(e.currentTarget as HTMLElement, addLayerMenu(ed))),
      b('copy', 'Duplicate (Ctrl+D)', () => ed.duplicate(), !L),
      b('up', 'Move up', () => move(1), !L || ed.selItem !== null || i >= ed.doc.layers.length - 1),
      b('down', 'Move down', () => move(-1), !L || ed.selItem !== null || i <= 0),
      el('span', { class: 'spacer' }),
      b('trash', 'Delete (Del)', () => ed.deleteSelection(), !L && !ed.selItem),
    );
    function move(d: number) {
      const S = ed.selectedLayer();
      if (!S) return;
      ed.doc.checkpoint('Move layer');
      ed.doc.moveLayer(S.id, ed.doc.index(S.id) + d);
      ed.doc.changed();
      ed.emit('selection');
    }
  }

  function layerRow(L: Layer) {
    const sel = ed.selLayer === L.id && !ed.selItem;
    const supported = WEB_KINDS.includes(L.kind);
    const r = el('div', {
      class: `layer${sel ? ' sel' : ''}${L.visible ? '' : ' hidden-layer'}${supported ? '' : ' unsupported'}`,
      draggable: renaming !== L.id, 'data-id': L.id,
      title: supported ? '' : 'This kind of layer isn’t shown in the browser version yet – it is kept and saved with the map.',
    });
    const eye = el('button', { class: 'ico' + (L.visible ? '' : ' off'), title: L.visible ? 'Hide' : 'Show', html: icon(L.visible ? 'eye' : 'eyeOff') });
    eye.onclick = e => { e.stopPropagation(); ed.doc.checkpoint(L.visible ? 'Hide layer' : 'Show layer'); L.visible = !L.visible; ed.doc.changed(); };
    const lock = el('button', { class: 'ico' + (L.locked ? '' : ' off'), title: L.locked ? 'Unlock' : 'Lock', html: icon(L.locked ? 'lock' : 'unlock') });
    lock.onclick = e => { e.stopPropagation(); ed.doc.checkpoint(L.locked ? 'Unlock layer' : 'Lock layer'); L.locked = !L.locked; ed.doc.changed(); };
    const hasItems = L.kind === 'annotations' && (L.items || []).length > 0;
    const caret = el('button', { class: 'ico', title: collapsed.has(L.id) ? 'Show items' : 'Hide items', html: hasItems ? icon(collapsed.has(L.id) ? 'caretRight' : 'caretDown') : '', style: { width: '16px' } });
    caret.onclick = e => { e.stopPropagation(); collapsed.has(L.id) ? collapsed.delete(L.id) : collapsed.add(L.id); render(); };
    const kindIcon = L.kind === 'basemap' ? 'basemap' : L.kind;
    const name = el('div', { class: 'name' });
    if (renaming === L.id) {
      const inp = el('input', { type: 'text', value: L.name });
      const finish = (ok: boolean) => {
        if (renaming !== L.id) return;
        renaming = null;
        const v = inp.value.trim();
        if (ok && v && v !== L.name) { ed.doc.checkpoint('Rename layer'); L.name = v; ed.doc.changed(); } else render();
      };
      inp.onkeydown = e => { e.stopPropagation(); if (e.key === 'Enter') finish(true); if (e.key === 'Escape') finish(false); };
      inp.onblur = () => finish(true);
      name.append(inp);
      setTimeout(() => { inp.focus(); inp.select(); });
    } else {
      name.textContent = L.name;
    }
    const count = L.kind === 'annotations' ? el('span', { class: 'badge' }, String((L.items || []).length)) : null;
    r.append(eye, lock, caret, el('span', { class: 'kind', html: icon(kindIcon) }), name, count || '');
    r.onclick = () => ed.select(L.id);
    r.ondblclick = () => { renaming = L.id; render(); };
    dnd(r, { kind: 'layer', id: L.id }, L);
    return r;
  }

  function itemRow(L: Layer, it: Item) {
    const sel = ed.selItem === it.id;
    const r = el('div', { class: `layer item${sel ? ' sel' : ''}${it.hidden ? ' hidden-layer' : ''}`, draggable: true, 'data-id': it.id });
    const eye = el('button', { class: 'ico' + (it.hidden ? ' off' : ''), title: it.hidden ? 'Show' : 'Hide', html: icon(it.hidden ? 'eyeOff' : 'eye') });
    eye.onclick = e => { e.stopPropagation(); ed.doc.checkpoint(it.hidden ? 'Show' : 'Hide'); it.hidden = !it.hidden; ed.doc.changed(); };
    const kind = { text: 'text', symbol: 'symbol', line: 'line', area: 'area' }[it.type as string] || 'data';
    r.append(eye, el('span', { class: 'kind', html: icon(kind) }), el('div', { class: 'name' }, itemTitle(it)));
    r.onclick = () => ed.select(L.id, it.id);
    r.ondblclick = () => ed.zoomToSelection();
    dnd(r, { kind: 'item', id: it.id }, L, it);
    return r;
  }

  // drag and drop: layers reorder; items reorder within or move into another annotation layer
  function dnd(r: HTMLElement, me: { kind: 'layer' | 'item'; id: string }, L: Layer, it?: Item) {
    r.addEventListener('dragstart', e => { dragged = me; e.dataTransfer!.effectAllowed = 'move'; e.dataTransfer!.setData('text/plain', me.id); });
    r.addEventListener('dragend', () => { dragged = null; clearDrop(); });
    r.addEventListener('dragover', e => {
      if (!dragged || dragged.id === me.id) return;
      const where = dropWhere(e, r, me, L);
      if (!where) return;
      e.preventDefault();
      clearDrop();
      r.classList.add('drop-' + where);
    });
    r.addEventListener('dragleave', () => r.classList.remove('drop-above', 'drop-below', 'drop-into'));
    r.addEventListener('drop', e => {
      if (!dragged) return;
      const where = dropWhere(e, r, me, L);
      clearDrop();
      if (!where) return;
      e.preventDefault();
      const d = dragged;
      dragged = null;
      if (d.kind === 'layer' && me.kind === 'layer') {
        // the list shows the top layer first, so "above" in the list = higher index
        ed.doc.checkpoint('Move layer');
        const target = ed.doc.index(me.id);
        const from = ed.doc.index(d.id);
        let to = where === 'above' ? target + 1 : target;
        if (from < to) to--;
        ed.doc.moveLayer(d.id, to);
        ed.doc.changed(); ed.emit('selection');
      } else if (d.kind === 'item') {
        ed.doc.checkpoint('Move item');
        if (me.kind === 'layer') ed.doc.moveItemTo(d.id, L.id, (L.items || []).length);
        else {
          const items: Item[] = L.items;
          const [srcL] = ed.doc.findItem(d.id);
          let idx = items.findIndex(x => x.id === it!.id);
          // items are listed top (drawn last) first
          idx = where === 'above' ? idx + 1 : idx;
          if (srcL === L && items.findIndex(x => x.id === d.id) < idx) idx--;
          ed.doc.moveItemTo(d.id, L.id, idx);
        }
        ed.selLayer = L.id;
        ed.doc.changed(); ed.emit('selection');
      }
    });
  }

  function dropWhere(e: DragEvent, r: HTMLElement, me: { kind: string }, L: Layer): 'above' | 'below' | 'into' | null {
    if (!dragged) return null;
    const half = e.offsetY < r.offsetHeight / 2;
    if (dragged.kind === 'layer') return me.kind === 'layer' ? (half ? 'above' : 'below') : null;
    if (L.kind !== 'annotations' || L.locked) return null;
    return me.kind === 'layer' ? 'into' : half ? 'above' : 'below';
  }

  const clearDrop = () => list.querySelectorAll('.drop-above, .drop-below, .drop-into').forEach(x => x.classList.remove('drop-above', 'drop-below', 'drop-into'));

  function render() {
    const scroll = list.scrollTop;
    list.innerHTML = '';
    if (!ed.doc.layers.length) list.append(el('div', { class: 'empty' }, 'No layers – add one with +'));
    for (const L of [...ed.doc.layers].reverse()) {
      list.append(layerRow(L));
      if (L.kind === 'annotations' && !collapsed.has(L.id)) for (const it of [...(L.items || [])].reverse()) list.append(itemRow(L, it));
    }
    list.scrollTop = scroll;
    // don't rebuild the slider while it is being dragged
    if (!controls.contains(document.activeElement) || controls.dataset.layer !== ed.selLayer) renderControls();
    controls.dataset.layer = ed.selLayer || '';
    renderButtons();
  }

  ed.on('change', render);
  ed.on('selection', render);
  render();
}

export function addLayerMenu(ed: Editor) {
  return [
    { label: 'Background maps' },
    ...BASEMAPS.map(b => ({ label: b.name, action: () => ed.addBasemap(b.id) })),
    { sep: true },
    { label: 'Plain colour', action: () => ed.addColorLayer() },
    { label: 'Map service (tiles / WMS)…', action: () => ed.addServiceDialog() },
    { sep: true },
    { label: 'Labels & symbols layer', action: () => ed.addAnnotationLayer() },
  ];
}
