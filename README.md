# LSBpipe v0.2 — Legacy FITS / WCS experimental integration

**Scientific status: experimental, not publication-ready.** This builds directly
on the mathematically benchmarked fixed-ellipse v0.1 prototype. It does not
implement independent sky estimation, PSF correction, colour matching, star
subtraction, or a complete uncertainty model.

## What's new

- `ObservationSet`: load matched 2D image, inverse-variance, shared MASKBITS FITS
  for Legacy DR9/DR10/DR11, with explicit band and release. FITS loader uses
  **Astropy** and rejects mismatched WCS grids even when array shapes match.
- `LocalSkyEllipse`: transform true astronomical position angle (N through E)
  and sky axis ratio into a *local* pixel ellipse from the differential WCS.
  Handles reflections, axis rotations, shear, and non-square pixels without
  pretending sky-PA is a pixel-space rotation angle.
- Semantic, **band-dependent** mask policy: nonfinite / nonpositive ivar invalid;
  `SATUR_BAND` and `ALLMASK_BAND` conservatively rejected by default;
  `BRIGHT`, `MEDIUM`, `GALAXY`, etc. retained as warnings, **not automatically
  rejected**. Override satur/allmask only as a documented sensitivity test.
- Fast optional on-the-fly sky-centered FITS cutouts via `Cutout2D`, necessary
  for memory-efficient work on Legacy brick images.
- `measure-fits` CLI produces observed-valid-pixel radial profiles / raw COG
  (CSV), with hashed file provenance, geometry and caveats (JSON). User passes
  an **explicit residual background value** (zero by default); no sky estimator.
- `scripts/make_mock_legacy_fits.py` generates three genuinely formatted but
  **entirely synthetic** FITS images for an offline integration test.

## Install on Windows / Linux

In the extracted directory, Python 3.10+:

```bash
python -m pip install -e ".[exact,test,demo]"
python -m pytest -q
python -m lsbpipe --help
```

`exact` installs Astropy + Photutils, required for the recommended exact
aperture-overlap backend. To use the independent (approximate) NumPy subpixel
backend, install `.[astro,test,demo]` and pass `--backend subpixel`.

## Synthetic mock FITS example (not real telescope data)

```bash
python scripts/make_mock_legacy_fits.py --output mock_fits --size 128
python -m lsbpipe measure-fits \
  --image mock_fits/mock-image-r.fits \
  --ivar mock_fits/mock-invvar-r.fits \
  --maskbits mock_fits/mock-maskbits.fits \
  --release dr9 --band r \
  --ra 96.0619 --dec 77.9684 --q 0.63 --pa 127 \
  --max-radius 8 --step 1 --background 0.0003 \
  --backend exact --output mock_output
```

PA is in degrees east of north on the sky; `max-radius` / `step` are arcsec;
`background` is nMgy per pixel and **must be separately estimated** for real
science. `--pa 127` is selected to correspond approximately to the mock source
pixel orientation of 37 degrees under this reflected, north-up WCS.

### With real DR9 NGC2146sat images

Provide the co-registered, same-band **science image**, **inverse variance**, and
**shared MASKBITS**. Their actual file paths are specific to your local data.
The example below is a command template, **not a completed DR9 run**:

```bash
python -m lsbpipe measure-fits \
  --image "<path-to-coadd-image-r.fits>" \
  --ivar "<path-to-coadd-invvar-r.fits>" \
  --maskbits "<path-to-coadd-maskbits.fits>" \
  --band r --release dr9 \
  --ra 96.0619 --dec 77.9684 --q 0.61 --pa 135 \
  --cutout-size 600 --max-radius 65 --step 2 \
  --background 0 --backend exact \
  --output "ngc2146sat_r_trial"
```

The geometry / background values are **illustrative initial choices**, not
adopted calibrated NGC2146sat measurements. If source files use compressed FITS
with data in HDU 1, pass `--image-hdu 1` / `--ivar-hdu 1` / `--maskbits-hdu 1`
selectively. `--cutout-size` is in pixels. Original files remain unchanged.

For images that have already been cropped on a common WCS grid, omit the
`--cutout-size` argument. An exact method can be slow for hundreds of apertures;
use small, scientifically justified cutouts first.

## Photometry contract and limitations

Input image units: **nanomaggies per pixel**. Input inverse variance:
**1/(nanomaggies)^2**. Unknown external FITS units cannot be silently assumed;
this loader is for documented Legacy coadds (not arbitrary survey FITS).

The original pixel values and raw signed aperture sums are preserved. Missing
pixels are not set to zero *as physical measurements* or reconstructed: their
weights are zero in the integral and valid area is tracked separately. COG is
**observed valid-pixel flux**, not full galactic flux. The statistical errors
propagate **only diagonal pixel inverse variances**; they are not appropriate as
total errors for correlated Legacy coadds. The WCS sky ellipse is an affine
linearization about the target; for large fields/strong distortion a full sky
aperture integration is needed. No sky/model estimation, galaxy detection,
PSF, matched colours or automated science acceptance are included.

`manifest.json` always states `EXPERIMENTAL_NOT_ADOPTED`. No numerical result
produced here should be reported as an adopted NGC2146sat measurement.

## Verification recorded for this environment

- **52 executed automated tests passed** with NumPy/SciPy/pytest.
- **One module skipped** (`test_fits_io_astropy.py`, containing 4 integration
  tests) because Astropy/Photutils were unavailable and package downloads were
  blocked. This includes reading the actual FITS bytes using Astropy.
- FITS mock writer output verified independently for 2880-byte FITS padding,
  header cards, supported BITPIX values and binary array contents.
- The legacy v0.1 synthetic demo still reproduces its earlier statistics.

See `docs/VALIDATION_REPORT_V02.md` and `docs/TECHNICAL_DESIGN_V02.md`.

## Primary references

- [Legacy DR9 bitmasks](https://www.legacysurvey.org/dr9/bitmasks/)
- [Legacy DR11 bitmasks](https://www.legacysurvey.org/dr11/bitmasks/)
- [Legacy DR9 image files](https://www.legacysurvey.org/dr9/files/)
- [Astropy Cutout2D](https://docs.astropy.org/en/stable/api/astropy.nddata.utils.Cutout2D.html)
- [Photutils EllipticalAperture](https://docs.astropy.org/projects/photutils/en/latest/api/photutils.aperture.EllipticalAperture.html)
