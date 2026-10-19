// Exporting: the page (PNG, JPEG or PDF at 150–600 dpi) or the map view as a picture.

import type { Editor } from './editor';
import { renderMap, symbolsReady } from './render';
import { drawPage, frameView, pageGeometry, paperMm, picturesReady } from './page';
import { makePdf } from './pdf';
import { downloadBlob, safeName } from './storage';
import { dialog, el, message, row, select } from './ui/dom';

const MAX_PIXELS = 120e6;     // browsers refuse much bigger canvases

export async function exportDialog(ed: Editor) {
  const page = ed.pageMode;
  const what = select([['page', 'The page (title, legend, scale bar …)'], ['view', 'The map view as it is on screen']], page ? 'page' : 'view', () => upd());
  const fmt = select([['pdf', 'PDF'], ['png', 'PNG picture'], ['jpeg', 'JPEG picture']], page ? 'pdf' : 'png', () => upd());
  const dpi = select([['150', '150 dpi (screen, e-mail)'], ['300', '300 dpi (print)'], ['600', '600 dpi (fine print)']], '300', () => upd());
  const scale = select([['1', 'Screen size (1×)'], ['2', 'Double (2×)'], ['3', 'Triple (3×)'], ['4', 'Large (4×)']], '2', () => upd());
  const credits = el('input', { type: 'checkbox', checked: true });
  const dpiRow = row('Resolution', dpi), scaleRow = row('Size', scale), credRow = row('Credits', el('label', { class: 'checks' }, credits, 'Write map credits in the corner'));
  const info = el('p', {});
  const upd = () => {
    const isPage = what.value === 'page';
    dpiRow.style.display = isPage ? '' : 'none';
    scaleRow.style.display = isPage ? 'none' : '';
    credRow.style.display = isPage ? 'none' : '';
    if (isPage) {
      const [W, H] = paperMm(ed.doc.page);
      const s = Number(dpi.value) / 25.4;
      info.textContent = `${ed.doc.page.paper || 'A4'} ${ed.doc.page.landscape !== false ? 'landscape' : 'portrait'} · ${Math.round(W * s)} × ${Math.round(H * s)} pixels`;
    } else {
      const s = Number(scale.value);
      info.textContent = `${Math.round(ed.cw * s)} × ${Math.round(ed.ch * s)} pixels`;
    }
  };
  upd();
  const r = await dialog('Export', [
    row('Export', what), row('Format', fmt), dpiRow, scaleRow, credRow, info,
  ], [{ label: 'Cancel', value: null }, { label: 'Export', value: 'ok', primary: true }]);
  if (r !== 'ok') return;
  try {
    if (what.value === 'page') await exportPage(ed, Number(dpi.value), fmt.value as any);
    else await exportView(ed, Number(scale.value), fmt.value as any, credits.checked);
  } catch (e: any) {
    ed.status('');
    message('Export failed', e?.message || String(e));
  }
}

async function finish(ed: Editor, canvas: HTMLCanvasElement, fmt: 'png' | 'jpeg' | 'pdf', widthPt: number, heightPt: number, urls: string[]) {
  if (ed.tiles.isTainted(urls)) {
    message('Can’t export this map', 'One of the map services on this map doesn’t allow its tiles to be used in pictures made by other websites (CORS). Hide that layer and try again.');
    ed.status('');
    return;
  }
  const base = safeName(ed.doc.title || ed.doc.page.title);
  if (fmt === 'pdf') {
    const blob = await makePdf([{ canvas, widthPt, heightPt }], ed.doc.page.title || ed.doc.title);
    downloadBlob(blob, base + '.pdf');
    ed.status(`Exported ${base}.pdf`);
    return;
  }
  const blob = await new Promise<Blob | null>(res => canvas.toBlob(res, fmt === 'jpeg' ? 'image/jpeg' : 'image/png', 0.93));
  if (!blob) throw new Error('The browser couldn’t make the picture – try a lower resolution.');
  const name = `${base}.${fmt === 'jpeg' ? 'jpg' : 'png'}`;
  downloadBlob(blob, name);
  ed.status(`Exported ${name}`);
}

/** Render the page at `dpi` into a new canvas (waiting for tiles, symbols and pictures). */
export async function renderPage(ed: Editor, dpi: number): Promise<{ canvas: HTMLCanvasElement; urls: string[] }> {
  const doc = ed.doc;
  const [W, H] = paperMm(doc.page);
  let s = dpi / 25.4;
  if (W * s * H * s > MAX_PIXELS) s = Math.sqrt(MAX_PIXELS / (W * H));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(W * s); canvas.height = Math.round(H * s);
  const ctx = canvas.getContext('2d')!;
  const geo = pageGeometry(doc, 0, 0, s);
  const view = frameView(doc, geo);
  const mc = document.createElement('canvas');
  mc.width = Math.max(1, Math.round(geo.frame.w)); mc.height = Math.max(1, Math.round(geo.frame.h));
  const mctx = mc.getContext('2d')!;
  const urls: string[] = [];
  const pass = () => {
    urls.length = 0;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawPage(ctx, doc, geo, () => {
      mctx.setTransform(1, 0, 0, 1, 0, 0);
      mctx.clearRect(0, 0, mc.width, mc.height);
      renderMap(mctx, view, doc, ed.tiles, { dpr: 1, k: geo.k }, urls);
      ctx.drawImage(mc, geo.frame.x, geo.frame.y, geo.frame.w, geo.frame.h);
    });
  };
  pass();
  await ed.tiles.whenIdle();
  await symbolsReady();
  await picturesReady(doc);
  pass();
  return { canvas, urls };
}

async function exportPage(ed: Editor, dpi: number, fmt: 'png' | 'jpeg' | 'pdf') {
  ed.status('Exporting the page…');
  await new Promise(r => setTimeout(r, 30));
  const { canvas, urls } = await renderPage(ed, dpi);
  const [W, H] = paperMm(ed.doc.page);
  await finish(ed, canvas, fmt, W * 72 / 25.4, H * 72 / 25.4, urls);
}

async function exportView(ed: Editor, scale: number, fmt: 'png' | 'jpeg' | 'pdf', credits: boolean) {
  ed.status('Exporting…');
  const v = ed.view.clone();
  // the map view without the page: the document's view over the whole window
  const [cx, cy, z] = ed.doc.view;
  v.cx = cx; v.cy = cy; v.zoom = z; v.width = ed.cw; v.height = ed.ch;
  if (v.width * scale * v.height * scale > MAX_PIXELS) scale = Math.sqrt(MAX_PIXELS / (v.width * v.height));
  const out = document.createElement('canvas');
  out.width = Math.round(v.width * scale); out.height = Math.round(v.height * scale);
  const ctx = out.getContext('2d')!;
  const urls: string[] = [];
  const pass = () => {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, out.width, out.height);
    urls.length = 0;
    renderMap(ctx, v, ed.doc, ed.tiles, { dpr: scale }, urls);
  };
  pass();
  await ed.tiles.whenIdle();
  await symbolsReady();
  pass();
  if (credits) {
    const t = ed.attribution();
    if (t) {
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      ctx.font = '10px "Segoe UI", Arial, sans-serif';
      const w = ctx.measureText(t).width + 12;
      ctx.fillStyle = 'rgba(255,255,255,0.75)';
      ctx.fillRect(v.width - w, v.height - 15, w, 15);
      ctx.fillStyle = '#333'; ctx.textBaseline = 'middle';
      ctx.fillText(t, v.width - w + 6, v.height - 7.5);
    }
  }
  await finish(ed, out, fmt, v.width * 0.75, v.height * 0.75, urls);
}
