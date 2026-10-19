// Resizing the side panels: drag the left edge of the sidebar to make it wider or narrower, and the
// line between Properties and Layers to give one more room. Double-click a handle to reset it.
// The sizes are remembered in this browser.

import { el } from './dom';

const KEY = 'mapmaker.sidebar';
const DEFAULT_W = 300;
const MIN_W = 220, MIN_PROPS = 110, MIN_LAYERS = 210;   // Layers keeps room for its controls and a few rows

function load(): { w?: number; h?: number } {
  try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { return {}; }
}
function store(v: { w?: number; h?: number }) {
  try { localStorage.setItem(KEY, JSON.stringify(v)); } catch { /* private window: not remembered */ }
}

export function mountSidebarResize() {
  const app = document.getElementById('app')!;
  const sidebar = document.getElementById('sidebar')!;
  const props = document.getElementById('properties-panel')!;
  const state = load();

  const maxW = () => Math.max(MIN_W, Math.min(900, window.innerWidth - 360));
  const setW = (w: number | undefined) => {
    if (w === undefined) app.style.removeProperty('--side-w');
    else app.style.setProperty('--side-w', Math.round(Math.max(MIN_W, Math.min(maxW(), w))) + 'px');
  };
  const setH = (h: number | undefined) => {
    if (h === undefined) { props.style.removeProperty('flex'); return; }
    const max = sidebar.clientHeight - MIN_LAYERS - 6;
    props.style.flex = `0 0 ${Math.round(Math.max(MIN_PROPS, Math.min(max, h)))}px`;
  };
  setW(state.w);
  requestAnimationFrame(() => setH(state.h));

  // the sidebar's left edge
  const edge = el('div', { class: 'side-grip', title: 'Drag to make the panels wider or narrower · double-click to reset' });
  sidebar.append(edge);
  drag(edge, 'ew', (e0, e) => {
    const w = sidebar.getBoundingClientRect().right - e.clientX;
    setW(w); state.w = parseInt(app.style.getPropertyValue('--side-w')) || DEFAULT_W;
  }, () => store(state));
  edge.ondblclick = () => { setW(undefined); delete state.w; store(state); };

  // between Properties and Layers
  const split = el('div', { class: 'split-grip', title: 'Drag to give Properties or Layers more room · double-click to reset' });
  props.after(split);
  drag(split, 'ns', (_e0, e) => {
    const h = e.clientY - props.getBoundingClientRect().top;
    setH(h); state.h = Math.round(props.getBoundingClientRect().height) || undefined;
  }, () => store(state));
  split.ondblclick = () => { setH(undefined); delete state.h; store(state); };

  window.addEventListener('resize', () => { if (state.w) setW(state.w); if (state.h) setH(state.h); });
}

function drag(handle: HTMLElement, dir: 'ew' | 'ns', move: (start: PointerEvent, e: PointerEvent) => void, done: () => void) {
  handle.addEventListener('pointerdown', start => {
    start.preventDefault();
    handle.setPointerCapture(start.pointerId);
    document.body.classList.add('resizing-' + dir);
    const mv = (e: PointerEvent) => move(start, e);
    const up = () => {
      handle.removeEventListener('pointermove', mv);
      handle.removeEventListener('pointerup', up);
      handle.removeEventListener('pointercancel', up);
      document.body.classList.remove('resizing-' + dir);
      done();
    };
    handle.addEventListener('pointermove', mv);
    handle.addEventListener('pointerup', up);
    handle.addEventListener('pointercancel', up);
  });
}
