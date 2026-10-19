// The menu bar and pop-up menus.

import type { Editor } from '../editor';
import { el } from './dom';
import { addLayerMenu } from './layers';

export interface MenuItem { label?: string; key?: string; action?: () => void; disabled?: boolean | (() => boolean); sep?: boolean }

const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
export const keyLabel = (k: string) => isMac ? k.replace('Ctrl+', '⌘').replace('Shift+', '⇧') : k;

function buildList(items: MenuItem[], close: () => void) {
  const list = el('div', { class: 'menu-list', role: 'menu' });
  for (const it of items) {
    if (it.sep) { list.append(el('div', { class: 'menu-sep' })); continue; }
    if (!it.action) { list.append(el('div', { class: 'menu-label' }, it.label || '')); continue; }
    const dis = typeof it.disabled === 'function' ? it.disabled() : !!it.disabled;
    list.append(el('button', {
      class: 'menu-item', role: 'menuitem', disabled: dis,
      onclick: () => { close(); it.action!(); },
    }, el('span', {}, it.label || ''), it.key ? el('span', { class: 'key' }, keyLabel(it.key)) : ''));
  }
  return list;
}

let openPopup: HTMLElement | null = null;
function closePopup() { openPopup?.remove(); openPopup = null; }
document.addEventListener('mousedown', e => {
  if (openPopup && !openPopup.contains(e.target as Node)) closePopup();
  document.querySelectorAll('.menu.open').forEach(m => { if (!m.contains(e.target as Node)) m.classList.remove('open'); });
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closePopup(); document.querySelectorAll('.menu.open').forEach(m => m.classList.remove('open')); }
});

/** A pop-up menu next to an element (e.g. the + under the Layers panel). */
export function openMenuAt(anchor: HTMLElement, items: MenuItem[]) {
  closePopup();
  const wrap = el('div', { class: 'menu open', style: { position: 'fixed', zIndex: '60' } });
  const list = buildList(items, closePopup);
  wrap.append(list);
  document.body.append(wrap);
  const r = anchor.getBoundingClientRect();
  const h = list.offsetHeight, w = list.offsetWidth;
  wrap.style.left = Math.min(window.innerWidth - w - 8, r.left) + 'px';
  wrap.style.top = (r.top - h - 4 > 8 ? r.top - h - 4 : r.bottom + 4) + 'px';
  list.style.top = '0';
  openPopup = wrap;
}

export interface MenuHooks { table: { toggle: () => void; open: (id?: string | null) => void } }

export function mountMenus(ed: Editor, hooks: MenuHooks) {
  const nav = document.getElementById('menus')!;
  const hasData = () => !!ed.dataTarget();
  const nsel = () => ed.selectedCount(ed.dataTarget()?.id);
  const menus: [string, () => MenuItem[]][] = [
    ['File', () => [
      { label: 'New map', key: 'Ctrl+Alt+N', action: () => ed.newMap() },
      { label: 'Open…', key: 'Ctrl+O', action: () => ed.openMap() },
      { label: 'Save', key: 'Ctrl+S', action: () => ed.saveMap() },
      { sep: true },
      { label: 'Add data from a file…', key: 'Ctrl+Shift+O', action: () => ed.importData() },
      { sep: true },
      { label: 'Export (PDF, PNG, JPEG)…', key: 'Ctrl+E', action: () => ed.exportDialog() },
    ]],
    ['Edit', () => [
      { label: ed.doc.canUndo() ? `Undo ${ed.doc.undoLabel()}` : 'Undo', key: 'Ctrl+Z', action: () => ed.undo(), disabled: !ed.doc.canUndo() },
      { label: ed.doc.canRedo() ? `Redo ${ed.doc.redoLabel()}` : 'Redo', key: 'Ctrl+Shift+Z', action: () => ed.redo(), disabled: !ed.doc.canRedo() },
      { sep: true },
      { label: 'Duplicate', key: 'Ctrl+D', action: () => ed.duplicate(), disabled: !ed.selLayer && !ed.selItem },
      { label: 'Delete', key: 'Del', action: () => ed.deleteSelection(), disabled: !ed.selLayer && !ed.selItem && !ed.selElement },
      { label: 'Deselect', key: 'Esc', action: () => ed.select(ed.selLayer, null), disabled: !ed.selItem },
    ]],
    ['Layer', () => addLayerMenu(ed)],
    ['Select', () => {
      const L = ed.dataTarget();
      const n = nsel();
      return [
        { label: L ? `In “${L.name}”${n ? ` – ${n.toLocaleString('sv-SE')} selected` : ''}` : 'No data layer yet' },
        { label: 'Select by attributes…', key: 'Ctrl+F', action: () => import('./select').then(m => m.selectByAttributes(ed)), disabled: !hasData() },
        { label: 'Select features tool (rectangle)', key: 'M', action: () => ed.setTool('features'), disabled: !hasData() },
        { label: 'Select all', key: 'Ctrl+A', action: () => ed.selectAll(), disabled: !hasData() },
        { label: 'Invert selection', key: 'Ctrl+I', action: () => ed.invertSelection(), disabled: !hasData() },
        { label: 'Clear selection', key: 'Ctrl+Shift+A', action: () => ed.clearSelection(), disabled: !n },
        { sep: true },
        { label: 'Zoom to selected', key: 'Ctrl+3', action: () => ed.zoomToSelected(), disabled: !n },
        { label: 'New layer from selection', action: () => ed.layerFromSelection(), disabled: !n },
        { label: 'Remove selected features from layer', action: () => ed.removeSelectedFeatures(), disabled: !n },
        { sep: true },
        { label: 'Attribute table', key: 'Ctrl+T', action: () => hooks.table.toggle(), disabled: !hasData() },
      ];
    }],
    ['Page', () => [
      { label: ed.pageMode ? 'Hide the page (map only)' : 'Show the page', key: 'Ctrl+L', action: () => ed.setPageMode(!ed.pageMode) },
      { label: 'Fit the page in the window', key: 'Ctrl+1', action: () => ed.fitPage(), disabled: !ed.pageMode },
      { label: 'Page setup and design…', action: () => { if (!ed.pageMode) ed.setPageMode(true); ed.selectElement('page'); } },
      { sep: true },
      { label: 'Insert picture or logo…', action: () => ed.insertPicture() },
      { sep: true },
      { label: 'Export the page…', key: 'Ctrl+E', action: () => ed.exportDialog() },
    ]],
    ['View', () => [
      { label: 'Zoom in', key: '+', action: () => ed.zoomBy(1) },
      { label: 'Zoom out', key: '−', action: () => ed.zoomBy(-1) },
      { label: 'Zoom to Sweden', key: 'Ctrl+0', action: () => ed.zoomToSweden() },
      { label: 'Zoom to selection', key: 'Ctrl+2', action: () => ed.zoomToSelection(), disabled: !ed.selLayer && !ed.selItem },
      { sep: true },
      { label: 'Attribute table', key: 'Ctrl+T', action: () => hooks.table.toggle(), disabled: !hasData() },
      { label: 'Show / hide side panels', key: 'Tab', action: () => { document.getElementById('app')!.classList.toggle('panels-hidden'); ed.resize(); } },
    ]],
    ['Help', () => [
      { label: 'Keyboard shortcuts', action: () => import('./help').then(m => m.showShortcuts()) },
      { label: 'About MapMaker', action: () => import('./help').then(m => m.showAbout()) },
    ]],
  ];
  for (const [name, items] of menus) {
    const m = el('div', { class: 'menu' });
    const btn = el('button', { 'aria-haspopup': 'menu' }, name);
    const open = () => {
      document.querySelectorAll('.menu.open').forEach(x => x.classList.remove('open'));
      m.querySelector('.menu-list')?.remove();
      m.append(buildList(items(), () => m.classList.remove('open')));
      m.classList.add('open');
    };
    btn.onmousedown = e => { e.preventDefault(); m.classList.contains('open') ? m.classList.remove('open') : open(); };
    btn.onmouseenter = () => { if (document.querySelector('.menu.open') && !m.classList.contains('open')) open(); };
    m.append(btn);
    nav.append(m);
  }
}
