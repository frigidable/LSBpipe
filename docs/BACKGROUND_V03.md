# LSBpipe v0.3 P0b — residual-background candidates

**Status:** experimental, not scientifically adopted. This increment provides two models of the *residual background in already sky-subtracted DESI Legacy coadds*. It does not recover any extended emission lost during upstream background subtraction. It does not perform PSF-wing subtraction, neighbor subtraction, mask reconstruction or model selection.

## Numerical methods

`fit_residual_background(image_nMgy, inverse_variance_nMgy2, scene, method=..., grid_id=..., observation_valid=..., settings=...)` fits a candidate from explicitly labelled `SKY_TRAIN` pixels. `SKY_HOLDOUT` pixels are disjoint and assessed after the fit; neither a failed fit nor a failed holdout check can be silently adopted.

- `constant`: inverse-variance-weighted median initialization followed by robust weighted regression with Huber IRLS.
- `tilted_plane`: `B(x,y) = b0 + bx*(x-xref) + by*(y-yref)` with reference pixel at `((nx-1)/2,(ny-1)/2)`. Internally x/y columns are scaled to improve conditioning; output `b0` is nanomaggies per pixel, and `bx/by` are nanomaggies per pixel **per image pixel**.
- The fit uses inverse variance only as a **relative weight**. It does not infer a full covariance matrix, an error bar on the background or independent pixel counts. Robust-scale estimation is data-driven.
- Rank-deficient or severely conditioned training sky returns `ILL_CONDITIONED`; too few valid samples returns `INSUFFICIENT_SKY`. Background maps cannot be evaluated from unsuccessful candidates.
- A `BackgroundCandidate` records training and holdout residual medians/RMS, condition number, iteration count and quadrant-level holdout summaries. Both the original image and the scene remain unchanged.

## Quality control

`fit_status` and `holdout_status` are independent. A candidate can fit successfully yet fail validation, have no usable holdout pixels, or have insufficient spatial coverage. The four quadrant residual summaries help reveal large-scale structure, but **quadrant coverage by itself does not demonstrate statistical independence**. Researchers must select genuinely spatially separated training/holdout blocks with sufficient distance given the coadd noise correlation.

A numeric QA rejection is only applied when the researcher explicitly sets `max_abs_holdout_tile_median_nMgy`. Without it, the status is `MEASURED_UNCALIBRATED`, never automatically `PASSED`. Thresholds are provisional and depend on a scientific measurement goal, sky region size, diffuse contamination and the observational noise model. Meeting a user threshold is not equivalent to publication-ready accuracy.

Example:

```python
from lsbpipe.background import fit_observation_background, BackgroundSettings

# observation: v0.2 ObservationSet, scene: v0.3 SceneState
# Add disjoint SKY_TRAIN and SKY_HOLDOUT layers to scene first.
candidate = fit_observation_background(
    observation, scene, method='tilted_plane',
    settings=BackgroundSettings(max_abs_holdout_tile_median_nMgy=0.001)
)
print(candidate.fit_status, candidate.holdout_status)
if candidate.coefficients_nMgy is not None:
    model = candidate.image(observation.shape)  # new array, no input mutation
```

## Verification

P0b adds nine deterministic tests for constant sky plus outliers, exact tilted plane, rejection by a user-specified holdout tolerance, collinear/insufficient sky, masks and WCS identity, single-quadrant holdout, missing valid holdout data, and invalid settings.

In the development environment without Astropy/Photutils, the complete available suite passed **68 tests; one Astropy-dependent module skipped**. The user's previous v0.3 P0a state passed **63 tests on Windows with Astropy and Photutils**. P0b has not yet been re-run in that full environment.

This result is engineering verification on synthetic images, **not scientific validation on real LSB galaxies**.

## Remaining v0.3 work

P0c will remeasure the same annular/aperture geometry under alternative backgrounds and check `DeltaF = -sum(weights * DeltaB)` at fixed masks. It will not overwrite signed raw flux or impose COG monotonicity. A more complete background QA layer should later include explicitly disjoint sky blocks with separation tests, spatial covariance estimates and injection–recovery experiments.
