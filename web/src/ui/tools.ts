// The tool strip on the left and the options bar along the top.

import { Editor, TOOLS } from '../editor';
import { DASHES, TEXT_PRESETS } from '../shared/styles';
import { SYMBOLS } from '../shared/symbols';
import { symbolSvg } from '../render';
import { check, colorInput, el, select } from './dom';
import { icon } from './icons';
import { symbolPicker } from './properties';

export function mountTools(ed: Editor) {
  const bar = document.getElementById('toolbar')!;
  const opts = document.getElementById('optionsbar')!;
  const hint = document.getElementById('hint')!;
  let hintTimer = 0;

  function renderBar() {
    bar.innerHTML = '';
    TOOLS.forEach((t, i) => {
      if (i === 3) bar.append(el('div', { class: 'tool-sep' }));
      bar.append(el('button', {
        class: 'tool' + (ed.tool === t.id ? ' active' : ''), title: `${t.label} (${t.key})`, 'aria-label': t.label,
        html: icon(t.id), onclick: () => ed.setTool(t.id),
      }));
    });
  }

  function renderOptions() {
    opts.innerHTML = '';
    const t = TOOLS.find(x => x.id === ed.tool)!;
    const o = ed.opts;
    opts.append(el('span', { class: 'tool-name' }, t.label));
    const sep = () => el('span', { class: 'sep' });
    const lab = (text: string, ...c: Node[]) => el('label', {}, text, ...c);
    if (ed.tool === 'text') {
      opts.append(sep(), lab('Type style', select(Object.entries(TEXT_PRESETS).map(([k, v]) => [k, v.label]), o.preset, v => { o.preset = v; })));
    } else if (ed.tool === 'symbol') {
      const prev = el('img', { src: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(symbolSvg(o.symbol, o.symbolColor, null)), style: { width: '18px', height: '18px' } });
      const pick = el('button', { class: 'btn', style: { display: 'flex', alignItems: 'center', gap: '6px', padding: '2px 8px' } }, prev, SYMBOLS[o.symbol]?.label || o.symbol);
      pick.onclick = () => {
        const pop = el('div', { class: 'menu-list', style: { display: 'block', position: 'fixed', width: '280px', padding: '8px', maxHeight: '70vh', overflow: 'auto' } });
        const close = (ev?: MouseEvent) => { if (ev && pop.contains(ev.target as Node)) return; pop.remove(); document.removeEventListener('mousedown', close); };
        pop.append(symbolPicker(o.symbol, k => { o.symbol = k; close(); renderOptions(); }));
        const r = pick.getBoundingClientRect();
        pop.style.left = r.left + 'px'; pop.style.top = r.bottom + 4 + 'px';
        document.body.append(pop);
        setTimeout(() => document.addEventListener('mousedown', close));
      };
      opts.append(sep(), lab('Symbol', pick),
        lab('Colour', colorInput(o.symbolColor, v => { o.symbolColor = v; prev.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(symbolSvg(o.symbol, v, null)); })),
        lab('Size', numberInput(o.symbolSize, 8, 80, 1, v => { o.symbolSize = v; })),
        check('Badge', o.badge, v => { o.badge = v; }));
    } else if (ed.tool === 'line') {
      opts.append(sep(), lab('Colour', colorInput(o.lineColor, v => { o.lineColor = v; })),
        lab('Width', numberInput(o.lineWidth, 0.3, 16, 0.1, v => { o.lineWidth = v; })),
        lab('Dashes', select(Object.entries(DASHES), o.lineDash, v => { o.lineDash = v; })));
    } else if (ed.tool === 'area') {
      opts.append(sep(), lab('Fill', colorInput(o.areaFill, v => { o.areaFill = v; })));
    }
    opts.append(sep(), el('span', { style: { color: 'var(--faint)' } }, t.hint));
  }

  function numberInput(v: number, min: number, max: number, step: number, f: (v: number) => void) {
    const i = el('input', { type: 'number', value: v, min, max, step, style: { width: '64px' } });
    i.onchange = () => { const n = Math.max(min, Math.min(max, Number(i.value))); if (Number.isFinite(n)) f(n); };
    return i;
  }

  ed.on('tool', () => {
    renderBar(); renderOptions();
    const t = TOOLS.find(x => x.id === ed.tool)!;
    hint.textContent = t.hint;
    hint.classList.add('show');
    clearTimeout(hintTimer);
    hintTimer = window.setTimeout(() => hint.classList.remove('show'), 2600);
  });
  renderBar();
  renderOptions();
}
