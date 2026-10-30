"""Robust *residual* background estimators for already sky-subtracted coadds.

Fit constant or tilted-plane candidates to explicitly labelled sky-training
pixels; evaluate separate holdouts. No automatic adoption, masked-pixel
reconstruction, uncertainty calibration, or mutation of source images.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal
import numpy as np

from .scene import Operation, SceneState, evaluate_mask


class FitStatus(str, Enum):
    FITTED = 'FITTED'
    INSUFFICIENT_SKY = 'INSUFFICIENT_SKY'
    ILL_CONDITIONED = 'ILL_CONDITIONED'
    NUMERICAL_FAILURE = 'NUMERICAL_FAILURE'


class HoldoutStatus(str, Enum):
    NOT_EVALUATED = 'NOT_EVALUATED'
    INSUFFICIENT_HOLDOUT = 'INSUFFICIENT_HOLDOUT'
    INSUFFICIENT_SPATIAL_COVERAGE = 'INSUFFICIENT_SPATIAL_COVERAGE'
    MEASURED_UNCALIBRATED = 'MEASURED_UNCALIBRATED'
    REJECTED_BY_USER_THRESHOLD = 'REJECTED_BY_USER_THRESHOLD'
    WITHIN_USER_THRESHOLD = 'WITHIN_USER_THRESHOLD'


@dataclass(frozen=True)
class BackgroundSettings:
    min_training_pixels: int = 12
    min_holdout_pixels: int = 12
    min_holdout_quadrants: int = 2
    max_design_condition: float = 1e8
    huber_t: float = 1.345
    max_iterations: int = 50
    convergence_relative: float = 1e-10
    max_abs_holdout_tile_median_nMgy: float | None = None

    def __post_init__(self):
        if self.min_training_pixels < 3 or self.min_holdout_pixels < 1:
            raise ValueError('Pixel counts must be positive (training at least 3)')
        if not 1 <= self.min_holdout_quadrants <= 4:
            raise ValueError('min_holdout_quadrants must be between 1 and 4')
        for value in (self.max_design_condition, self.huber_t, self.convergence_relative):
            if not np.isfinite(value) or value <= 0:
                raise ValueError('Numerical settings must be finite and positive')
        if self.max_iterations < 1:
            raise ValueError('max_iterations must be >= 1')
        limit = self.max_abs_holdout_tile_median_nMgy
        if limit is not None and (not np.isfinite(limit) or limit < 0):
            raise ValueError('User-supplied holdout threshold must be non-negative finite')


@dataclass(frozen=True)
class HoldoutTile:
    quadrant: int
    pixels: int
    median_residual_nMgy: float
    mean_residual_nMgy: float
    rms_residual_nMgy: float


@dataclass(frozen=True)
class BackgroundCandidate:
    method: Literal['constant', 'tilted_plane']
    fit_status: FitStatus
    holdout_status: HoldoutStatus
    reference_xy_pix: tuple[float, float]
    coefficients_nMgy: tuple[float, float, float] | None
    training_pixels: int
    holdout_pixels: int
    design_condition: float | None
    iterations: int
    training_median_residual_nMgy: float | None
    training_rms_residual_nMgy: float | None
    holdout_median_residual_nMgy: float | None
    holdout_rms_residual_nMgy: float | None
    tiles: tuple[HoldoutTile, ...]
    warnings: tuple[str, ...] = ()

    def evaluate_at(self, x_pix, y_pix):
        """Evaluate a residual-sky candidate in nanomaggies per pixel."""
        if self.coefficients_nMgy is None or self.fit_status != FitStatus.FITTED:
            raise ValueError('Cannot evaluate an unsuccessful background candidate')
        b0, bx, by = self.coefficients_nMgy
        x, y = np.asarray(x_pix, dtype=float), np.asarray(y_pix, dtype=float)
        return b0 + bx * (x - self.reference_xy_pix[0]) + by * (y - self.reference_xy_pix[1])

    def image(self, shape: tuple[int, int]) -> np.ndarray:
        """Materialize a fresh model map without modifying the input."""
        yy, xx = np.indices(shape, dtype=float)
        return np.asarray(self.evaluate_at(xx, yy), dtype=float)


def _weighted_median(values, weights):
    order = np.argsort(values)
    v, w = values[order], weights[order]
    return float(v[np.searchsorted(np.cumsum(w), 0.5 * np.sum(w), side='left')])


def _robust_scale(residuals):
    mad = 1.4826 * np.median(np.abs(residuals - np.median(residuals)))
    # Scale must be data-derived. A formal pixel error can exceed scatter by
    # orders of magnitude and thereby defeat Huber downweighting.
    floor = 64. * np.finfo(float).eps * max(1., float(np.max(np.abs(residuals))))
    return max(float(mad), floor)


def _weighted_lstsq(design, values, weights, max_condition):
    sqrtw = np.sqrt(weights / np.median(weights))
    matrix = design * sqrtw[:, None]
    rhs = values * sqrtw
    singular = np.linalg.svd(matrix, compute_uv=False)
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else np.inf
    if not np.isfinite(condition) or condition > max_condition:
        return None, condition
    coefficients, _, rank, _ = np.linalg.lstsq(matrix, rhs, rcond=None)
    if rank != design.shape[1] or not np.all(np.isfinite(coefficients)):
        return None, condition
    return coefficients, condition


def _fit_robust(design, values, ivar, method, settings):
    if method == 'constant':
        coefficients = np.array([_weighted_median(values, ivar)])
        condition = 1.0
    else:
        coefficients, condition = _weighted_lstsq(
            design, values, ivar, settings.max_design_condition)
        if coefficients is None:
            return None, condition, 0
    for iteration in range(1, settings.max_iterations + 1):
        residuals = values - design @ coefficients
        centered = residuals / _robust_scale(residuals)
        huber_weights = np.ones_like(centered)
        outliers = np.abs(centered) > settings.huber_t
        huber_weights[outliers] = settings.huber_t / np.abs(centered[outliers])
        updated, condition = _weighted_lstsq(
            design, values, ivar * huber_weights, settings.max_design_condition)
        if updated is None:
            return None, condition, iteration
        converged = (np.max(np.abs(updated - coefficients)) <=
                     settings.convergence_relative *
                     max(1., float(np.max(np.abs(coefficients)))))
        coefficients = updated
        if converged:
            break
    return coefficients, condition, iteration


def _rms(values):
    return float(np.sqrt(np.mean(values**2)))


def fit_residual_background(
    image_nMgy: np.ndarray,
    inverse_variance_nMgy2: np.ndarray,
    scene: SceneState,
    *,
    method: Literal['constant', 'tilted_plane'],
    grid_id: str,
    observation_valid: np.ndarray | None = None,
    settings: BackgroundSettings | None = None,
) -> BackgroundCandidate:
    """Estimate one sky candidate, retaining fitting and QA status separately.

    Fitting samples are selected through the SKY_FIT operation; holdout samples
    through SKY_HOLDOUT. Their spatial separation beyond disjoint membership
    is the researcher's responsibility, and covariance is not modelled.
    """
    if method not in ('constant', 'tilted_plane'):
        raise ValueError('Only constant and tilted_plane candidates are implemented')
    settings = settings or BackgroundSettings()
    image = np.asarray(image_nMgy, dtype=float)
    ivar = np.asarray(inverse_variance_nMgy2, dtype=float)
    train = evaluate_mask(scene, image, ivar, grid_id=grid_id,
                          operation=Operation.SKY_FIT,
                          observation_valid=observation_valid)
    held = evaluate_mask(scene, image, ivar, grid_id=grid_id,
                         operation=Operation.SKY_HOLDOUT,
                         observation_valid=observation_valid)
    ntrain, nheld = train.n_valid, held.n_valid
    ny, nx = scene.shape
    xref, yref = (nx-1)/2., (ny-1)/2.
    ref = (xref, yref)

    def failure(status, cond=None, iterations=0):
        return BackgroundCandidate(method, status, HoldoutStatus.NOT_EVALUATED,
                                   ref, None, ntrain, nheld, cond, iterations,
                                   None, None, None, None, (), (status.value,))

    if ntrain < settings.min_training_pixels:
        return failure(FitStatus.INSUFFICIENT_SKY)
    yy, xx = np.nonzero(train.valid)
    values, weights = image[yy, xx], ivar[yy, xx]
    xs, ys = max(1., xref), max(1., yref)
    if method == 'constant':
        design = np.ones((ntrain, 1))
    else:
        design = np.column_stack(
            (np.ones(ntrain), (xx-xref)/xs, (yy-yref)/ys))
    try:
        fit, cond, iterations = _fit_robust(
            design, values, weights, method, settings)
    except (ValueError, np.linalg.LinAlgError, FloatingPointError, OverflowError):
        return failure(FitStatus.NUMERICAL_FAILURE)
    if fit is None:
        return failure(FitStatus.ILL_CONDITIONED, cond, iterations)

    coeff = ((float(fit[0]), 0., 0.) if method == 'constant'
             else (float(fit[0]), float(fit[1])/xs, float(fit[2])/ys))
    provisional = BackgroundCandidate(
        method, FitStatus.FITTED, HoldoutStatus.NOT_EVALUATED, ref, coeff,
        ntrain, nheld, cond, iterations, None, None, None, None, ())
    residual_train = values - provisional.evaluate_at(xx, yy)
    hy, hx = np.nonzero(held.valid)
    residual_held = image[hy, hx] - provisional.evaluate_at(hx, hy)
    tiles = []
    for quadrant in range(4):
        selection = ((hx > xref).astype(int) +
                     2 * (hy > yref).astype(int)) == quadrant
        r = residual_held[selection]
        if r.size:
            tiles.append(HoldoutTile(
                quadrant, int(r.size), float(np.median(r)),
                float(np.mean(r)), _rms(r)))
    warnings = []
    if nheld < settings.min_holdout_pixels:
        qa = HoldoutStatus.INSUFFICIENT_HOLDOUT
        warnings.append('HOLDOUT_PIXELS_BELOW_MINIMUM')
    elif len(tiles) < settings.min_holdout_quadrants:
        qa = HoldoutStatus.INSUFFICIENT_SPATIAL_COVERAGE
        warnings.append('HOLDOUT_SPATIAL_COVERAGE_LIMITED')
    elif settings.max_abs_holdout_tile_median_nMgy is None:
        qa = HoldoutStatus.MEASURED_UNCALIBRATED
        warnings.append('NO_CALIBRATED_HOLDOUT_THRESHOLD')
    elif max(abs(tile.median_residual_nMgy) for tile in tiles) > settings.max_abs_holdout_tile_median_nMgy:
        qa = HoldoutStatus.REJECTED_BY_USER_THRESHOLD
        warnings.append('HOLDOUT_RESIDUAL_EXCEEDS_USER_LIMIT')
    else:
        qa = HoldoutStatus.WITHIN_USER_THRESHOLD

    return BackgroundCandidate(
        method, FitStatus.FITTED, qa, ref, coeff, ntrain, nheld,
        cond, iterations, float(np.median(residual_train)),
        _rms(residual_train),
        float(np.median(residual_held)) if nheld else None,
        _rms(residual_held) if nheld else None,
        tuple(tiles), tuple(warnings))


def fit_observation_background(observation, scene: SceneState, *, method,
                               settings: BackgroundSettings | None = None) -> BackgroundCandidate:
    """Fit from v0.2 ObservationSet, preserving its existing validity map."""
    from .mask_engine import MaskEngine
    MaskEngine(scene).evaluate(observation, Operation.SKY_FIT)
    return fit_residual_background(
        observation.image_nMgy, observation.inverse_variance_nMgy2,
        scene, method=method, grid_id=scene.grid_id,
        observation_valid=observation.valid, settings=settings)
