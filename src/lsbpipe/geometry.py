"""Fixed-reference ellipse in raster pixel coordinates, without sky-WCS claims.

Pixel centres are (column, row): x increases rightward, y increases downwards
on the usual displayed array. theta_image_deg is measured from +column toward
+row. This is NOT astronomical position angle (north through east).
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, pi
import numpy as np


@dataclass(frozen=True)
class ReferenceGeometry:
    x_pix: float
    y_pix: float
    q: float
    theta_image_deg: float = 0.0

    def __post_init__(self) -> None:
        if not all(map(isfinite, (self.x_pix, self.y_pix, self.q, self.theta_image_deg))):
            raise ValueError("Geometry must have finite parameters")
        if not (0.0 < self.q <= 1.0):
            raise ValueError("Axial ratio must satisfy 0 < q <= 1")

    @property
    def theta_rad(self) -> float:
        return float(np.deg2rad(self.theta_image_deg))

    def elliptical_radius(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        dx = x - self.x_pix
        dy = y - self.y_pix
        ct, st = np.cos(self.theta_rad), np.sin(self.theta_rad)
        xx = ct * dx + st * dy
        yy = -st * dx + ct * dy
        return np.hypot(xx, yy / self.q)

    def area_pix2(self, a_pix: float) -> float:
        if a_pix < 0 or not isfinite(a_pix):
            raise ValueError("Semimajor axis must be non-negative and finite")
        return pi * self.q * a_pix * a_pix


def _sampled_weights(
    shape: tuple[int, int], geo: ReferenceGeometry, a_pix: float, subpixels: int
) -> np.ndarray:
    """Subpixel *approximation*; exact backend is provided separately.

    Conservative Lipschitz bounds identify pixels entirely in/out before
    supersampling the uncertain boundary. The same subgrid at every radius
    guarantees nested weights for increasing a_pix.
    """
    if not isinstance(subpixels, (int, np.integer)) or subpixels < 1:
        raise ValueError("subpixels must be positive integer")
    if a_pix == 0:
        return np.zeros(shape, dtype=np.float64)
    ny, nx = shape
    result = np.zeros(shape, dtype=np.float64)
    # Full ellipse lies within a circle of radius a_pix; include pixel extent.
    x0 = max(0, int(np.floor(geo.x_pix - a_pix - 1)))
    x1 = min(nx, int(np.ceil(geo.x_pix + a_pix + 1)))
    y0 = max(0, int(np.floor(geo.y_pix - a_pix - 1)))
    y1 = min(ny, int(np.ceil(geo.y_pix + a_pix + 1)))
    if x0 >= x1 or y0 >= y1:
        return result
    yy, xx = np.mgrid[y0:y1, x0:x1]
    rr = geo.elliptical_radius(xx, yy)
    bound = np.sqrt(0.5) / geo.q  # maximum change from centre to pixel corner
    inside = rr + bound < a_pix
    outside = rr - bound > a_pix
    roi = result[y0:y1, x0:x1]
    roi[inside] = 1.0
    edge = ~(inside | outside)
    if np.any(edge):
        by, bx = np.nonzero(edge)
        xs = (bx + x0).astype(float)
        ys = (by + y0).astype(float)
        frac = np.zeros(len(xs), dtype=np.float64)
        offsets = (np.arange(subpixels, dtype=float) + 0.5) / subpixels - 0.5
        for oy in offsets:
            for ox in offsets:
                frac += geo.elliptical_radius(xs + ox, ys + oy) < a_pix
        roi[edge] = frac / (subpixels * subpixels)
    return result


def fractional_aperture_weights(
    shape: tuple[int, int], geometry: ReferenceGeometry, a_pix: float,
    *, backend: str = "subpixel", subpixels: int = 8,
) -> np.ndarray:
    """Pixel area fractions in an ellipse; no data mask applied.

    backend='exact': Photutils exact pixel/ellipse overlap (optional extra).
    backend='subpixel': controlled subpixel approximation, NOT mathematically
    exact. Pixel centres are integer (column,row), not corners.
    """
    if len(shape) != 2 or any(int(s) != s or s <= 0 for s in shape):
        raise ValueError("shape must be two positive integers")
    if not isfinite(a_pix) or a_pix < 0:
        raise ValueError("a_pix must be non-negative and finite")
    if backend == "subpixel":
        return _sampled_weights(shape, geometry, a_pix, subpixels)
    if backend == "exact":
        if a_pix == 0:
            return np.zeros(shape, dtype=float)
        try:
            from photutils.aperture import EllipticalAperture
        except ImportError as exc:
            raise RuntimeError(
                "Exact overlap needs Photutils. Install: pip install -e '.[exact]'"
            ) from exc
        ap = EllipticalAperture(
            (geometry.x_pix, geometry.y_pix), a=a_pix,
            b=a_pix * geometry.q, theta=geometry.theta_rad,
        )
        patch = ap.to_mask(method="exact").to_image(shape)
        return np.zeros(shape, dtype=float) if patch is None else np.asarray(patch, dtype=float)
    raise ValueError(f"Unknown backend {backend!r}")
