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

## Visual evidence from the original four FITS (next QA checkpoint)

The independent image/mask inspector was added after the successful real
Windows coverage test. It uses the already-downloaded originals and does
NOT fetch any data or modify the compressed FITS. Install the optional
matplotlib dependency if necessary:

```powershell
python -m pip install -e ".[demo]"
```

In the existing PowerShell session where `$pilotRoot` is defined:

```powershell
python -m lsbpipe.surveys.qa_plots `
  --directory "$pilotRoot\data" `
  --release dr9 --region south --brick 0401p012 --band r `
  --ra 40.096208 --dec 1.229250 --width-arcmin 5 `
  --target-radius 35 --sky-inner 65 --sky-outer 110 `
  --output "$pilotRoot\qa-dr9-south-r.png"
```

The output is a four-panel WCS-registered diagnostic: original image
flux (asinh display stretch), logarithmic positive inverse variance,
contributing NEXP, and the optical MASKBITS categorical display.
All panels show the sky-coordinate **5′ square**, and the independent
**35″ target circle, 65–110″ annulus** in magenta.

MASKBITS display classes are prioritized solely for readability:
0 exact MASKBITS zero, 1 SGA GALAXY only, 2 other nonzero MASKBITS flags,
3 SATUR_R or ALLMASK_R. Multiple overlapping bits are reduced to
the highest display class; the FITS and numeric `pilot-coverage.json`
remain the authoritative record of every bit. Class 1 **does not imply
the science source must be masked**. Class 2 is a warning, not an
automatically rejected pixel. Class 3 is the current conservative
instrumental rejection class.

The viewport is padded around the 5′ search square. This is still a
**single-brick** view; an incomplete single-brick cutout fails explicitly
instead of filling missing data with zeros and representing them as
coverage. A 5′ square is *not* validated by the earlier circular-aperture
QA alone; the complete footprint must be checked separately.

After rendering, send the PNG together with the latest mask census:

```powershell
$qa = Get-Content "$pilotRoot\pilot-coverage.json" -Raw |
  ConvertFrom-Json
$qa.regions | ForEach-Object {
  Write-Host "REGION: $($_.label)"
  $_.maskbit_counts | Format-List
  $_.maskbit_fractions | Format-List
}
```

This plot is an exploratory QA figure. Neither logarithmic IVAR nor
asinh image contrast is an estimate of limiting surface brightness,
sky contamination, Sérsic parameters or measurement errors.

## P3 exact five-arcminute square audit from original FITS

The earlier 35 arcsec circle and 65–110 arcsec sky ring do not measure the
whole 5′ by 5′ search footprint. The new --inspect-square option uses
the exact tangent-plane sky polygon shared with the official Viewer overlay.
It works from the existing original FITS with no further download:

    python -m lsbpipe.surveys pilot @pilotArgs --inspect-square

Or combine both existing circle/ring and new square diagnostics:

    python -m lsbpipe.surveys pilot @pilotArgs --inspect --inspect-square --target-radius 35 --sky-inner 65 --sky-outer 110

Read $pilotRoot\pilot-square-coverage.json on Windows:

    $sq = Get-Content "$pilotRoot\pilot-square-coverage.json" -Raw | ConvertFrom-Json
    $sq | Select-Object side_arcmin,polygon_within_single_brick,approximate_geometric_fraction,pixel_centers_sampled,estimated_square_pixel_centers,instrumentally_usable_fraction,mean_nexp_where_positive,warnings | Format-List
    $sq.nexp_percentiles_positive | Format-List
    $sq.maskbit_counts | Format-List

The square test uses WCS for each pixel-center selection and separately
projects boundary samples to determine whether one original brick
encloses the entire square. The geometric fraction is only a local
pixel-area approximation, NOT a rigorous mosaic completeness guarantee.
Even 100% instrumental usability does not imply clean sky or sufficient
surface-brightness depth. MEDIUM/Gaia and GALAXY/SGA are diagnostic flags,
not automatic rejections of target flux. All original FITS stay unchanged.
An incomplete single brick is explicitly reported rather than padded.
The status remains DIAGNOSTIC_NOT_ADOPTED.

## Localize saturated / Gaia / SGA mask footprints in sky coordinates

The four-panel QA figure is a human-readable diagnostic, not an
unambiguous attribution of masked pixels to physical sources.
Use the following read-only, offline command on already downloaded FITS:

    python -m lsbpipe.surveys pilot @pilotArgs --localize-masks

The output $pilotRoot\pilot-mask-components.json gives for each of
SATUR_R, MEDIUM, GALAXY, BRIGHT and ALLMASK_R:

- total tagged pixel count inside the square (WCS TAN membership);
- number of 8-neighbor connected regions (not source detections);
- the 20 largest regions with pixel count, centroid (ICRS RA/Dec),
  angular distance to target, cutout-pixel bounding box, and counts
  intersecting the 35 arcsec target circle and the 65–110 arcsec ring;
- warning if the region touches the selected field boundary.

The circular intersection uses exact spherical separations, rather
than the original linearized Jacobian circle and ring selection, so
a few pixel centers exactly on a radius may differ between reports.
Connected regions may be truncated by the selected square edge; a
single Gaia MEDIUM connected region need not correspond to a single
real star if masks overlap. The source catalog association must be
validated separately; a GALAXY footprint may mark the target.

Read the largest components in PowerShell:

    $loc = Get-Content "$pilotRoot\pilot-mask-components.json" -Raw | ConvertFrom-Json
    foreach ($key in 'SATUR_R','MEDIUM','GALAXY') {
      Write-Host "MASK $key"
      $loc.bits.$key | Select-Object total_pixels_inside_square,connected_regions,total_target_circle_pixels,total_sky_annulus_pixels | Format-List
      $loc.bits.$key.regions | Select-Object -First 5 pixels,centroid_ra_deg,centroid_dec_deg,angular_separation_arcsec,sky_annulus_pixels,touches_square_boundary | Format-Table
    }

No FITS modification, masking decision, sky fitting or automatic
photometric acceptance is made by this operation.

## Strict paired DR9 North/South native-grid diagnostic

After downloading and independently inspecting both original quartets,
compare the same pixel centers without mosaicking or exposure stacking:

    $northRoot = 'C:\Users\frigi\Documents\PhD\LSBpipeData\pilot_0401p012_north_r'
    $southRoot = 'C:\Users\frigi\Documents\PhD\LSBpipeData\pilot_0401p012_south_r'
    python -m lsbpipe.surveys.compare_regions --north-directory "$northRoot\data" --south-directory "$southRoot\data" --brick 0401p012 --band r --ra 40.096208 --dec 1.229250 --width-arcmin 5 --output "$southRoot\north-south-paired.json"

Examine the results in PowerShell:

    $pair = Get-Content "$southRoot\north-south-paired.json" -Raw | ConvertFrom-Json
    $pair | Select-Object status,pixels,native_wcs_alignment | Format-List
    $pair.per_region.north | Format-List
    $pair.per_region.south | Format-List
    $pair.paired_instrumental_validity | Format-List
    $pair.paired_mask_flags.SATUR_R | Format-List
    $pair.paired_mask_flags.ALLMASK_R | Format-List
    $pair.paired_mask_flags.MEDIUM | Format-List
    $pair.paired_mask_flags.GALAXY | Format-List
    $pair.paired_nexp | Format-List
    $pair.paired_inverse_variance | Format-List

The comparison is fail-closed if the original North and South FITS
shapes differ or their celestial WCS tie points disagree by >0.001
pixel. No interpolation or missing-pixel fill is performed.
The report counts exact overlapping SATUR_R and ALLMASK_R bits
and separately records invalid image / IVAR / NEXP *outside*
instrumental saturation and ALLMASK flags. Paired NEXP and IVAR
compare only values positive in both reductions at matching pixels.
Interpret the comparison strictly as a reduction-consistency audit:
the two coadds must not be statistically combined as independent
observations merely because both are labeled DR9.
