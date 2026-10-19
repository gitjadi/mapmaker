// Coordinate systems used for Swedish data: SWEREF 99 TM and its local zones, RT 90 2.5 gon V,
// Web Mercator, and lon/lat. The same Transverse Mercator formulas as the desktop app's geo.py.

const A = 6378137.0, F = 1 / 298.257222101;
const E2 = F * (2 - F), N = F / (2 - F);
const AROOF = A / (1 + N) * (1 + N ** 2 / 4 + N ** 4 / 64);

type TM = [lon0deg: number, k0: number, fn: number, fe: number];
const ZONES: Record<number, TM> = {
  3006: [15.0, 0.9996, 0, 500000],
  3007: [12.0, 1, 0, 150000], 3008: [13.5, 1, 0, 150000], 3009: [15.0, 1, 0, 150000], 3010: [16.5, 1, 0, 150000],
  3011: [18.0, 1, 0, 150000], 3012: [14.25, 1, 0, 150000], 3013: [15.75, 1, 0, 150000], 3014: [17.25, 1, 0, 150000],
  3015: [18.75, 1, 0, 150000], 3016: [20.25, 1, 0, 150000], 3017: [21.75, 1, 0, 150000], 3018: [23.25, 1, 0, 150000],
  // RT 90 2.5 gon V, Lantmäteriet's direct GRS 80 approximation (better than a metre in Sweden)
  3021: [15 + 48 / 60 + 22.624306 / 3600, 1.00000561024, -667.711, 1500064.274],
};

export const GEOGRAPHIC = [4326, 4258, 4619, 4124, 4979, 4937];

function tmInverse(E: number, Nn: number, [lon0d, k0, fn, fe]: TM): [number, number] {
  const n = N, e2 = E2;
  const d1 = n / 2 - 2 * n ** 2 / 3 + 37 * n ** 3 / 96 - n ** 4 / 360;
  const d2 = n ** 2 / 48 + n ** 3 / 15 - 437 * n ** 4 / 1440;
  const d3 = 17 * n ** 3 / 480 - 37 * n ** 4 / 840;
  const d4 = 4397 * n ** 4 / 161280;
  const As = e2 + e2 ** 2 + e2 ** 3 + e2 ** 4;
  const Bs = -(7 * e2 ** 2 + 17 * e2 ** 3 + 30 * e2 ** 4) / 6;
  const Cs = (224 * e2 ** 3 + 889 * e2 ** 4) / 120;
  const Ds = -(4279 * e2 ** 4) / 1260;
  const xi = (Nn - fn) / (k0 * AROOF), eta = (E - fe) / (k0 * AROOF);
  const xp = xi - d1 * Math.sin(2 * xi) * Math.cosh(2 * eta) - d2 * Math.sin(4 * xi) * Math.cosh(4 * eta)
    - d3 * Math.sin(6 * xi) * Math.cosh(6 * eta) - d4 * Math.sin(8 * xi) * Math.cosh(8 * eta);
  const ep = eta - d1 * Math.cos(2 * xi) * Math.sinh(2 * eta) - d2 * Math.cos(4 * xi) * Math.sinh(4 * eta)
    - d3 * Math.cos(6 * xi) * Math.sinh(6 * eta) - d4 * Math.cos(8 * xi) * Math.sinh(8 * eta);
  const phis = Math.asin(Math.sin(xp) / Math.cosh(ep));
  const dl = Math.atan2(Math.sinh(ep), Math.cos(xp));
  const s = Math.sin(phis);
  const lat = phis + s * Math.cos(phis) * (As + Bs * s ** 2 + Cs * s ** 4 + Ds * s ** 6);
  return [lon0d + dl * 180 / Math.PI, lat * 180 / Math.PI];
}

/** A function from the coordinate system's (x, y) to (lon, lat), or an error for systems it doesn't know. */
export function toWgs84(epsg: number | null | undefined): (x: number, y: number) => [number, number] {
  if (epsg == null || GEOGRAPHIC.includes(epsg)) return (x, y) => [x, y];
  if (ZONES[epsg]) { const p = ZONES[epsg]; return (x, y) => tmInverse(x, y, p); }
  if (epsg === 3857 || epsg === 900913) {
    const R = 6378137;
    return (x, y) => [x / R * 180 / Math.PI, (2 * Math.atan(Math.exp(y / R)) - Math.PI / 2) * 180 / Math.PI];
  }
  throw new Error(`The coordinate system EPSG:${epsg} isn’t built in. Save the data in SWEREF 99 TM (EPSG:3006) or WGS 84 and try again.`);
}

/** EPSG code for metre coordinates in Sweden from typical values: SWEREF 99 TM or RT 90. */
export function guessProjected(x: number, y: number): number {
  if (y > 6.0e6 && y < 7.8e6 && x > 2.0e5 && x < 1.0e6) return 3006;
  if (y > 6.0e6 && y < 7.8e6 && x > 1.1e6 && x < 1.95e6) return 3021;
  if (x > 6.0e6 && x < 7.8e6 && y > 2.0e5 && y < 1.0e6) throw new Error('The north and east coordinates seem to be swapped.');
  throw new Error('Couldn’t tell which coordinate system the coordinates are in (expected WGS 84, SWEREF 99 TM or RT 90).');
}

/** EPSG code from a shapefile's .prj text. */
export function prjEpsg(prj: string | null): number {
  if (!prj) return 3006;
  const t = prj.toUpperCase().replace(/ /g, '_');
  if (t.includes('SWEREF') && t.includes('TM')) return 3006;
  if (t.includes('RT90') || t.includes('RT_90')) return 3021;
  if (t.includes('PSEUDO') && t.includes('MERCATOR') || t.includes('WEB_MERCATOR')) return 3857;
  if (t.startsWith('GEOGCS') && (t.includes('WGS') || t.includes('ETRS') || t.includes('SWEREF'))) return 4326;
  const m = /AUTHORITY\["EPSG","(\d+)"\]\]$/.exec(prj.trim());
  if (m) return Number(m[1]);
  throw new Error('The shapefile’s coordinate system (.prj) isn’t SWEREF 99 TM, RT 90 or WGS 84.');
}

export function median(a: number[]) {
  const v = a.filter(Number.isFinite).sort((x, y) => x - y);
  return v.length ? v[v.length >> 1] : NaN;
}
