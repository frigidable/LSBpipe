"""Safe scene-to-observation bridge for operation-specific masks."""
from __future__ import annotations
from dataclasses import dataclass
from .scene import MaskDecision, Operation, SceneState, evaluate_mask, grid_fingerprint
from .survey import ObservationSet


@dataclass(frozen=True)
class MaskEngine:
    """Bind a versioned scene to an ObservationSet without implicit resampling.

    Underlying observation quality is always honored. A matching array shape
    without identical WCS header identity is not sufficient for computation.
    """
    scene: SceneState

    def evaluate(self, observation: ObservationSet, operation: Operation | str) -> MaskDecision:
        if observation.band != self.scene.band:
            raise ValueError('Observation and scene bands differ')
        grid = grid_fingerprint(observation.wcs, observation.shape)
        return evaluate_mask(self.scene, observation.image_nMgy,
                             observation.inverse_variance_nMgy2,
                             grid_id=grid, operation=operation,
                             observation_valid=observation.valid)
