// Map tiles: sources (XYZ and WMS in Web Mercator) and an in-memory cache of loaded images.
// Tiles are requested with CORS so the canvas can be exported; a server that refuses CORS still
// shows on screen, but makes PNG export impossible (the browser "taints" the canvas).

export interface TileSourceDef {
  id: string; name: string; urls: string[]; kind: string; max_zoom: number; min_zoom: number;
  attribution: string; wms_layers: string; user?: string | null; password?: string | null; note: string;
  role: string; user_defined: boolean; labels: string[]; crs: number; tile_size: number;
}

export function tileUrl(src: Partial<TileSourceDef> & { urls: string[] }, z: number, x: number, y: number): string {
  const tmpl = src.urls[(x + y) % src.urls.length];
  if (src.kind === 'wms') {
    const half = 20037508.342789244;
    const size = 2 * half / 2 ** z;
    const minx = -half + x * size, maxy = half - y * size;
    const sep = tmpl.includes('?') ? '&' : '?';
    return `${tmpl}${sep}SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&LAYERS=${encodeURIComponent(src.wms_layers || '')}` +
      `&STYLES=&FORMAT=image/png&TRANSPARENT=true&CRS=EPSG:3857&WIDTH=256&HEIGHT=256&BBOX=${minx},${maxy - size},${minx + size},${maxy}`;
  }
  return tmpl.replace('{z}', String(z)).replace('{x}', String(x)).replace('{y}', String(y))
    .replace('{-y}', String(2 ** z - 1 - y)).replace('{s}', 'abc'[(x + y) % 3]);
}

interface Entry { img: HTMLImageElement; ok: boolean; failed: boolean; tainted: boolean; used: number }

export class TileCache {
  private map = new Map<string, Entry>();
  private pending = 0;
  private idleWaiters: (() => void)[] = [];
  onLoad: (() => void) | null = null;
  limit = 1500;

  /** The image if loaded, otherwise starts loading it and returns null. */
  get(url: string): HTMLImageElement | null {
    let e = this.map.get(url);
    if (e) { e.used = performance.now(); return e.ok ? e.img : null; }
    e = { img: new Image(), ok: false, failed: false, tainted: false, used: performance.now() };
    this.map.set(url, e);
    this.load(url, e, true);
    this.trim();
    return null;
  }

  has(url: string) { return !!this.map.get(url)?.ok; }
  failed(url: string) { return !!this.map.get(url)?.failed; }
  /** True if any tile on screen came from a server without CORS. */
  isTainted(urls: string[]) { return urls.some(u => this.map.get(u)?.tainted); }

  private load(url: string, e: Entry, cors: boolean) {
    this.pending++;
    const img = new Image();
    if (cors) img.crossOrigin = 'anonymous';
    img.decoding = 'async';
    img.onload = () => {
      e.img = img; e.ok = true; e.tainted = !cors;
      this.done();
    };
    img.onerror = () => {
      if (cors) { this.pending--; this.load(url, e, false); return; }   // try again without CORS
      e.failed = true;
      this.done();
    };
    img.src = url;
  }

  private done() {
    this.pending--;
    this.onLoad?.();
    if (this.pending <= 0) { this.pending = 0; this.idleWaiters.splice(0).forEach(f => f()); }
  }

  /** Resolves when no tiles are loading (or after `timeout` ms). */
  whenIdle(timeout = 20000): Promise<void> {
    if (this.pending === 0) return Promise.resolve();
    return new Promise(res => {
      const t = setTimeout(res, timeout);
      this.idleWaiters.push(() => { clearTimeout(t); res(); });
    });
  }

  private trim() {
    if (this.map.size <= this.limit) return;
    const old = [...this.map.entries()].filter(([, e]) => e.ok || e.failed).sort((a, b) => a[1].used - b[1].used);
    for (const [k] of old.slice(0, this.map.size - this.limit)) this.map.delete(k);
  }
}
