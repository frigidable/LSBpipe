"""Fixed-mask, fixed-aperture photometric sensitivity to residual sky choices.

Diagnostic comparisons only: no automatic sky adoption or systematic error bar.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import numpy as np

from .background import BackgroundCandidate
from .geometry import ReferenceGeometry, fractional_aperture_weights
from .profiles import MeasurementFrame, ProfileResult, measure_profiles


def _read_only(values):
    arr = np.asarray(values, dtype=np.float64)
    return np.frombuffer(arr.tobytes(), dtype=np.float64).reshape(arr.shape)


@dataclass(frozen=True)
class FluxDelta:
    """Alternative minus reference, observed and independently predicted [nMgy]."""
    annular_nMgy: np.ndarray
    cog_nMgy: np.ndarray
    predicted_annular_nMgy: np.ndarray
    predicted_cog_nMgy: np.ndarray

    def __post_init__(self):
        for name in ('annular_nMgy', 'cog_nMgy',
                     'predicted_annular_nMgy', 'predicted_cog_nMgy'):
            object.__setattr__(self, name, _read_only(getattr(self, name)))


@dataclass(frozen=True)
class SensitivityResult:
    """Science-unadopted comparisons; uncertainties are not confidence bounds."""
    reference: str
    profiles: Mapping[str, ProfileResult]
    deltas: Mapping[str, FluxDelta]
    holdout_statuses: Mapping[str, str | None]

    def __post_init__(self):
        object.__setattr__(self, 'profiles', MappingProxyType(dict(self.profiles)))
        object.__setattr__(self, 'deltas', MappingProxyType(dict(self.deltas)))
        object.__setattr__(self, 'holdout_statuses', MappingProxyType(dict(self.holdout_statuses)))


def _background_array(value, shape):
    if isinstance(value, BackgroundCandidate):
        arr = value.image(shape)  # rejects unsuccessful fits, not holdout-rejected fits
    else:
        arr = np.asarray(value, dtype=float)
    if arr.ndim == 0:
        arr = np.full(shape, float(arr))
    if arr.shape != shape or not np.all(np.isfinite(arr)):
        raise ValueError('Background scenario must be finite scalar or image-sized array')
    return np.asarray(arr, dtype=np.float64)


def compare_backgrounds(
    frame: MeasurementFrame,
    geometry: ReferenceGeometry,
    edges_pix,
    backgrounds: Mapping[str, float | np.ndarray | BackgroundCandidate],
    *,
    reference: str,
    backend: str = 'subpixel',
    subpixels: int = 8,
) -> SensitivityResult:
    """Remeasure identical image, mask, neighbor model and aperture weights.

    Each supplied background *replaces* frame.background; no corrections are
    added together. All candidates are diagnostic (including holdout-rejected
    fitted candidates) and are never automatically adopted. Pixel unit is
    nanomaggies/pixel. No mask filling or COG monotonicity enforcement.

    Difference identity at fixed aperture weights: ΔF = -sum(w_valid × ΔB).
    The independent prediction uses the same geometrical backend but does not
    subtract noisy measured galaxy fluxes. It exposes cancellation errors.
    """
    if len(backgrounds) < 2 or reference not in backgrounds:
        raise ValueError('Provide a reference and at least one alternative background')
    if any(not isinstance(name, str) or not name.strip() for name in backgrounds):
        raise ValueError('Background scenario names must be nonempty strings')
    shape = frame.image.shape
    models = {name: _background_array(value, shape) for name, value in backgrounds.items()}
    statuses = {name: value.holdout_status.value
                if isinstance(value, BackgroundCandidate) else None
                for name, value in backgrounds.items()}
    usable = frame.usable
    profiles = {}
    for name, background in models.items():
        scenario = MeasurementFrame(
            image=frame.image, pixel_scale_arcsec=frame.pixel_scale_arcsec,
            valid=frame.valid, inverse_variance=frame.inverse_variance,
            background=background, neighbor=frame.neighbor)
        profiles[name] = measure_profiles(
            scenario, geometry, edges_pix, backend=backend, subpixels=subpixels)
    baseline = profiles[reference]
    base_model = models[reference]
    deltas = {}
    edges = np.asarray(baseline.edges_pix, dtype=float)
    for name, candidate in models.items():
        if name == reference:
            continue
        diff = candidate - base_model
        previous = fractional_aperture_weights(shape, geometry, edges[0],
                                               backend=backend, subpixels=subpixels)
        prediction_ann, prediction_cog = [], []
        for i, high in enumerate(edges[1:]):
            current = fractional_aperture_weights(shape, geometry, high,
                                                 backend=backend, subpixels=subpixels)
            annular = np.maximum(current - previous, 0.0)  # numerical roundoff only
            annular_valid = annular * usable
            prediction_ann.append(float(-np.sum(annular_valid * diff))
                                  if baseline.area_valid_pix2[i] > 0 else np.nan)
            prediction_cog.append(float(-np.sum(current * usable * diff)))
            previous = current
        actual_ann = profiles[name].annular_flux_valid_nMgy - baseline.annular_flux_valid_nMgy
        actual_cog = profiles[name].cog_raw_nMgy - baseline.cog_raw_nMgy
        deltas[name] = FluxDelta(actual_ann, actual_cog,
                                 np.asarray(prediction_ann), np.asarray(prediction_cog))
    return SensitivityResult(reference, profiles, deltas, statuses)


def compare_observation_backgrounds(
    observation, scene, geometry: ReferenceGeometry, edges_pix, backgrounds,
    *, reference: str, backend: str = 'subpixel', subpixels: int = 8,
) -> SensitivityResult:
    """ObservationSet adapter; rejects a changed observation WCS/band/quality.

    Geometry is already defined in the observation's raster coordinates. The
    caller must obtain that geometry from the same observation WCS. Fitted
    candidate provenance is checked against observation grid, shape and band.
    """
    from .mask_engine import MaskEngine
    from .scene import Operation
    mask = MaskEngine(scene).evaluate(observation, Operation.PHOTOMETRY)
    for name, candidate in backgrounds.items():
        if isinstance(candidate, BackgroundCandidate) and (
            candidate.grid_id != scene.grid_id or
            candidate.band != observation.band or
            candidate.image_shape != observation.shape
        ):
            raise ValueError(f'Background candidate {name!r} source grid/band/shape mismatch')
    frame = MeasurementFrame(
        image=observation.image_nMgy,
        pixel_scale_arcsec=np.sqrt(observation.pixel_area_arcsec2),
        valid=mask.valid,
        inverse_variance=observation.inverse_variance_nMgy2)
    return compare_backgrounds(frame, geometry, edges_pix, backgrounds,
                               reference=reference, backend=backend, subpixels=subpixels)
