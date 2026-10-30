"""Analytic Sérsic truth set and deterministic pixel-integrated synthetic images."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from scipy.special import gammaincinv, gammainc, gamma
from .geometry import ReferenceGeometry


@dataclass(frozen=True)
class SersicTruth:
    geometry: ReferenceGeometry
    re_pix: float
    n: float
    ie_nMgy_per_pix2: float

    def __post_init__(self):
        if not np.all(np.isfinite([self.re_pix, self.n, self.ie_nMgy_per_pix2])) or min(self.re_pix, self.n, self.ie_nMgy_per_pix2) <= 0:
            raise ValueError("re, n and Ie must be finite and positive")

    @property
    def bn(self) -> float:
        return float(gammaincinv(2.0 * self.n, 0.5))

    @property
    def total_nMgy(self) -> float:
        n, b, re, ie = self.n, self.bn, self.re_pix, self.ie_nMgy_per_pix2
        return float(2 * np.pi * self.geometry.q * ie * re ** 2 * n * np.exp(b) * gamma(2*n) / b ** (2*n))


def analytic_sersic_flux(truth: SersicTruth, a_pix: float) -> float:
    if not np.isfinite(a_pix) or a_pix < 0:
        raise ValueError("a_pix must be finite and nonnegative")
    b = truth.bn
    return float(truth.total_nMgy * gammainc(2*truth.n, b*(a_pix / truth.re_pix)**(1/truth.n)))


def sersic_image(shape: tuple[int, int], truth: SersicTruth, *, subpixels: int = 4) -> np.ndarray:
    """Pixel-integrated mock flux (nMgy/pixel); no PSF or noise.

    The integration is a midpoint quadrature, accurate for resolved mock
    sources but not intended for unresolved/central-cusp benchmarking.
    """
    if subpixels < 1 or int(subpixels) != subpixels:
        raise ValueError("subpixels must be positive integer")
    yy, xx = np.indices(shape)
    pixels = np.zeros(shape, dtype=float)
    offsets = (np.arange(subpixels, dtype=float) + .5) / subpixels - .5
    for dy in offsets:
        for dx in offsets:
            r = truth.geometry.elliptical_radius(xx + dx, yy + dy)
            pixels += truth.ie_nMgy_per_pix2 * np.exp(-truth.bn * ((r / truth.re_pix)**(1/truth.n) - 1))
    return pixels / (subpixels * subpixels)
