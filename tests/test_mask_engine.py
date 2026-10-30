import numpy as np
import pytest
from lsbpipe.mask_engine import MaskEngine
from lsbpipe.scene import Operation, scene_from_observation
from lsbpipe.survey import ObservationSet


def test_engine_revalidates_actual_observation_wcs_and_quality():
    class W:
        def __init__(self, dx=0): self.dx = dx
        def to_header(self, relax=True): return {'CRPIX1': 10 + self.dx}
    image, iv = np.ones((6, 7)), np.ones((6, 7))
    valid = np.ones(image.shape, dtype=bool)
    valid[3, 3] = False
    original = ObservationSet(image, iv, None, W(), 'r', 'dr9', 0.262**2, valid, {})
    state = scene_from_observation(original, 'target')
    engine = MaskEngine(state)
    output = engine.evaluate(original, Operation.PHOTOMETRY)
    assert not output.valid[3, 3]
    shifted = ObservationSet(image, iv, None, W(1), 'r', 'dr9', 0.262**2, valid, {})
    with pytest.raises(ValueError, match='grid_id'):
        engine.evaluate(shifted, Operation.PHOTOMETRY)
    different_band = ObservationSet(image, iv, None, W(), 'g', 'dr9', 0.262**2, valid, {})
    with pytest.raises(ValueError, match='bands differ'):
        engine.evaluate(different_band, Operation.PHOTOMETRY)
