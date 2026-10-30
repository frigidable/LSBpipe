"""Signed annular photometry and raw curve-of-growth with explicit valid area."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import numpy as np
from .geometry import ReferenceGeometry, fractional_aperture_weights


@dataclass(frozen=True)
class MeasurementFrame:
    """Image in nanomaggies per pixel, already flux calibrated.

    `valid` is a *measurement* mask (True = usable); a target-protection
    semantic layer must never override hard invalid pixels. `background` and
    `neighbor` are separately supplied removable model arrays or scalars.
    No sky/WCS support in this prototype; pixel scale is isotropic and fixed.
    """
    image: np.ndarray
    pixel_scale_arcsec: float
    valid: Optional[np.ndarray] = None
    inverse_variance: Optional[np.ndarray] = None
    background: float | np.ndarray = 0.0
    neighbor: float | np.ndarray = 0.0

    def __post_init__(self):
        arr = np.asarray(self.image, dtype=np.float64)
        if arr.ndim != 2 or arr.size == 0:
            raise ValueError("image must be a nonempty 2D array")
        if not np.isfinite(self.pixel_scale_arcsec) or self.pixel_scale_arcsec <= 0:
            raise ValueError("pixel scale must be positive and finite")
        for name in ("valid", "inverse_variance"):
            value = getattr(self, name)
            if value is None:
                continue
            val = np.asarray(value, dtype=bool if name == "valid" else float)
            if val.shape != arr.shape:
                raise ValueError(f"{name} shape must match image")
            val = np.array(val, copy=True)
            val.setflags(write=False)
            object.__setattr__(self, name, val)
        for name in ("background", "neighbor"):
            val = np.asarray(getattr(self, name), dtype=float)
            if val.ndim > 0 and val.shape != arr.shape:
                raise ValueError(f"{name} must be scalar or image-sized")
            if not np.all(np.isfinite(val)):
                raise ValueError(f"{name} must be finite")
            val = val.copy()
            val.setflags(write=False)
            object.__setattr__(self, name, val)
        arr = arr.copy()
        arr.setflags(write=False)
        object.__setattr__(self, "image", arr)

    @property
    def usable(self) -> np.ndarray:
        out = np.isfinite(self.image)
        if self.valid is not None:
            out &= self.valid
        if self.inverse_variance is not None:
            out &= np.isfinite(self.inverse_variance) & (self.inverse_variance > 0)
        return out

    @property
    def corrected(self) -> np.ndarray:
        return self.image - self.background - self.neighbor


@dataclass(frozen=True)
class ProfileResult:
    edges_pix: np.ndarray
    annular_flux_valid_nMgy: np.ndarray
    cog_raw_nMgy: np.ndarray
    annular_sigma_diagonal_nMgy: np.ndarray
    cog_sigma_diagonal_nMgy: np.ndarray
    area_geometric_pix2: np.ndarray
    area_imaged_pix2: np.ndarray
    area_valid_pix2: np.ndarray
    intensity_nMgy_arcsec2: np.ndarray
    mu_ab_mag_arcsec2: np.ndarray
    flags: tuple[str, ...]
    backend: str
    subpixels: int

    @property
    def area_fraction(self) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            return self.area_valid_pix2 / self.area_geometric_pix2

    @property
    def usable_fraction_of_image_footprint(self) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            return self.area_valid_pix2 / self.area_imaged_pix2


def measure_profiles(
    frame: MeasurementFrame,
    geometry: ReferenceGeometry,
    edges_pix: np.ndarray,
    *, backend: str = "subpixel", subpixels: int = 8,
) -> ProfileResult:
    """Measure independent annuli and raw COG from identical aperture weights.

    Area of a truncated aperture outside the input image remains in the
    geometric denominator, thus loss of footprint isn't silently ignored.
    No inpainting, no extrapolation and no monotonicity imposed on COG.
    Diagonal-only error is NA if inverse variance is missing, and is *not*
    suitable as the complete covariance of resampled Legacy coadds.
    """
    edges = np.asarray(edges_pix, dtype=float)
    if edges.ndim != 1 or len(edges) < 2 or edges[0] != 0 or not np.all(np.isfinite(edges)) or not np.all(np.diff(edges) > 0):
        raise ValueError("edges must start at zero and strictly increase with finite radii")
    usable = frame.usable
    signal = np.where(usable, frame.corrected, 0.0)
    ivar = frame.inverse_variance
    variance = None
    if ivar is not None:
        variance = np.zeros_like(frame.image)
        variance[usable] = 1 / ivar[usable]
    pixarea = frame.pixel_scale_arcsec ** 2
    prev = fractional_aperture_weights(frame.image.shape, geometry, edges[0], backend=backend, subpixels=subpixels)
    fvals, fsum, es, ecs, ag, ai, av, inten, mus, flags = ([] for _ in range(10))
    for i, hi in enumerate(edges[1:]):
        curr = fractional_aperture_weights(frame.image.shape, geometry, hi, backend=backend, subpixels=subpixels)
        ann = curr - prev
        if np.min(ann) < -1e-10:
            raise AssertionError("Aperture geometry is not nested")
        ann = np.maximum(ann, 0.0)  # numerical roundoff only
        ann_valid = ann * usable
        curr_valid = curr * usable
        geometric = geometry.area_pix2(hi) - geometry.area_pix2(edges[i])
        imaged = float(np.sum(ann))
        valid_area = float(np.sum(ann_valid))
        f = float(np.sum(ann * signal)) if valid_area > 0 else np.nan
        cumulative = float(np.sum(curr * signal))
        fvals.append(f)
        fsum.append(cumulative)
        ag.append(geometric)
        ai.append(imaged)
        av.append(valid_area)
        intensity = f / (valid_area * pixarea) if valid_area > 0 else np.nan
        inten.append(intensity)
        mus.append(22.5 - 2.5 * np.log10(intensity) if intensity > 0 else np.nan)
        if variance is None:
            es.append(np.nan)
            ecs.append(np.nan)
        else:
            es.append(float(np.sqrt(np.sum(ann_valid ** 2 * variance))))
            ecs.append(float(np.sqrt(np.sum(curr_valid ** 2 * variance))))
        reasons = []
        if valid_area == 0:
            reasons.append("NO_VALID_AREA")
        if imaged < geometric * (1 - 0.01):
            reasons.append("FRAME_TRUNCATION")
        if valid_area < imaged * (1 - 0.01):
            reasons.append("PARTIAL_MASK")
        if valid_area > 0 and intensity <= 0:
            reasons.append("NONPOSITIVE_INTENSITY")
        if variance is None:
            reasons.append("NO_NOISE_MODEL")
        flags.append("|".join(reasons) if reasons else "OK")
        prev = curr
    if not np.allclose(np.cumsum(np.nan_to_num(fvals)), fsum, atol=1e-8, rtol=1e-10):
        raise AssertionError("Raw cumulative flux and disjoint annuli disagree")
    return ProfileResult(
        edges_pix=np.array(edges, copy=True),
        annular_flux_valid_nMgy=np.array(fvals),
        cog_raw_nMgy=np.array(fsum),
        annular_sigma_diagonal_nMgy=np.array(es),
        cog_sigma_diagonal_nMgy=np.array(ecs),
        area_geometric_pix2=np.array(ag),
        area_imaged_pix2=np.array(ai),
        area_valid_pix2=np.array(av),
        intensity_nMgy_arcsec2=np.array(inten),
        mu_ab_mag_arcsec2=np.array(mus),
        flags=tuple(flags), backend=backend, subpixels=subpixels,
    )


def half_flux_radius(result: ProfileResult, *, target_flux_nMgy: float, nonmonotone_tolerance_nMgy: float = 0.0) -> float:
    """Finite-aperture half-flux radius, only for a valid, monotonic COG.

    This is neither intrinsic Sérsic R_e nor a total/asymptotic R_e.
    Returns a semimajor-axis pixel coordinate. The caller explicitly provides
    target total flux and checks completeness/quality independently.
    """
    if not np.isfinite(target_flux_nMgy) or target_flux_nMgy <= 0:
        raise ValueError("target flux must be positive and finite")
    if nonmonotone_tolerance_nMgy < 0:
        raise ValueError("nonmonotone tolerance cannot be negative")
    vals = result.cog_raw_nMgy
    if not np.all(np.isfinite(vals)) or np.any(np.diff(vals) < -nonmonotone_tolerance_nMgy):
        raise ValueError("Raw COG nonmonotonic: cannot infer half-flux without a declared model")
    x = np.concatenate(([result.edges_pix[0]], result.edges_pix[1:]))
    y = np.concatenate(([0.0], vals))
    if 0.5 * target_flux_nMgy > y[-1]:
        raise ValueError("Target half-flux not contained in sampled aperture")
    return float(np.interp(0.5 * target_flux_nMgy, y, x))
