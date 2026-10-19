// A minimal PDF writer: one picture per page, stored losslessly (Flate) or as JPEG.
// Enough for exporting map pages at print resolution without a PDF library.

export interface PdfPage { canvas: HTMLCanvasElement; widthPt: number; heightPt: number; jpeg?: boolean }

async function zlib(data: Uint8Array): Promise<Uint8Array> {
  const s = new Blob([data as BlobPart]).stream().pipeThrough(new CompressionStream('deflate'));
  return new Uint8Array(await new Response(s).arrayBuffer());
}

async function imageStream(canvas: HTMLCanvasElement, jpeg: boolean): Promise<{ filter: string; data: Uint8Array }> {
  if (jpeg) {
    const blob = await new Promise<Blob | null>(r => canvas.toBlob(r, 'image/jpeg', 0.93));
    if (!blob) throw new Error('The browser couldn’t make the picture.');
    return { filter: '/DCTDecode', data: new Uint8Array(await blob.arrayBuffer()) };
  }
  const { width: w, height: h } = canvas;
  const rgba = canvas.getContext('2d')!.getImageData(0, 0, w, h).data;
  const rgb = new Uint8Array(w * h * 3);
  // flatten onto white (PDF pictures have no transparency here)
  for (let i = 0, j = 0; i < rgba.length; i += 4, j += 3) {
    const a = rgba[i + 3] / 255;
    rgb[j] = rgba[i] * a + 255 * (1 - a); rgb[j + 1] = rgba[i + 1] * a + 255 * (1 - a); rgb[j + 2] = rgba[i + 2] * a + 255 * (1 - a);
  }
  return { filter: '/FlateDecode', data: await zlib(rgb) };
}

export async function makePdf(pages: PdfPage[], title = 'Map'): Promise<Blob> {
  const enc = new TextEncoder();
  const parts: Uint8Array[] = [];
  const offsets: number[] = [];
  let pos = 0;
  const push = (b: Uint8Array | string) => { const u = typeof b === 'string' ? enc.encode(b) : b; parts.push(u); pos += u.length; };
  const obj = (n: number, body: string | Uint8Array[]) => {
    offsets[n] = pos;
    push(`${n} 0 obj\n`);
    if (typeof body === 'string') push(body); else for (const b of body) push(b);
    push('\nendobj\n');
  };
  push('%PDF-1.4\n%\xe2\xe3\xcf\xd3\n');
  // 1 catalog, 2 pages, 3 info, then per page: page, content, image
  const kids = pages.map((_, i) => `${4 + i * 3} 0 R`).join(' ');
  obj(1, '<< /Type /Catalog /Pages 2 0 R >>');
  obj(2, `<< /Type /Pages /Kids [${kids}] /Count ${pages.length} >>`);
  // the title as UTF-16 (so å, ä, ö come through)
  const utf16 = (t: string) => '<FEFF' + [...t].map(ch => { const c = ch.codePointAt(0)!; const u = c > 0xffff ? String.fromCharCode(0xd800 + ((c - 0x10000) >> 10), 0xdc00 + ((c - 0x10000) & 0x3ff)) : ch; return [...u].map(x => x.charCodeAt(0).toString(16).padStart(4, '0')).join(''); }).join('').toUpperCase() + '>';
  obj(3, `<< /Title ${utf16(title)} /Producer (MapMaker web) >>`);
  for (let i = 0; i < pages.length; i++) {
    const p = pages[i];
    const n = 4 + i * 3;
    const img = await imageStream(p.canvas, !!p.jpeg);
    const W = p.widthPt.toFixed(2), H = p.heightPt.toFixed(2);
    obj(n, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${W} ${H}] /Contents ${n + 1} 0 R /Resources << /XObject << /Im${i} ${n + 2} 0 R >> >> >>`);
    const content = `q ${W} 0 0 ${H} 0 0 cm /Im${i} Do Q`;
    obj(n + 1, `<< /Length ${content.length} >>\nstream\n${content}\nendstream`);
    obj(n + 2, [enc.encode(`<< /Type /XObject /Subtype /Image /Width ${p.canvas.width} /Height ${p.canvas.height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter ${img.filter} /Length ${img.data.length} >>\nstream\n`), img.data, enc.encode('\nendstream')]);
  }
  const n = 4 + pages.length * 3;
  const xref = pos;
  let x = `xref\n0 ${n}\n0000000000 65535 f \n`;
  for (let i = 1; i < n; i++) x += String(offsets[i]).padStart(10, '0') + ' 00000 n \n';
  push(x);
  push(`trailer\n<< /Size ${n} /Root 1 0 R /Info 3 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  return new Blob(parts as BlobPart[], { type: 'application/pdf' });
}
