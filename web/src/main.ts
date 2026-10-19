// MapMaker in the browser – start-up: the editor, its panels, keyboard shortcuts and the status bar.

import { Editor, TOOLS } from './editor';
import { moveItem } from './doc';
import { mountLayers } from './ui/layers';
import { mountMenus } from './ui/menus';
import { mountProperties } from './ui/properties';
import { mountTools } from './ui/tools';
import { el } from './ui/dom';
import { mountAttributeTable } from './ui/attrtable';
import { selectByAttributes } from './ui/select';

const canvas = document.getElementById('map') as HTMLCanvasElement;
const ed = new Editor(canvas);
(window as any).mapmaker = ed;            // handy in the browser console

const table = mountAttributeTable(ed);
mountMenus(ed, { table });
mountTools(ed);
mountLayers(ed);
mountProperties(ed);

// ---------------------------------------------------------------- title and status
const titleEl = document.getElementById('doc-title')!;
function renderTitle() {
  titleEl.textContent = ed.doc.title + (ed.doc.dirty ? ' •' : '');
  document.title = `${ed.doc.title} – MapMaker`;
}
titleEl.onclick = () => {
  const inp = el('input', { type: 'text', value: ed.doc.title, style: { width: '240px', height: '22px' } });
  titleEl.replaceChildren(inp);
  inp.focus(); inp.select();
  const done = (ok: boolean) => {
    const v = inp.value.trim();
    if (ok && v && v !== ed.doc.title) { ed.doc.checkpoint('Rename map'); ed.doc.title = v; ed.doc.changed(); }
    renderTitle();
  };
  inp.onkeydown = e => { e.stopPropagation(); if (e.key === 'Enter') inp.blur(); if (e.key === 'Escape') { inp.value = ed.doc.title; inp.blur(); } };
  inp.onblur = () => done(true);
};
ed.on('change', renderTitle);

const saveState = document.getElementById('save-state')!;
ed.on('saved' as any, () => { saveState.textContent = 'Kept in this browser'; });

// the Page button (top right): show the map on the printed page
const pageBtn = document.getElementById('page-toggle')!;
pageBtn.onclick = () => ed.setPageMode(!ed.pageMode);
const renderPageBtn = () => pageBtn.classList.toggle('on', ed.pageMode);
ed.on('page', renderPageBtn);
ed.on('change', renderPageBtn);

const pageZoom = document.getElementById('status-page')!;
const coords = document.getElementById('status-coords')!;
const scale = document.getElementById('status-scale')!;
const msg = document.getElementById('status-msg')!;
let msgTimer = 0;
ed.on('view', () => {
  const ll = ed.hoverLonlat();
  coords.textContent = ll ? `${ll[1].toFixed(5)}° N  ${ll[0].toFixed(5)}° E` : '';
  scale.textContent = ed.scaleText();
  pageZoom.textContent = ed.pageMode ? `Page ${ed.pagePercent()} %` : '';
});
ed.on('status', () => {
  const u = ed.unsupportedNames();
  msg.textContent = ed.statusMsg || (u ? `Not shown in the browser yet: ${u}` : '');
  clearTimeout(msgTimer);
  if (ed.statusMsg) msgTimer = window.setTimeout(() => { ed.statusMsg = ''; ed.emit('status'); }, 4000);
});

// ---------------------------------------------------------------- keyboard
const typing = (e: KeyboardEvent) => {
  const t = e.target as HTMLElement;
  return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
};

document.addEventListener('keydown', e => {
  if (document.getElementById('overlay')!.classList.contains('show')) return;
  const mod = e.ctrlKey || e.metaKey;
  const k = e.key.toLowerCase();
  if (mod) {
    const cmds: Record<string, () => void> = {
      z: () => (e.shiftKey ? ed.redo() : ed.undo()), y: () => ed.redo(), s: () => ed.saveMap(), o: () => ed.openMap(),
      e: () => ed.exportDialog(), d: () => ed.duplicate(), '0': () => ed.zoomToSweden(), '2': () => ed.zoomToSelection(),
      '3': () => ed.zoomToSelected(), '1': () => ed.fitPage(), l: () => ed.setPageMode(!ed.pageMode), t: () => table.toggle(),
      f: () => selectByAttributes(ed), i: () => ed.invertSelection(),
      a: () => (e.shiftKey ? ed.clearSelection() : ed.selectAll()),
    };
    if (e.altKey && k === 'n') { e.preventDefault(); ed.newMap(); return; }
    if (e.shiftKey && k === 'o') { e.preventDefault(); ed.importData(); return; }
    if (cmds[k] && !(typing(e) && ['z', 'y', 'a', 'i'].includes(k))) { e.preventDefault(); cmds[k](); }
    return;
  }
  if (typing(e)) return;
  if (e.key === ' ') { e.preventDefault(); ed.setSpace(true); return; }
  const tool = TOOLS.find(t => t.key.toLowerCase() === k);
  if (tool && !e.altKey) { ed.setTool(tool.id); return; }
  if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); ed.deleteSelection(); return; }
  if (e.key === 'Escape') { ed.drawing ? ed.cancelDrawing() : ed.selElement ? ed.selectElement(null) : ed.select(ed.selLayer, null); return; }
  if (e.key === 'Enter' && ed.drawing) { ed.finishDrawing(); return; }
  if (e.key === '+' || e.key === '=') { ed.zoomBy(0.5); return; }
  if (e.key === '-' || e.key === '_') { ed.zoomBy(-0.5); return; }
  if (e.key === 'Tab') { e.preventDefault(); document.getElementById('app')!.classList.toggle('panels-hidden'); ed.resize(); return; }
  const arrows: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
  if (arrows[e.key]) {
    e.preventDefault();
    const it = ed.selectedItem();
    const [dx, dy] = arrows[e.key].map(v => v * (e.shiftKey ? 10 : 1));
    if (it) {
      const [lon0, lat0] = ed.view.toLonlat(ed.view.width / 2, ed.view.height / 2);
      const [lon1, lat1] = ed.view.toLonlat(ed.view.width / 2 + dx, ed.view.height / 2 + dy);
      ed.doc.checkpoint('Nudge', 'nudge:' + it.id);
      moveItem(it, lon1 - lon0, lat1 - lat0);
      ed.doc.changed();
    } else {
      ed.view.cx += dx * 40 / ed.view.world; ed.view.cy += dy * 40 / ed.view.world;
      ed.saveView(); ed.redraw(); ed.emit('view');
    }
  }
});
document.addEventListener('keyup', e => { if (e.key === ' ') ed.setSpace(false); });
window.addEventListener('blur', () => ed.setSpace(false));

// open a .mapmaker file by dropping it on the window
window.addEventListener('dragover', e => { if (e.dataTransfer?.types.includes('Files')) e.preventDefault(); });
window.addEventListener('drop', e => {
  const files = [...(e.dataTransfer?.files || [])];
  if (!files.length) return;
  e.preventDefault();
  const map = files.find(f => /\.mapmaker$/i.test(f.name));
  if (map) ed.openMap(map); else ed.importData(files);
});

// keep the map when the tab is hidden or closed, without waiting for the autosave timer
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') ed.autosaveNow(); });
window.addEventListener('pagehide', () => { ed.autosaveNow(); });

// ---------------------------------------------------------------- start
(async () => {
  const restored = await ed.restoreAutosave();
  if (!restored) ed.zoomToSweden();
  renderTitle();
  renderPageBtn();
  ed.emit('view');
  ed.emit('tool');
  if (restored) ed.status('Carried on where you left off');
})();
