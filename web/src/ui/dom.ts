// Small DOM helpers: element builder, form rows and modal dialogs.

type Attrs = Record<string, any>;

export function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs = {}, ...children: (Node | string | null | undefined | false)[]): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'html') e.innerHTML = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(e.style, v);
    else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
    else if (k in e && typeof v !== 'string') (e as any)[k] = v;
    else e.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children) if (c !== null && c !== undefined && c !== false) e.append(c);
  return e;
}

export const row = (label: string, ...control: (Node | string)[]) => el('label', { class: 'row' }, el('span', {}, label), ...control);

export function slider(value: number, min: number, max: number, step: number, onInput: (v: number) => void, fmt = (v: number) => String(v)) {
  const num = el('input', { type: 'number', min, max, step, value: fmt(value) });
  const rng = el('input', { type: 'range', min, max, step, value });
  rng.addEventListener('input', () => { num.value = fmt(Number(rng.value)); onInput(Number(rng.value)); });
  num.addEventListener('change', () => {
    const v = Math.max(min, Math.min(max, Number(num.value)));
    if (!Number.isFinite(v)) return;
    rng.value = String(v); num.value = fmt(v); onInput(v);
  });
  return el('div', { class: 'slider' }, rng, num);
}

export function select(options: [string, string][], value: string, onChange: (v: string) => void) {
  const s = el('select', { onchange: () => onChange(s.value) });
  for (const [v, label] of options) s.append(el('option', { value: v, selected: v === value }, label));
  return s;
}

export function colorInput(value: string, onInput: (v: string) => void) {
  const c = el('input', { type: 'color', value: normHex(value) });
  c.addEventListener('input', () => onInput(c.value));
  return c;
}

export function check(label: string, value: boolean, onChange: (v: boolean) => void) {
  const c = el('input', { type: 'checkbox', checked: !!value, onchange: () => onChange(c.checked) });
  return el('label', {}, c, label);
}

export function normHex(h: string | null | undefined): string {
  if (!h) return '#000000';
  if (/^#[0-9a-f]{3}$/i.test(h)) return '#' + [...h.slice(1)].map(c => c + c).join('');
  return /^#[0-9a-f]{6}/i.test(h) ? h.slice(0, 7) : '#000000';
}

// ---------------------------------------------------------------- dialogs
let closeCurrent: (() => void) | null = null;

/** Show a modal dialog. `buttons` resolve the promise with their value; Escape resolves null. */
export function dialog<T = string>(title: string, body: Node[], buttons: { label: string; value: T | null; primary?: boolean }[] = [{ label: 'OK', value: 'ok' as any, primary: true }]): Promise<T | null> {
  closeCurrent?.();
  const overlay = document.getElementById('overlay')!;
  return new Promise(resolve => {
    const done = (v: T | null) => {
      overlay.classList.remove('show');
      overlay.innerHTML = '';
      document.removeEventListener('keydown', onKey, true);
      closeCurrent = null;
      resolve(v);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(null); }
      if (e.key === 'Enter' && !(e.target instanceof HTMLTextAreaElement)) {
        const p = buttons.find(b => b.primary);
        if (p) { e.preventDefault(); e.stopPropagation(); done(p.value); }
      }
    };
    closeCurrent = () => done(null);
    const foot = el('div', { class: 'foot' }, ...buttons.map(b =>
      el('button', { class: 'btn' + (b.primary ? ' primary' : ''), onclick: () => done(b.value) }, b.label)));
    const box = el('div', { class: 'dialog', role: 'dialog', 'aria-label': title }, el('h2', {}, title), el('div', { class: 'body' }, ...body), foot);
    overlay.innerHTML = '';
    overlay.append(box);
    overlay.classList.add('show');
    overlay.onmousedown = e => { if (e.target === overlay) done(null); };
    document.addEventListener('keydown', onKey, true);
    (box.querySelector('input, select, textarea') as HTMLElement | null)?.focus();
  });
}

export const message = (title: string, text: string) => dialog(title, [el('p', {}, text)]);

export async function confirmDialog(title: string, text: string, ok = 'OK'): Promise<boolean> {
  return (await dialog(title, [el('p', {}, text)], [{ label: 'Cancel', value: null }, { label: ok, value: 'ok', primary: true }])) === 'ok';
}
