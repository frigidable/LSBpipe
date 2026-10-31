# FITS Explorer quantitative scales / MASKBITS / QUALITY — source-level audit
Date: 2026-10-31. Branch: `feature/science-qa-gui` (draft PR #6). No changes to `main`.

## Scope and provenance
Inspected `display_transfer.py`, `fits_raster.py`, `quality_map.py`,
`fits_explorer.py`, `survey.py`, `surveys/coverage.py`,
`surveys/qa_plots.py` and corresponding pure NumPy and Qt tests.

Official reference: [Legacy Surveys DR9 bitmasks](https://www.legacysurvey.org/dr9/bitmasks/)
and [DR9 original coadd products](https://www.legacysurvey.org/dr9/files/).
MASKBITS are integer powers of two; optical masks are from HDU1, not WISE HDU2/HDU3.
IMAGE is in nanomaggies/pixel, INVVAR in inverse squared nanomaggies,
and NEXP denotes contributing exposures. Neither an image stretch nor a
raw INVVAR/NEXP comparison measures adopted LSB surface-brightness depth.

## Confirmed defects and corrections
1. **Logarithmic empty-data domain.** For all-zero INVVAR, or IMAGE without
   positive finite pixels, the former automatic range `(0, 1, "log")` was
   not a legal logarithmic manual range. The Qt colorbar could fail when it
   tried to render that range. The renderer now emits an explicitly unavailable
   logarithmic range (NaN bounds used strictly as a sentinel), draws no
   colorbar, and says that there are no positive finite source samples.
   No arbitrary positive range or flux offset is invented. Pixel hover keeps
   the original signed data. Synthetic numerical and Qt regression cases
   exercise both IMAGE and INVVAR.
2. **Fixed-stride percentile aliasing.** Sampling a flattened image at
   `flat[::stride]` can completely miss periodic populations of pixels.
   A fixture with 25% pixels at 100 nanomaggies and every-fourth-pixel
   spacing reproduces the error. Auto display limits now use a deterministic,
   seeded uniform sample of at most 250,000 original pixels, with
   replacement for large rasters. This is a *display-only approximation*,
   not a scientifically calibrated noise/quantile estimator.
3. **Conflicting visual priorities.** The translucent overlay formerly
   allowed SATUR/ALLMASK to hide numeric failure, while categorical QUALITY
   ranked numeric failure highest. Both now apply:
   numerical invalid > same-band ALLMASK > same-band SATUR >
   non-exclusion warnings > zero-bit numerically valid.
   User toggles still affect visibility only, never the scientific mask.
4. **False 'clean' labels for known and unknown MASKBITS.** A pixel bearing
   NPRIMARY, BAILOUT, CLUSTER, a different band's saturation, or an
   unrecognized higher bit could formerly enter the QUALITY clean class
   because only BRIGHT/MEDIUM/GALAXY warnings were explicitly colored.
   QUALITY now reserves class 0 for zero-bit and numerically valid pixels,
   and colors **every other nonzero flag** not superseded by the current
   band's hard masks as a generic diagnostic warning. The summary key is
   `other_bit_not_excluded` (previously
   `catalog_warning_not_excluded`), and the legend is updated.
   This is a display classification, not an expansion of hard exclusions.
   In particular, GALAXY is not automatically a veto on target pixels.

## Interpretation and separation of responsibilities
- `QUALITY` is one mutually exclusive five-class *diagnostic*, on all
  original loaded cutout pixel centers. Its histogram does not refer to
  the square sky field or target/sky annulus unless an explicit identical
  footprint selection is implemented later.
- `MASKBITS` uses its earlier four-class general categorical overview;
  unlike QUALITY, it does not include numerical validity, and combines
  SATUR/ALLMASK in one visible category. Its differing grouping is intentional.
- `overlay_rgba` uses independently enabled visual flags, hence an
  overlay with disabled layers is not itself a definitive QA census.
- Existing `coverage_from_arrays` per-band SATUR/ALLMASK policy and
  per-region JSON are untouched; even SATUR indicates saturation in at least
  one contributing observation, not necessarily that all coadd signal
  is unusable. A different selection policy requires a separate science
  justification.
- Samples from resampled coadds are correlated; pixelwise INVVAR and NEXP
  do not by themselves calibrate aperture uncertainty or limiting
  surface brightness.
- These changes do not read the user's original Windows DR9 coadds.
  All new fixtures are explicitly synthetic. All PRs remain Draft.

## Verification targets
- Pure tests: log-domain empty cases, zero/signed IMAGE,
  periodic percentile aliasing, deterministic auto range, overlapping
  flags, other/future flags, complete histogram, immutable source buffers.
- Qt tests: actual `QLabel` for unavailable log colorbars, rapid
  layer/toggle changes, original FITS readout unchanged.
- CI: Windows/Ubuntu science-core and Windows offscreen screenshot
  workflows must pass on the final commit. A green workflow on an earlier
  commit is not verification of a later commit.
- On-device: open pre-downloaded DR9 0401p012 North/South science quartets
  in the GUI on the user's Windows computer; verify colorbar, pixel readout,
  warning colors, independent flag visibility and interactive performance.
  This final on-device gate remains pending.

## Next science/UX gate
Implement an explicit footprint-aware mask census with separate
`loaded cutout`, `requested TAN square`, `target screening circle` and
`sky screening annulus` scopes. Keep *exact pixel-center denominators*,
bitwise overlaps and numerical invalidity together in one report, and
compare to the already validated DR9 paired North/South result on the
same sky footprint. Do not silently interpret QUALITY colors as an
adopted contamination mask. After that, profile original FITS memory and
render latency on Windows (incl. high DPI), then design tiled/pyramid
display rather than downsampling without notice.

No merger into `main`, no science photometry adoption, no claims
of an observed PSF, sky model, depth or real GUI validation.
