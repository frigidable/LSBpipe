"""Fixed-mask differential photometry invariants for P0c."""
import numpy as np
import pytest

from lsbpipe.background import BackgroundCandidate, FitStatus, HoldoutStatus
from lsbpipe.geometry import ReferenceGeometry
from lsbpipe.profiles import MeasurementFrame
from lsbpipe.sensitivity import compare_backgrounds, compare_observation_backgrounds
from lsbpipe.scene import LayerKind, MaskLayer, scene_from_observation
from lsbpipe.survey import ObservationSet


def case(shape=(70, 74)):
    yy, xx = np.indices(shape)
    galaxy = 1.2*np.exp(-((xx-37)**2+(yy-35)**2)/70.)
    image = galaxy - .0004
    valid = np.ones(shape, bool)
    valid[:, :25] = False
    valid[35:45, 35:40] = False
    ivar = np.ones(shape)*100
    return MeasurementFrame(image, .262, valid=valid, inverse_variance=ivar), ReferenceGeometry(37.3, 35.2, .63, 23)


def check_identity(result, key, tol=2e-10):
    delta = result.deltas[key]
    np.testing.assert_allclose(delta.annular_nMgy, delta.predicted_annular_nMgy, atol=tol, rtol=1e-10, equal_nan=True)
    np.testing.assert_allclose(delta.cog_nMgy, delta.predicted_cog_nMgy, atol=tol, rtol=1e-10)


def test_fixed_mask_constant_background_response_and_no_mutation():
    frame, geometry = case()
    original = frame.image.copy()
    edges = [0., 5., 12., 20., 31.]
    out = compare_backgrounds(frame, geometry, edges, {'raw': 0., 'offset': .012}, reference='raw')
    check_identity(out, 'offset')
    delta = out.deltas['offset']
    np.testing.assert_allclose(delta.annular_nMgy,
                               -.012 * out.profiles['raw'].area_valid_pix2, atol=1e-10)
    np.testing.assert_allclose(delta.cog_nMgy,
                               -.012 * np.cumsum(out.profiles['raw'].area_valid_pix2), atol=1e-10)
    assert np.array_equal(frame.image, original)
    assert np.array_equal(out.profiles['raw'].area_valid_pix2, out.profiles['offset'].area_valid_pix2)
    with pytest.raises(ValueError):
        delta.cog_nMgy[0] = 100


def test_tilted_background_mask_asymmetry_and_finite_footprint():
    frame, geometry = case()
    yy, xx = np.indices(frame.image.shape)
    bg = .002 + .001 * (xx-37) - .0007 * (yy-35)
    out = compare_backgrounds(frame, geometry, [0, 10, 22, 39, 60],
                              {'raw': 0.0, 'tilt': bg}, reference='raw')
    check_identity(out, 'tilt')
    assert abs(out.deltas['tilt'].cog_nMgy[-1]) > .01
    assert np.all(out.profiles['raw'].area_valid_pix2 <= out.profiles['raw'].area_imaged_pix2 + 1e-10)


def test_nonmonotone_cog_and_negative_annular_flux_preserved():
    arr = np.full((50, 50), -.02)
    frame = MeasurementFrame(arr, .262)
    geometry = ReferenceGeometry(25, 25, 1.)
    out = compare_backgrounds(frame, geometry, [0, 5, 12, 20],
                              {'raw': 0.0, 'shift': .001}, reference='raw')
    assert np.all(out.profiles['shift'].annular_flux_valid_nMgy < 0)
    assert np.all(np.diff(out.profiles['shift'].cog_raw_nMgy) < 0)
    assert np.isnan(out.profiles['shift'].mu_ab_mag_arcsec2).all()
    check_identity(out, 'shift')


def test_fully_masked_ring_is_nan_but_cog_still_zero():
    frame = MeasurementFrame(np.ones((40, 40)), .262, valid=np.zeros((40, 40), bool))
    out = compare_backgrounds(frame, ReferenceGeometry(20, 20, 1.), [0, 5, 10],
                              {'zero': 0., 'one': 1.}, reference='zero')
    delta = out.deltas['one']
    assert np.isnan(delta.annular_nMgy).all()
    assert np.isnan(delta.predicted_annular_nMgy).all()
    assert np.array_equal(delta.cog_nMgy, [0., 0.])
    check_identity(out, 'one')


def test_frame_existing_background_is_replaced_not_compounded():
    frame, geo = case()
    frame = MeasurementFrame(frame.image, .262, valid=frame.valid,
                             inverse_variance=frame.inverse_variance, background=.6)
    out = compare_backgrounds(frame, geo, [0, 7, 12],
                              {'b0': 0., 'b1': .1}, reference='b0')
    check_identity(out, 'b1')
    assert out.profiles['b0'].cog_raw_nMgy[-1] > out.profiles['b1'].cog_raw_nMgy[-1]


def test_invalid_scenarios_and_candidate_failures():
    frame, geo = case()
    for backgrounds, ref in [({'a': 0.}, 'a'), ({'a': 0., 'b': 1.}, 'missing'),
                             ({'a': 0., 'b': np.full(frame.image.shape, np.nan)}, 'a'),
                             ({'a': 0., 'b': np.ones((2, 2))}, 'a')]:
        with pytest.raises(ValueError):
            compare_backgrounds(frame, geo, [0, 5], backgrounds, reference=ref)
    bad = BackgroundCandidate('constant', FitStatus.INSUFFICIENT_SKY,
                              HoldoutStatus.NOT_EVALUATED, (0., 0.), None,
                              0, 0, None, 0, None, None, None, None, ())
    with pytest.raises(ValueError, match='unsuccessful'):
        compare_backgrounds(frame, geo, [0, 5], {'a': 0., 'bad': bad}, reference='a')


def test_candidate_holdout_status_retained_even_when_rejected():
    frame, geo = case()
    rejected = BackgroundCandidate('constant', FitStatus.FITTED,
                                   HoldoutStatus.REJECTED_BY_USER_THRESHOLD,
                                   (37., 35.), (.005, 0., 0.),
                                   20, 20, 1., 5, 0., 0., 0., 0., ())
    out = compare_backgrounds(frame, geo, [0, 10, 20],
                              {'reference': 0., 'rejected': rejected}, reference='reference')
    assert out.holdout_statuses['rejected'] == 'REJECTED_BY_USER_THRESHOLD'
    check_identity(out, 'rejected')


def test_observation_adapter_uses_mask_engine_quality_and_checks_grid():
    class W:
        def __init__(self, offset=0.): self.offset = offset
        def to_header(self, relax=True): return {'CRPIX1': 10. + self.offset}
    image = np.ones((50, 50))
    ivar = np.ones((50, 50))
    valid = np.ones((50, 50), bool)
    valid[24, 24] = False
    obs = ObservationSet(image, ivar, None, W(), 'r', 'dr9', .262**2, valid, {})
    state = scene_from_observation(obs, 'galaxy')
    star = np.zeros(obs.shape, bool)
    star[25, 25] = True
    state = state.with_layer(MaskLayer('star', LayerKind.STAR_CORE, star, state.grid_id, band='r'))
    out = compare_observation_backgrounds(obs, state, ReferenceGeometry(25, 25, 1.),
                                          [0, 10], {'raw': 0., 'shift': 1.}, reference='raw')
    check_identity(out, 'shift')
    assert out.profiles['raw'].area_valid_pix2[0] < out.profiles['raw'].area_imaged_pix2[0]
    shifted = ObservationSet(image, ivar, None, W(1.), 'r', 'dr9', .262**2, valid, {})
    with pytest.raises(ValueError, match='grid_id'):
        compare_observation_backgrounds(shifted, state, ReferenceGeometry(25, 25, 1.),
                                        [0, 10], {'raw': 0., 'shift': 1.}, reference='raw')


def test_exact_overlap_when_photutils_available():
    pytest.importorskip('photutils')
    frame, geo = case((40, 42))
    out = compare_backgrounds(frame, ReferenceGeometry(20.1, 20.4, .7, 32),
                              [0., 8., 16.], {'raw': 0., 'other': .04},
                              reference='raw', backend='exact')
    check_identity(out, 'other')


def test_fitted_candidate_provenance_matches_observation_or_fails_closed():
    from lsbpipe.background import fit_observation_background

    class W:
        def __init__(self, offset=0.): self.offset = offset
        def to_header(self, relax=True): return {'CRPIX1': 10. + self.offset}

    shape = (40, 42)
    yy, xx = np.indices(shape)
    image = .003 + .0002*xx - .0004*yy
    ivar = np.ones(shape)
    obs = ObservationSet(image, ivar, None, W(), 'r', 'dr9', .262**2,
                         np.ones(shape, bool), {})
    state = scene_from_observation(obs, 'galaxy')
    train = (yy % 5 != 0) & (xx % 6 != 0)
    hold = ~train
    state = state.with_layer(MaskLayer('sky_fit', LayerKind.SKY_TRAIN,
                                      train, state.grid_id, 'r'))
    state = state.with_layer(MaskLayer('sky_holdout', LayerKind.SKY_HOLDOUT,
                                      hold, state.grid_id, 'r'))
    fitted = fit_observation_background(obs, state, method='tilted_plane')
    assert fitted.fit_status == FitStatus.FITTED
    assert fitted.grid_id == state.grid_id
    assert fitted.band == 'r' and fitted.image_shape == shape
    result = compare_observation_backgrounds(
        obs, state, ReferenceGeometry(21, 20, .7), [0, 8, 15],
        {'nominal': 0., 'plane': fitted}, reference='nominal')
    check_identity(result, 'plane')
    wrong = BackgroundCandidate('constant', FitStatus.FITTED,
                                HoldoutStatus.MEASURED_UNCALIBRATED,
                                (20., 19.), (.003, 0., 0.), 30, 30, 1., 5,
                                0., 0., 0., 0., (),
                                grid_id='wrong', band='r', image_shape=shape)
    with pytest.raises(ValueError, match='source grid'):
        compare_observation_backgrounds(
            obs, state, ReferenceGeometry(21, 20, .7), [0, 8, 15],
            {'nominal': 0., 'wrong': wrong}, reference='nominal')
    untagged = BackgroundCandidate('constant', FitStatus.FITTED,
                                   HoldoutStatus.MEASURED_UNCALIBRATED,
                                   (20., 19.), (.003, 0., 0.), 30, 30, 1., 5,
                                   0., 0., 0., 0., ())
    with pytest.raises(ValueError, match='source grid'):
        compare_observation_backgrounds(
            obs, state, ReferenceGeometry(21, 20, .7), [0, 8, 15],
            {'nominal': 0., 'untagged': untagged}, reference='nominal')
    with pytest.raises(ValueError, match='shape mismatch'):
        fitted.image((20, 21))
