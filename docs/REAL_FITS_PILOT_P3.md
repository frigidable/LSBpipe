# Real DR9 FITS pilot — P3 entry gate

Status: experimental; no real pixels have yet been measured by this task.
Branch: feature/real-fits-pilot-p3, based on feature/autonomous-ui-system.

## Controlled target

The latest Windows screenshot showed ICRS RA 40.096208 deg,
Dec +1.229250 deg, square search width 5 arcmin. DR9 north and south
both have brick 0401p012, with different brick-median NEXP counts.
These are different reductions, not independent exposures and not
proof that the target pixels have the brick-median depth.

Start with DR9 SOUTH, brick 0401p012, r band.
The four original required FITS candidates are:

- dr9/south/coadd/040/0401p012/legacysurvey-0401p012-image-r.fits.fz
- dr9/south/coadd/040/0401p012/legacysurvey-0401p012-invvar-r.fits.fz
- dr9/south/coadd/040/0401p012/legacysurvey-0401p012-nexp-r.fits.fz
- dr9/south/coadd/040/0401p012/legacysurvey-0401p012-maskbits.fits.fz

Official science products: https://www.legacysurvey.org/dr9/files/

## Windows PowerShell procedure

All science data go outside the source repository, under LSBpipeData.

    cd C:\Users\frigi\Documents\PhD\LSBpipe
    git fetch origin
    git switch --track origin/feature/real-fits-pilot-p3
    python -m pytest -q
    $pilotRoot = "C:\Users\frigi\Documents\PhD\LSBpipeData\pilot_0401p012_south_r"

A. Plan (catalog search may use the network on cache miss; no science GET):

    python -m lsbpipe.surveys pilot --ra 40.096208 --dec 1.229250 --width-arcmin 5 --release dr9 --region south --brick 0401p012 --band r --directory $pilotRoot

B. Verify four archive URLs with HEAD, preserving unknown versus 404:

    python -m lsbpipe.surveys pilot --ra 40.096208 --dec 1.229250 --width-arcmin 5 --release dr9 --region south --brick 0401p012 --band r --directory $pilotRoot --check-urls

C. Explicitly download, after reviewing pilot-manifest.json:

    python -m lsbpipe.surveys pilot --ra 40.096208 --dec 1.229250 --width-arcmin 5 --release dr9 --region south --brick 0401p012 --band r --directory $pilotRoot --download

D. Validate the downloaded science FITS (no further science GET):

    python -m lsbpipe.surveys pilot --ra 40.096208 --dec 1.229250 --width-arcmin 5 --release dr9 --region south --brick 0401p012 --band r --directory $pilotRoot --inspect --target-radius 35 --sky-inner 65 --sky-outer 110

Results: pilot-manifest.json, pilot-report.json, pilot-coverage.json,
data/dr9/south/coadd/040/0401p012/... and the download receipt.
A 404/410 aborts downloading; a HEAD failure is NOT automatically
equivalent to a missing file. Existing local files are explicitly
marked REUSED_LOCAL_UNVERIFIED rather than claimed to match remotely.

Optional: repeat in a DIFFERENT pilotRoot with --region north to compare
DR9 north and south. Do not combine them as independent measurements.

## Required checks

The local coverage diagnostic independently reports target and outer
annulus pixel-center counts, approximate geometric brick footprint,
finite IMAGE, positive INVVAR/NEXP, conservative SATUR_R/ALLMASK_R
screening and BRIGHT/GALAXY warning fractions. It validates consistent
shape and celestial WCS across all four FITS and records actual HDU
indices and header BUNIT strings when present.

Example radii 35/65/110 arcsec are screening placeholders, NOT
adopted galaxy isophotes or clean background regions. This does
not model correlated noise, PSF wings or sky uncertainty.

For DR9 the official files documentation describes coadded image units
as nanomaggies/pixel, inverse variance of the coadd and NEXP per pixel.
MASKBITS optical raster is HDU1. Compressed image files may have an
empty primary and one unambiguous science-image extension; the
loader records its actual choice and rejects multiple ambiguous HDUs.

## Scientific acceptance

This stage is NOT a mosaic and cannot prove complete coverage of
a five-arcminute field with a single brick. A real Windows run with
verified original files and an independently cross-checked coverage JSON
is required before multi-brick coverage P3 or photometric adoption.

Direct NERSC FITS download was unavailable from the assistant execution
environment while writing this plan. No HTTP or real-pixel numerical
results are claimed here.

## Windows field-run checkpoint — 2026-10-31

Actual user-run evidence (not derived from a synthetic fixture):

- `python -m pytest -q`: **221 passed in 19.34 seconds** on Windows.
- `pilot` dry-run: **4 candidate files**, not yet verified.
- `pilot --check-urls`: **HTTP_OK ×4**.
- `pilot --download`: **FOUR_FILES_FETCHED_OR_REUSED_NEEDS_FITS_QA**.
  Actual per-file states and SHA-256 are stored in
  `data/download-report.json`, not printed in the short CLI output.
- `pilot --inspect` with 35/65/110 arcsec diagnostic radii:
  **FITS_QA_DIAGNOSTIC_NOT_ADOPTED**; the original four local FITS were
  successfully read through the shape/WCS/pixel screening code.

The *numerical contents* of `pilot-coverage.json` and the file receipt
have **not yet been independently examined**, so no science completeness
fraction, depth or successful photometry is claimed.

### Follow-up: historical evidence preservation

The first on-device `--inspect` command printed
`{"UNVERIFIED_URL": 4}` even though the immediately preceding download
had reported `HTTP_OK ×4`. Root cause: each invocation regenerated the
manifest and overwrote the single most recent report. It did *not*
invalidate the already downloaded FITS.

New experimental fix:
- HEAD results are additionally persisted in `pilot-probe.json` with a
  UTC observation timestamp, and are reused only when the **complete
  field, release, region, brick, band and all four URLs** match exactly.
- `http_evidence_source` explicitly distinguishes
  `CURRENT_RUN`, `PREVIOUS_RUN_NOT_RECHECKED`, and `NOT_CHECKED`.
  A previously successful HEAD is never presented as a fresh check.
- The original `data/download-report.json` is linked as an independent
  previous-run receipt when its field and four selected product paths
  match; this is *not* an authoritative remote-byte checksum.
- FITS QA exceptions now record `FITS_QA_FAILED` and the error rather
  than leaving an older successful report as the latest result.

**Migration for the pilot that ran under the old code:** the earlier
HEAD-only evidence snapshot did not yet exist and was overwritten.
After pulling the updated branch, run `--check-urls` once more,
then `--inspect`. No new science FITS download is required.
Please inspect the numerical `pilot-coverage.json` separately.
