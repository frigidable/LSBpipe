# LSBpipe v0.3 P0c — Fixed-geometry photometric sensitivity

**Status:** experimental diagnostic only, not a science-adopted photometric correction or a calibrated uncertainty model.

The P0c module `src/lsbpipe/sensitivity.py` remeasures **identical pixels, masks, PSF/unsubtracted neighbor model, annular edges, aperture geometry and fractional pixel weights** under alternative candidate residual-sky maps. It delegates every flux measurement to the v0.2 `measure_profiles` implementation without modifying the numerical engine.

For a fixed valid-pixel indicator `v_i` and geometric fractional aperture weight `a_i`, the effective weight is `w_i = v_i * a_i`. If model B replaces reference model A,

```text
F_B - F_A = -sum_i w_i * (background_B[i] - background_A[i]).
```

This identity applies to every finite annulus and each raw cumulative aperture. P0c records both **the difference of the independently remeasured fluxes** and **the prediction from the background-map difference**. The latter calculation avoids subtracting two large observed fluxes and can reveal cancellation issues. Unusable annuli retain `NaN` annular flux; completely unusable cumulative apertures retain zero raw measured flux, not filled flux.

## API

```python
from lsbpipe.sensitivity import compare_observation_backgrounds
from lsbpipe.geometry import ReferenceGeometry

comparison = compare_observation_backgrounds(
    observation, scene,
    geometry=ReferenceGeometry(x_pix=200, y_pix=220, q=.7, theta_image_deg=25),
    edges_pix=[0, 10, 20, 30, 40],
    backgrounds={'uncorrected': 0., 'constant': constant_candidate,
                 'tilted_plane': plane_candidate},
    reference='uncorrected',
    backend='exact',
)
delta = comparison.deltas['tilted_plane']
print(delta.cog_nMgy, delta.predicted_cog_nMgy)
print(comparison.holdout_statuses)
```

For stand-alone non-FITS inputs, use `compare_backgrounds(frame, geometry, edges_pix, backgrounds, reference=..., backend=...)`, where `frame` is the existing v0.2 `MeasurementFrame`. Backgrounds may be finite scalars, image-sized arrays or fitted `BackgroundCandidate` instances. Failed fits cannot be evaluated. A holdout-rejected candidate **may** be compared for diagnostic purposes, but its holdout status is preserved in the output; no model is automatically adopted.

**Replacement semantics:** each input candidate *replaces* `MeasurementFrame.background`; candidates are not added to an existing correction. A neighbor model already in the frame remains identical for every scenario.

The `compare_observation_backgrounds` adapter uses `MaskEngine(scene).evaluate(observation, PHOTOMETRY)` and the `ObservationSet` quality mask. It rejects a mismatch between scene and observation WCS/grid/band. Geometry is expressed in **pixel**, not sky-PA coordinates, and must be computed from the observation WCS by the caller.

## Interpretation and remaining gaps

- Differences between deterministic candidate sky maps are **sensitivity diagnostics**, not confidence intervals, background posterior distributions or an error budget.
- A flat and a tilted sky model fitted to the same pixels are **correlated** alternatives, not independent random perturbations. Correlated coadd noise and bias from galaxy emission leaking into sky training regions are not characterized here.
- As of P0c, `BackgroundCandidate` does **not** record its source `grid_id` or band. The adapter verifies the *observation and scene*, but **cannot itself establish that an arbitrary supplied candidate was fitted on this grid**. Until that provenance is added, researchers must provide candidates from the same source scene and image. This needs closing before scientific adoption.
- `ReferenceGeometry` is pixel-space. Sky-PA conversion and reference frame identity must be maintained explicitly by the caller.
- `R_50` and Sérsic `R_e` are distinct quantities; this P0c PR does not infer an effective radius from potentially nonmonotonic curves of growth.
- Inverse variance in v0.2 supplies only diagonal pixel noise. The systematic envelope among these candidates is not a statistical sky covariance estimate.
- No imputation of missing light, PSF subtraction, new geometry fit or automatic COG monotonicity is performed.

## Verification

Nine new tests cover a constant background with masked area, a tilted plane with asymmetric masks and image-footprint truncation, signed negative annular flux / nonmonotonic COG, completely masked annuli, replacement rather than additive semantics, failed/invalid candidate maps, preservation of rejected holdout status, WCS/band/observation-mask checking, and exact Photutils fractional pixel geometry.

In the local runtime: **76 passed, 2 skipped** (one Astropy integration test module and the Photutils-only exact test). The previous user-verified Windows baseline is **72 passed** at P0b. Full P0c Windows validation is outstanding; with both dependencies installed, **81 passed** is expected if the new exact-backend test succeeds.

No real coadd or independent injection–recovery science validation has been performed.
