"""The map view (Web Mercator) and coordinate conversions."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

TILE = 256


@dataclass(frozen=True)
class View:
    """What is on screen. cx, cy: centre in Web-Mercator unit coordinates (0..1);
    zoom: the world is 256 * 2**zoom pixels wide; width/height: image size in pixels."""
    cx: float
    cy: float
    zoom: float
    width: int
    height: int

    @property
    def world(self) -> float:
        return TILE * 2.0 ** self.zoom

    def to_px(self, mx, my):
        w = self.world
        return (np.asarray(mx) - self.cx) * w + self.width / 2, (np.asarray(my) - self.cy) * w + self.height / 2

    def to_merc(self, px, py):
        w = self.world
        return (px - self.width / 2) / w + self.cx, (py - self.height / 2) / w + self.cy

    def to_lonlat(self, px, py):
        mx, my = self.to_merc(px, py)
        return merc_to_lonlat(mx, my)

    def bbox_lonlat(self):
        """(west, south, east, north)"""
        w, n = self.to_lonlat(0, 0)
        e, s = self.to_lonlat(self.width, self.height)
        return (float(w), float(s), float(e), float(n))

    def meters_per_px(self) -> float:
        _, lat = self.to_lonlat(self.width / 2, self.height / 2)
        return 40075016.686 * math.cos(math.radians(float(lat))) / self.world

    def moved(self, **kw) -> "View":
        d = dict(cx=self.cx, cy=self.cy, zoom=self.zoom, width=self.width, height=self.height)
        d.update(kw)
        return View(**d)


def lonlat_to_merc(lon, lat):
    lon = np.asarray(lon, dtype=float)
    lat = np.clip(np.asarray(lat, dtype=float), -85.05112878, 85.05112878)
    x = (lon + 180.0) / 360.0
    y = (1 - np.log(np.tan(np.radians(lat)) + 1 / np.cos(np.radians(lat))) / math.pi) / 2
    return x, y


def merc_to_lonlat(mx, my):
    lon = np.asarray(mx) * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * np.asarray(my)))))
    return lon, lat
