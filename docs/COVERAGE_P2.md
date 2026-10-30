# P2 — Real-image Coverage Intelligence (experimental)

## Purpose

Unlike the `survey-bricks` summary median NEXP, P2 reads actual downloaded
FITS pixel values and answers a constrained question: *how much of a chosen
circular target region and surrounding annulus lies within one source brick,
and how many pixels survive basic instrumental screening?*

P2 **does not** decide that a galaxy is measurable, determine the extent of an
LSB galaxy, fit any sky, or fill a masked region. A scientific acceptance rule
requires separate depth/sky noise, PSF-wing and correlated-noise validation.

## Preparation on Windows

From the `feature/survey-explorer-v04` branch:

```powershell
git pull --ff-only
python -m pytest -q
python -m lsbpipe.gui
```

Use **DR9 only** for the initial test at
RA=154.7709°, Dec=46.4537°; four intersecting north bricks were actually
discovered on Windows. Choose one brick from the list (e.g., `1544p465`),
and download exactly these four science products **in a single band**:

- `image-r.fits.fz`
- `invvar-r.fits.fz`
- `nexp-r.fits.fz`
- `maskbits.fits.fz` (optical bitmask in **HDU1**)

In the GUI, set the brick filter, then **Clear visible**, select just the
four requested rows for that brick, click **Check selected files**, and
review any `HTTP_MISSING` or `HTTP_ERROR` statuses before download.
Be aware that **Clear visible affects only currently shown rows**: if you
left other bricks selected while filtering, they remain selected. The
preflight dialog always shows the actual overall selected-file count and
lets you cancel.

After downloading, select the same brick in the upper table, specify a
circular target radius and a separate sky annulus, and click
**Inspect local FITS**. Pick `r` and the **root download directory**
(the folder containing `dr9/north/coadd/...`). The report appears in a
dialog and is saved to `coverage-reports/dr9_north_1544p465_r.json`.

For reproducibility, the same test is available without Qt:

```powershell
python -m lsbpipe.surveys inspect-coverage `
  --directory C:\LSBpipeData --release dr9 --region north `
  --brick 1544p465 --band r `
  --ra 154.7709 --dec 46.4537 `
  --target-radius 60 --sky-inner 90 --sky-outer 180 `
  --output C:\LSBpipeData\coverage-reports\pilot-r.json
```

**Radii above are examples, not a measured LSB galaxy radius.**
Choose radii based on inspected data, expected light distribution and
the availability of uncontaminated sky. Do not treat this default
annulus as a validated sky-training mask.

## Metrics

P2 validates that all four raster inputs are 2D, share the same shape,
and have mutually consistent celestial WCS at multiple tie points
(tolerance 0.001 image pixel). It does not reproject mismatched arrays.

For each screen region it reports:
- sampled pixel-center count and approximate geometric footprint fraction
  (a partial brick can have low footprint even when every sampled pixel is good)
- fractions of finite image pixels, positive finite inverse variance, and
  positive finite NEXP separately
- *conservative* usable fraction: all the above AND neither `SATUR_B`
  nor `ALLMASK_B` is set for that band
- average contributing NEXP among positive-NEXP sampled pixels
- `BRIGHT` and `GALAXY` MASKBITS fractions as warnings, **not**
  automatic signal exclusion
- explicit warnings for an incomplete aperture footprint and invalid pixels

The geometric regions use a local WCS tangent-plane Jacobian evaluated at
the given celestial centre, and scan FITS rasters in row chunks. They are
circular *screening* regions, not elliptical isophotes; distortions across
large angular scales are not evaluated. Sampled pixel centers are not the
same as fractional pixel areas. The approximate geometric footprint fraction
is intentionally diagnostic, not a sub-pixel calibrated aperture integral.

The per-pixel noise in Legacy coadds is correlated by resampling. No
covariance model, background uncertainty, correlated-pixel correction,
scattered-light model, extinction correction or colour-term correction is
included. These are later photometric stages.

## DR9/DR11 north relationship

DR11 northern coadds are reductions of observations already used in DR9.
The same brick, `r` band in DR9 and DR11 can represent **distinct reduction
products** even when they originate from the same raw observations. The
download plan therefore preserves both paths and tags
`related_release_groups` rather than falsely asserting that one can be
discarded. It reports `unique_spatial_bricks` separately from per-release
brick records. Do not average DR9 and DR11 as independent measurements.

## Validation

Offline synthetic science tests cover a complete small WCS grid, missing
NEXP, non-finite/zero IVAR, band-specific saturation, warning masks, cutout
at a brick edge, FITS HDU1 for maskbits, WCS mismatch and missing files.
They are regression tests, not a calibration on actual faint galaxies.

Before science integration, a user should carry out the one-brick test,
inspect the generated JSON, compare statistics with Astropy independently,
and validate that the selected field requires one or multiple bricks for its
full target and clean-sky footprint.
