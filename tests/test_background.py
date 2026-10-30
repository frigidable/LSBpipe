"""Synthetic validation of residual-background fitting and spatial holdouts."""
import numpy as np
import pytest

from lsbpipe.scene import MaskLayer, LayerKind, SceneState
from lsbpipe.background import (
    BackgroundSettings, FitStatus, HoldoutStatus, fit_residual_background)


def make_scene(shape, train, hold, extra=()):
    grid = 'synthetic-grid-A'
    layers = [
        MaskLayer('training', LayerKind.SKY_TRAIN, train, grid, 'r'),
        MaskLayer('holdout', LayerKind.SKY_HOLDOUT, hold, grid, 'r')]
    for i, (kind, mask) in enumerate(extra):
        layers.append(MaskLayer(f'extra_{i}', kind, mask, grid, 'r'))
    return SceneState('galaxy', grid, 'r', shape, tuple(layers))


def regions(shape=(48, 52)):
    yy, xx = np.indices(shape)
    train = (yy % 5 != 0) & (xx % 6 != 0)
    hold = ~train
    target = (xx - 25)**2 + (yy - 23)**2 < 7**2
    train &= ~target
    hold &= ~target
    return train, hold


def fit(image, scene, method='constant', settings=None, ivar=None):
    return fit_residual_background(
        image, np.ones_like(image) if ivar is None else ivar,
        scene, method=method, grid_id='synthetic-grid-A', settings=settings)


def test_robust_constant_rejects_outliers_without_mutating_data():
    rng = np.random.default_rng(123)
    shape = (48, 52)
    train, hold = regions(shape)
    image = .008 + rng.normal(0., .001, shape)
    image[train & (np.indices(shape)[1] % 29 == 0)] += .5
    original = image.copy()
    result = fit(image, make_scene(shape, train, hold))
    assert result.fit_status == FitStatus.FITTED
    assert result.holdout_status == HoldoutStatus.MEASURED_UNCALIBRATED
    assert result.coefficients_nMgy[0] == pytest.approx(.008, abs=.001)
    assert result.coefficients_nMgy[1:] == (0., 0.)
    assert result.training_pixels > 100 and result.holdout_pixels > 100
    assert len(result.tiles) == 4
    assert np.array_equal(image, original)


def test_tilted_plane_restores_exact_gradient_and_outperforms_constant():
    shape = (55, 53)
    yy, xx = np.indices(shape)
    train, hold = regions(shape)
    image = .012 + .0008*(xx-26) - .0004*(yy-27)
    scene = make_scene(shape, train, hold)
    plane = fit(image, scene, 'tilted_plane')
    const = fit(image, scene, 'constant')
    assert plane.fit_status == FitStatus.FITTED
    assert plane.coefficients_nMgy == pytest.approx((.012, .0008, -.0004), abs=1e-10)
    assert plane.holdout_rms_residual_nMgy < 1e-10
    assert const.holdout_rms_residual_nMgy > .005
    assert np.max(np.abs(plane.image(shape) - image)) < 1e-10


def test_user_defined_holdout_threshold_flags_unfit_model():
    shape = (48, 52)
    yy, xx = np.indices(shape)
    train, hold = regions(shape)
    image = .02 + .002*(xx-25)
    scene = make_scene(shape, train, hold)
    settings = BackgroundSettings(max_abs_holdout_tile_median_nMgy=.003)
    assert fit(image, scene, 'constant', settings).holdout_status == (
        HoldoutStatus.REJECTED_BY_USER_THRESHOLD)
    assert fit(image, scene, 'tilted_plane', settings).holdout_status == (
        HoldoutStatus.WITHIN_USER_THRESHOLD)


def test_insufficient_and_collinear_sky_are_flagged_without_model():
    shape = (30, 30)
    train = np.zeros(shape, bool)
    train[3, 3] = True
    hold = np.zeros(shape, bool)
    hold[19:23, 2:27] = True
    result = fit(np.ones(shape), make_scene(shape, train, hold))
    assert result.fit_status == FitStatus.INSUFFICIENT_SKY
    with pytest.raises(ValueError, match='unsuccessful'):
        result.image(shape)
    train = np.zeros(shape, bool)
    train[3, 2:28] = True
    result = fit(np.ones(shape), make_scene(shape, train, hold), 'tilted_plane')
    assert result.fit_status == FitStatus.ILL_CONDITIONED
    assert result.design_condition > BackgroundSettings().max_design_condition


def test_target_protection_and_neighbor_are_excluded_from_sky_samples():
    shape = (48, 52)
    train, hold = regions(shape)
    target = np.zeros(shape, bool)
    target[:3, :] = True
    neighbor = np.zeros(shape, bool)
    neighbor[40:42, 10:13] = True
    scene = make_scene(shape, train, hold, [
        (LayerKind.TARGET_PROTECT, target),
        (LayerKind.NEIGHBOR, neighbor)])
    image = np.full(shape, .01)
    image[target | neighbor] = 500.
    result = fit(image, scene)
    assert result.coefficients_nMgy[0] == pytest.approx(.01, abs=1e-10)
    assert result.training_pixels < np.count_nonzero(train)
    with pytest.raises(ValueError, match='grid_id'):
        fit_residual_background(image, np.ones_like(image), scene,
                                method='constant', grid_id='wrong')


def test_single_quadrant_holdout_does_not_pass_spatial_qa():
    shape = (30, 30)
    train = np.zeros(shape, bool)
    train[:15, :] = True
    hold = np.zeros(shape, bool)
    hold[2:8, 2:9] = True
    train[hold] = False
    result = fit(np.ones(shape), make_scene(shape, train, hold))
    assert result.fit_status == FitStatus.FITTED
    assert result.holdout_status == HoldoutStatus.INSUFFICIENT_SPATIAL_COVERAGE


def test_no_valid_holdout_pixels_are_reported_not_adopted():
    shape = (48, 52)
    train, hold = regions(shape)
    ivar = np.ones(shape)
    ivar[hold] = 0.
    result = fit(np.ones(shape), make_scene(shape, train, hold), ivar=ivar)
    assert result.fit_status == FitStatus.FITTED
    assert result.holdout_pixels == 0
    assert result.holdout_status == HoldoutStatus.INSUFFICIENT_HOLDOUT
    assert result.holdout_median_residual_nMgy is None


def test_missing_explicit_sky_regions_fails_closed():
    scene = SceneState('galaxy', 'synthetic-grid-A', 'r', (10, 11))
    with pytest.raises(ValueError, match='Explicit sky_train'):
        fit(np.ones((10, 11)), scene)


def test_invalid_numerical_config_is_rejected():
    for options in ({'huber_t': 0}, {'max_design_condition': -1},
                    {'min_holdout_quadrants': 5},
                    {'max_abs_holdout_tile_median_nMgy': np.nan}):
        with pytest.raises(ValueError):
            BackgroundSettings(**options)
