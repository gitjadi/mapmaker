"""Elevation layers computed from a DEM mosaic (metres, NaN = no data):
hillshade, elevation colours and a sea-level (shoreline) simulation.

The same arrays are what predictive models will sample (elevation, slope,
aspect per find), so these functions are deliberately plain numpy."""
from __future__ import annotations

import numpy as np


def slope_aspect(elev: np.ndarray, m_per_px: float):
    """Slope (degrees) and aspect (degrees clockwise from north)."""
    e = np.where(np.isnan(elev), np.nanmean(elev) if np.isfinite(elev).any() else 0, elev)
    gy, gx = np.gradient(e, m_per_px)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    aspect = (np.degrees(np.arctan2(-gx, gy)) + 360) % 360
    return slope, aspect


def hillshade(elev: np.ndarray, m_per_px: float, azimuth=315.0, altitude=45.0, z=1.0) -> np.ndarray:
    """Classic hillshade, 0..1 (1 = fully lit)."""
    e = np.where(np.isnan(elev), 0, elev) * z
    gy, gx = np.gradient(e, m_per_px)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az = np.radians(360.0 - azimuth + 90.0)
    alt = np.radians(altitude)
    hs = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    hs = np.clip(hs, 0, 1)
    hs[np.isnan(elev)] = np.sin(alt)
    return hs


def hillshade_rgba(elev, m_per_px, strength=0.6, exaggeration=2.0) -> np.ndarray:
    """Grey image meant to be drawn with *multiply* blending: flat ground stays
    white (no change), slopes facing away from the light get darker."""
    hs = hillshade(elev, m_per_px, z=exaggeration)
    flat = np.sin(np.radians(45.0))
    rel = np.clip(hs / flat, 0, 1.0)
    v = 1.0 - strength * (1.0 - rel)
    g = (v * 255).astype(np.uint8)
    out = np.empty(elev.shape + (4,), dtype=np.uint8)
    out[..., 0] = g
    out[..., 1] = g
    out[..., 2] = g
    out[..., 3] = 255
    return out


# soft hypsometric tints in the Imhof tradition: blue sea, green lowland, yellowish hills, brown mountains
RELIEF_STOPS = [(-500, (150, 190, 220)), (-1, (186, 212, 231)), (0, (172, 208, 165)), (50, (148, 191, 139)),
                (150, (168, 198, 143)), (300, (189, 204, 150)), (500, (209, 215, 171)), (750, (225, 228, 181)),
                (1000, (239, 235, 192)), (1300, (222, 214, 163)), (1600, (202, 185, 130)), (2000, (195, 167, 107)),
                (2600, (220, 220, 220)), (3500, (255, 255, 255))]


def relief_rgba(elev, opacity=0.45, stops=RELIEF_STOPS) -> np.ndarray:
    xs = np.array([s[0] for s in stops], dtype=float)
    cs = np.array([s[1] for s in stops], dtype=float)
    out = np.zeros(elev.shape + (4,), dtype=np.uint8)
    e = np.nan_to_num(elev, nan=-9999)
    for i in range(3):
        out[..., i] = np.interp(e, xs, cs[:, i]).astype(np.uint8)
    out[..., 3] = np.where(np.isnan(elev), 0, int(opacity * 255))
    return out


def sealevel_rgba(elev, level: float, opacity=0.6, color=(40, 110, 190)) -> np.ndarray:
    """Everything below `level` metres becomes water."""
    out = np.zeros(elev.shape + (4,), dtype=np.uint8)
    under = np.nan_to_num(elev, nan=9999) < level
    out[under, 0], out[under, 1], out[under, 2] = color
    out[under, 3] = int(opacity * 255)
    return out


# ---------------------------------------------------------------------- laser terrain (1 m)
LIDAR_STYLES = {
    "hillshade": "Hillshade (light from the north-west)",
    "multi": "Hillshade from several directions",
    "slope": "Slope (steeper = darker)",
    "lrm": "Local relief (mounds light, pits dark)",
}
# styles a terrain layer can have: the laser styles plus colours by height (hypsometric tints)
TERRAIN_STYLES = dict(LIDAR_STYLES, tint="Colours by height (green lowland to brown upland)")


def lidar_margin(style: str, m_per_px: float) -> int:
    """Pixels of neighbouring terrain a style needs around a tile."""
    if style == "lrm":
        return int(min(250, 3 * _lrm_sigma(m_per_px) + 4))
    return 3


def _lrm_sigma(m_per_px: float) -> float:
    return max(2.0, 12.0 / max(m_per_px, 1e-6))      # a 12 m smoothing radius, at least 2 pixels


def _exaggeration(m_per_px: float) -> float:
    """More vertical exaggeration when zoomed out, so small relief stays visible."""
    return float(np.clip(1.0 + 0.5 * np.log2(max(m_per_px, 1.0)), 1.0, 3.0))


def lidar_rgba(elev: np.ndarray, m_per_px: float, style: str = "hillshade", strength: float = 0.8,
               background: bool = False) -> np.ndarray:
    """Grey terrain from a fine elevation model, in the style of archaeological laser maps.

    background=True: an opaque grey picture to use instead of a background map.
    background=False: an overlay – hillshade and slope are meant to be drawn with *multiply*
    blending (flat ground white = no change); local relief is drawn normally, semi-transparent."""
    out = np.zeros(elev.shape + (4,), dtype=np.uint8)
    ok = np.isfinite(elev)
    if not ok.any():
        return out
    e = np.where(ok, elev, np.nanmean(elev))
    z = _exaggeration(m_per_px) * (0.6 + 0.8 * strength if background else 1.0)
    if style in ("hillshade", "multi"):
        alt = 40.0
        if style == "hillshade":
            hs = hillshade(e, m_per_px, 315.0, alt, z)
        else:
            hs = (0.2 * hillshade(e, m_per_px, 270.0, alt, z) + 0.35 * hillshade(e, m_per_px, 315.0, alt, z)
                  + 0.25 * hillshade(e, m_per_px, 360.0, alt, z) + 0.2 * hillshade(e, m_per_px, 225.0, alt, z))
        v = hs / np.sin(np.radians(alt))              # 1 = flat ground
        if background:
            g = np.clip(205.0 * v, 0, 255)
        else:
            g = 255.0 - strength * 255.0 * (1.0 - np.clip(v, 0, 1))
        alpha = 255
    elif style == "slope":
        gy, gx = np.gradient(e * z, m_per_px)
        s = np.degrees(np.arctan(np.hypot(gx, gy)))
        dark = np.clip(s / 32.0, 0, 1) ** 0.8
        g = 255.0 - (215.0 if background else 255.0 * strength) * dark
        alpha = 255
    elif style == "lrm":
        from scipy.ndimage import gaussian_filter
        lrm = e - gaussian_filter(e, _lrm_sigma(m_per_px))
        r = 0.45 + 0.12 * max(m_per_px, 0.5)          # metres of relief from mid grey to white / black
        t = np.clip(lrm / r, -1, 1)
        if background:
            hs = hillshade(e, m_per_px, 315.0, 40.0, z) / np.sin(np.radians(40.0))
            g = 0.75 * (155.0 + 100.0 * t) + 0.25 * np.clip(205.0 * hs, 0, 255)
            alpha = 255
        else:
            g = 128.0 + 127.0 * t
            alpha = (np.abs(t) ** 0.7 * 255 * strength).astype(np.uint8)
    else:
        raise ValueError(style)
    gg = np.clip(g, 0, 255).astype(np.uint8)
    out[..., 0] = gg
    out[..., 1] = gg
    out[..., 2] = gg
    out[..., 3] = alpha
    out[~ok, 3] = 0
    return out
