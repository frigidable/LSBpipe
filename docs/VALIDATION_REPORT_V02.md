# LSBpipe v0.2 — validation report

Date: 2026-10-30. Version: 0.2.0. Scientific acceptance: NOT APPROVED.

## Executed checks

Command: `PYTHONPATH=src python -m pytest -q`

Result: **52 passed; 1 skipped**. The skipped unit is the full
`tests/test_fits_io_astropy.py` module: it contains four tests but could not be
collected because `astropy` was unavailable. No actual Astropy-based FITS/WCS
integration was executed here. `photutils` was also unavailable.

**New executed validations**:
- 20 combinations of local WCS affine geometry (5 Jacobians × 4 sky
  PA/axis-ratio choices) recover exact quadratic ellipse boundaries to
  floating-point precision; these include rotations, reversed axes,
  non-square pixels and shear.
- World PA periodicity modulo 180, area-Jacobian determinant, rejected singular
  transformations, conversion of sky radius grid.
- Per-band SATUR/ALLMASK exclusions, preservation of BRIGHT/GALAXY as
  information, signed int16 high-bit reinterpretation, ivar masking and array
  immutability.
- An independent binary-layout check on generated SYNTHETIC FITS triplets:
  header fields, data offsets, 2880-byte block alignment, typed binary payload.
- Legacy v0.1 mathematical profiles, rings, raw signed COG and synthetic Sérsic
  validations retained and passed.

The standard synthetic v0.1 demo (`seed=42`) was rerun and reproduces:
- intrinsic Sérsic model total: 256.1757272569881 nMgy
- analytic flux inside demo aperture: 254.38546652116443 nMgy
- measured unmasked flux: 255.6189605832162 nMgy
- measured valid-pixel masked flux: 218.25103092895256 nMgy
- finite-aperture R50: 19.932462821206848 pix

## Integration tests written but NOT executed

1. Astropy FITS read and use of returned celestial WCS.
2. On-image RA/Dec to pixel geometry conversion at declination +78 degrees.
3. Reject image/ivar mismatch even when shapes are equal.
4. Generate a WCS-preserving 64×64 cutout from a larger FITS input.
5. Photutils exact method (nested inside one of the test functions).

All five checks must be run in an environment with `astropy>=6` and
`photutils>=2` before any claims of real-data readiness.

## Known scientific and engineering limitations

- Observational background model is NOT fitted; CLI receives a caller-chosen
  constant residual background (default 0, not a measured zero).
- Local affine approximation to sky ellipse; WCS scale change across large
  apertures has not been mapped or flagged quantitatively.
- `inverse_variance` provides diagonal-only statistical variance, not
  resampling-induced covariance or systematic uncertainties.
- No PSF, halo subtraction, neighbour deblending, 2D model or sky estimation.
- No completeness/coverage science acceptance thresholds.
- FITS I/O is not yet proven on actual DR9/DR11 source files, including
  optional FITS tile compression and HDU selection.
- The mock FITS images are synthetic test data. Actual NGC2146sat files are not
  available in this workspace.

## Next acceptance gate

Run `python -m pytest -q` with extras `.[exact,test]`; all tests in the skipped
module must execute. Then run `measure-fits` on a frozen real DR9 data triplet,
record hashes, verify WCS co-registration and geometry orientation visually,
compare exact vs high-subpixel aperture results and old NGC2146sat measurements.
Only then advance to scientific sky estimation / background-model ensembles.
