// Autosave in the browser (IndexedDB), and downloading / opening files.

import type { ZipEntry } from './zip';

const DB = 'mapmaker', STORE = 'kv';

function open(): Promise<IDBDatabase> {
  return new Promise((res, rej) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE);
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}

async function tx<T>(mode: IDBTransactionMode, f: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  return new Promise((res, rej) => {
    const t = db.transaction(STORE, mode);
    const r = f(t.objectStore(STORE));
    t.oncomplete = () => { res(r.result); db.close(); };
    t.onerror = () => { rej(t.error); db.close(); };
  });
}

export interface Autosave { body: any; extra: ZipEntry[]; fileName: string | null; savedAt: number }

export async function saveAutosave(a: Autosave) {
  try { await tx('readwrite', s => s.put(a, 'autosave')); } catch (e) { console.warn('Autosave failed', e); }
}

export async function loadAutosave(): Promise<Autosave | null> {
  try { return (await tx<Autosave | undefined>('readonly', s => s.get('autosave'))) || null; } catch { return null; }
}

export function downloadBlob(blob: Blob, name: string) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

export function pickFile(accept: string): Promise<File | null> {
  return new Promise(res => {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = accept;
    inp.onchange = () => res(inp.files?.[0] || null);
    inp.addEventListener('cancel', () => res(null));
    inp.click();
  });
}

export function pickFiles(accept: string): Promise<File[] | null> {
  return new Promise(res => {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = accept;
    inp.multiple = true;
    inp.onchange = () => res(inp.files ? [...inp.files] : null);
    inp.addEventListener('cancel', () => res(null));
    inp.click();
  });
}

/** A file-safe name from a map title. */
export const safeName = (s: string) => (s || 'Untitled map').replace(/[\\/:*?"<>|]+/g, ' ').trim() || 'map';

// Tables never change once made, so each is stored once under its own key.
const savedTables = new Set<string>();

export async function saveTable(id: string, entries: ZipEntry[]) {
  if (savedTables.has(id)) return;
  try { await tx('readwrite', s => s.put(entries, 'table:' + id)); savedTables.add(id); }
  catch (e) { console.warn('Could not keep the table in the browser', e); }
}

export async function loadTable(id: string): Promise<ZipEntry[] | null> {
  try { const r = await tx<ZipEntry[] | undefined>('readonly', s => s.get('table:' + id)); if (r) savedTables.add(id); return r || null; }
  catch { return null; }
}

/** Remove kept tables no map uses any more. */
export async function pruneTables(keep: Set<string>) {
  try {
    const keys = await tx<IDBValidKey[]>('readonly', s => s.getAllKeys());
    for (const k of keys) {
      const id = String(k).startsWith('table:') ? String(k).slice(6) : null;
      if (id && !keep.has(id)) { await tx('readwrite', s => s.delete(k)); savedTables.delete(id); }
    }
  } catch { /* not important */ }
}
