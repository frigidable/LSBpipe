"""Scientific invariants for versioned scene masks (P0a)."""
import json
import numpy as np
import pytest
from lsbpipe.scene import (LayerKind, MaskLayer, Operation, SceneState,
                           evaluate_mask, grid_fingerprint, scene_from_observation)
from lsbpipe.survey import ObservationSet, legacy_validity


def layer(name, kind, coords, shape=(6, 7), grid='grid-1', band='r'):
    mask = np.zeros(shape, dtype=bool)
    for y, x in coords:
        mask[y, x] = True
    return MaskLayer(name, kind, mask, grid, band)


def scene(*layers):
    return SceneState('target', 'grid-1', 'r', (6, 7), layers)


def data():
    return np.ones((6, 7)), np.ones((6, 7))


def test_hard_masks_and_target_protection_precedence():
    img, iv = data()
    img[0, 0] = np.nan
    iv[0, 1] = 0
    s = scene(layer('target', LayerKind.TARGET_PROTECT, [(0, 0), (0, 1), (1, 1)]),
              layer('sat', LayerKind.SATURATION, [(1, 1)]),
              layer('star', LayerKind.STAR_CORE, [(2, 2)]))
    measurement = evaluate_mask(s, img, iv, grid_id='grid-1', operation='photometry')
    assert all(not measurement.valid[y, x] for y, x in ((0, 0), (0, 1), (1, 1), (2, 2)))
    assert measurement.valid[3, 3]
    assert not measurement.valid.flags.writeable
    with pytest.raises(ValueError): measurement.valid[3, 3] = False


def test_sky_fit_requires_explicit_region_excludes_target_and_neighbors():
    img, iv = data()
    s = scene(layer('training', LayerKind.SKY_TRAIN, [(1, 1), (1, 2), (2, 2)]),
              layer('holdout', LayerKind.SKY_HOLDOUT, [(4, 4)]),
              layer('target', LayerKind.TARGET_PROTECT, [(1, 1)]),
              layer('neighbor', LayerKind.NEIGHBOR, [(1, 2)]))
    train = evaluate_mask(s, img, iv, grid_id='grid-1', operation=Operation.SKY_FIT)
    hold = evaluate_mask(s, img, iv, grid_id='grid-1', operation=Operation.SKY_HOLDOUT)
    assert train.n_valid == 1 and train.valid[2, 2]
    assert hold.n_valid == 1 and hold.valid[4, 4]
    assert not np.any(train.valid & hold.valid)
    with pytest.raises(ValueError, match='Explicit sky_train'):
        evaluate_mask(scene(), img, iv, grid_id='grid-1', operation=Operation.SKY_FIT)
    with pytest.raises(ValueError, match='overlap'):
        scene(layer('a', LayerKind.SKY_TRAIN, [(1, 1)]),
              layer('b', LayerKind.SKY_HOLDOUT, [(1, 1)]))


def test_same_shape_wrong_grid_and_band_rejected():
    img, iv = data()
    with pytest.raises(ValueError, match='grid_id'):
        evaluate_mask(scene(), img, iv, grid_id='shifted', operation='photometry')
    with pytest.raises(ValueError, match='Grid/shape'):
        scene(layer('bad', LayerKind.STAR_WINGS, [(1, 1)], grid='shifted'))
    with pytest.raises(ValueError, match='Band'):
        scene(layer('bad', LayerKind.STAR_WINGS, [(1, 1)], band='g'))
    with pytest.raises(ValueError, match='shape'):
        scene(layer('bad', LayerKind.STAR_WINGS, [(1, 1)], shape=(7, 7)))


def test_no_mutation_revision_roundtrip_and_catalog_warnings():
    img, iv = data()
    raw = np.zeros((6, 7), dtype=bool)
    raw[2, 2] = True
    l = MaskLayer('warning', LayerKind.CATALOG_WARNING, raw, 'grid-1', 'r')
    raw[2, 2] = False
    assert l.data[2, 2]
    with pytest.raises(ValueError): l.data[2, 2] = False
    a = scene(l)
    b = a.with_layer(layer('manual', LayerKind.MANUAL_EXCLUDE, [(3, 3)]))
    assert a.revision == 0 and b.revision == 1 and len(a.layers) == 1
    assert evaluate_mask(a, img, iv, grid_id='grid-1', operation='photometry').valid[2, 2]
    assert not evaluate_mask(b, img, iv, grid_id='grid-1', operation='photometry').valid[3, 3]
    c = SceneState.from_dict(json.loads(json.dumps(b.to_dict())))
    assert c.to_dict() == b.to_dict()
    assert c.without_layer('manual').revision == 2
    with pytest.raises(ValueError, match='schema'):
        SceneState.from_dict({**b.to_dict(), 'schema_version': '999'})
    with pytest.raises(ValueError, match='exists'):
        b.with_layer(layer('manual', LayerKind.MANUAL_EXCLUDE, [(1, 1)]))


def test_observation_source_maskbits_are_semantic_and_conservative():
    class W:
        def __init__(self, delta=0): self.delta = delta
        def to_header(self, relax=True): return {'CRPIX1': 10 + self.delta, 'CTYPE1': 'RA---TAN'}
    image, iv = data()
    flags = np.zeros(image.shape, dtype=np.int16)
    flags[0, 0] |= 1 << 12    # GALAXY informative
    flags[0, 1] |= 1 << 1     # BRIGHT informative
    flags[1, 0] |= 1 << 3     # SATUR_R
    flags[1, 1] |= 1 << 6     # ALLMASK_R
    valid, reasons = legacy_validity(image, iv, flags, 'r', reject_satur=False, reject_allmask=False)
    o = ObservationSet(image, iv, flags, W(), 'r', 'dr9', 0.262**2, valid, reasons)
    s = scene_from_observation(o, 'NGC2146sat')
    decision = evaluate_mask(s, image, iv, grid_id=s.grid_id, operation='photometry')
    assert decision.valid[0, 0] and decision.valid[0, 1]
    assert not decision.valid[1, 0] and not decision.valid[1, 1]
    assert grid_fingerprint(W(), image.shape) != grid_fingerprint(W(0.2), image.shape)
    with pytest.raises(ValueError, match='grid_id'):
        evaluate_mask(s, image, iv, grid_id=grid_fingerprint(W(0.2), image.shape), operation='photometry')


def test_mask_exclusion_distinguishes_sky_from_photometry_and_preserves_signed_data():
    img, iv = data()
    img[4, 4] = -3
    s = scene(layer('train', LayerKind.SKY_TRAIN, [(0, 0), (0, 1)]),
              layer('target', LayerKind.TARGET_PROTECT, [(4, 4)]))
    assert evaluate_mask(s, img, iv, grid_id='grid-1', operation='photometry').valid[4, 4]
    assert img[4, 4] == -3
    assert not evaluate_mask(s, img, iv, grid_id='grid-1', operation='sky_fit').valid[4, 4]
    iv_copy, img_copy = iv.copy(), img.copy()
    evaluate_mask(s, img, iv, grid_id='grid-1', operation='geometry')
    assert np.array_equal(iv, iv_copy) and np.array_equal(img, img_copy)
