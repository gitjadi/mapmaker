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

/** A file-safe name from a map title. */
export const safeName = (s: string) => (s || 'Untitled map').replace(/[\\/:*?"<>|]+/g, ' ').trim() || 'map';
