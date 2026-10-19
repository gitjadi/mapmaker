// The map view (Web Mercator) and coordinate conversions – the same model as render.py:
// cx, cy = centre in unit Web Mercator (0..1), zoom = the world is 256 * 2^zoom CSS pixels wide.

export const TILE = 256;
const MAX_LAT = 85.05112878;

export function lonlatToMerc(lon: number, lat: number): [number, number] {
  const la = Math.max(-MAX_LAT, Math.min(MAX_LAT, lat)) * Math.PI / 180;
  return [(lon + 180) / 360, (1 - Math.log(Math.tan(la) + 1 / Math.cos(la)) / Math.PI) / 2];
}

export function mercToLonlat(mx: number, my: number): [number, number] {
  return [mx * 360 - 180, Math.atan(Math.sinh(Math.PI * (1 - 2 * my))) * 180 / Math.PI];
}

export class View {
  constructor(public cx: number, public cy: number, public zoom: number,
              public width: number, public height: number) {}

  get world() { return TILE * 2 ** this.zoom; }

  toPx(mx: number, my: number): [number, number] {
    const w = this.world;
    return [(mx - this.cx) * w + this.width / 2, (my - this.cy) * w + this.height / 2];
  }

  lonlatToPx(lon: number, lat: number): [number, number] {
    const [mx, my] = lonlatToMerc(lon, lat);
    return this.toPx(mx, my);
  }

  toMerc(px: number, py: number): [number, number] {
    const w = this.world;
    return [(px - this.width / 2) / w + this.cx, (py - this.height / 2) / w + this.cy];
  }

  toLonlat(px: number, py: number): [number, number] {
    const [mx, my] = this.toMerc(px, py);
    return mercToLonlat(mx, my);
  }

  metersPerPx(): number {
    const [, lat] = this.toLonlat(this.width / 2, this.height / 2);
    return 40075016.686 * Math.cos(lat * Math.PI / 180) / this.world;
  }

  /** Zoom by `dz` keeping the point under (px, py) fixed. */
  zoomAround(px: number, py: number, dz: number, min = 1, max = 21) {
    const [mx, my] = this.toMerc(px, py);
    this.zoom = Math.max(min, Math.min(max, this.zoom + dz));
    const w = this.world;
    this.cx = mx - (px - this.width / 2) / w;
    this.cy = my - (py - this.height / 2) / w;
  }

  clone(): View { return new View(this.cx, this.cy, this.zoom, this.width, this.height); }
}

/** A round scale number, e.g. 1 : 50 000. */
export function scaleDenominator(view: View): number {
  return view.metersPerPx() / (0.0254 / 96);
}
