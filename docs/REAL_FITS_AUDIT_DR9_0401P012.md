# P3: First real DR9 South FITS — independent audit record

## Data provenance

The Windows operator ran LSBpipe on **2026-10-31**, against the original
DESI Legacy Surveys DR9 **south** coadd, brick `0401p012`, band `r`.
Requested ICRS center was **(40.096208°, +1.229250°)** with a
**5′ × 5′** square search field. The aperture QA actually measured
a circular `r ≤ 35″` region and a `65″ ≤ r ≤ 110″` sky annulus.
These diagnostic circles do not cover the full 5′ search square.

Observed local setup: 225/225 pytest checks passed (7.32 s).
The pilot explicitly probed four URLs with `HTTP_OK` and downloaded
four files. Follow-up `--check-urls --inspect` returned
`FITS_QA_DIAGNOSTIC_NOT_ADOPTED`, `HTTP_OK × 4` and no exception.

Original local transfer receipts (SHA-256 is a **local fingerprint**,
not an independently confirmed checksum supplied by NERSC):

| Original DR9 South product | Bytes | SHA-256 |
|---|---:|---|
| image-r.fits.fz | 11,868,480 | dc3bc2794caa5b77560dc0bf305c728af203f72a72fa11fad331fb1aab7fc8da |
| invvar-r.fits.fz | 11,272,320 | 9bb0fbfacec9d014568ce9ef77e37b014f8e9f75fbf79dc33a5f5fa202f0527a |
| nexp-r.fits.fz | 259,200 | 7a64a8188f855d351398e88f0525b18b21b965dda3a2fc2b41b1e221b36f195e |
| maskbits.fits.fz | 276,480 | 764bce3f6311557fb951d9cd9c23abda950e559433da4e7d30aa9e44380fa15c |

Total local FITS bytes: **23,676,480**. All four were marked
`DOWNLOADED`, no reported errors. Full relative paths appear in
`pilot-manifest.json` and `data/download-report.json`.

## FITS structural checks

- Shape: **3600 × 3600 pixels** for all four images.
- WCS-compatible at tested tie points (according to existing LSBpipe
  WCS tolerance); no independent external WCS solution here.
- Derived pixel area: **0.06864396080280052 arcsec²**; equivalent local
  square-pixel scale **0.261999925″/px** (consistent with 0.262″).
- Recorded science HDU indices: **1 for all four**. Tile-compressed
  image extensions are supported; optical MASKBITS is explicitly HDU1.
- Header `BUNIT`: image `nanomaggy`, invvar `1/nanomaggy^2`;
  NEXP and MASKBITS did not supply BUNIT strings.

Official references:
- https://www.legacysurvey.org/dr9/files/
- https://www.legacysurvey.org/dr9/bitmasks/

## Actual measured aperture QA

| Metric | Target circle ≤35″ | Sky annulus 65–110″ |
|---|---:|---:|
| Pixel centers sampled | 56,067 | 360,398 |
| Approx geometric footprint | 100.000% (capped) | 99.9963785% |
| Finite IMAGE | 100% | 100% |
| Positive INVVAR | 100% | 100% |
| Positive NEXP | 100% | 100% |
| Instrumentally usable, current policy | 100% | 100% |
| Mean NEXP where positive | 7.073269481 | 8.318816975 |
| BRIGHT bit | 0 | 0 |
| GALAXY bit | 14,942 / 56,067 = **26.6502577%** | 0 |

The mean NEXP in the annulus is **17.61% higher** than in the target
(only a *sampled mean*, not a significance estimate or inverse-variance
weight). The 100% usability criterion currently means finite flux,
positive inverse variance, positive exposure and exclusion of
`SATUR_R`/`ALLMASK_R` only. It does **not** establish a clean sky,
unbiased PSF wings, independent coadd noise or adequate surface
brightness depth.

Official DR9 `MASKBITS` bit **12** named `GALAXY` indicates pixels
in a large SGA galaxy area. The 14,942 flagged central pixels are **not
intrinsically unusable**. They may overlap the target galaxy itself, so
automatically masking bit 12 would destroy precisely the desired flux.
Without a spatial mask map and SGA cross-identification, we cannot
attribute the flagged pixels to the target or a neighbor.

The background ring has no `GALAXY` or `BRIGHT` flags in the
original limited diagnostic. That **does not prove uncontaminated
background**: unflagged ordinary galaxies/stars and extended PSF halos
need separate segmentation and sky checks. `MEDIUM`, `NPRIMARY`,
`BAILOUT` and other bits had not been reported in the original run.

## Follow-up added to P2 diagnostic

`RegionCoverage` now additionally reports named per-region
`maskbit_counts` and `maskbit_fractions` for `NPRIMARY`,
`BRIGHT`, `MEDIUM`, `GALAXY`, `CLUSTER`, `BAILOUT`,
`SATUR_R` and `ALLMASK_R`, plus explicit source/sky warnings.
This is **diagnostic only** and does not alter the hard valid mask.
Tests verify an SGA-marked target pixel remains scientifically
unrejected by a bad-pixel policy; a saturated pixel does not.

The original results above are measurements from the earlier user run.
They were subsequently retested with the updated MASKBITS census on
Windows; see the second field-run checkpoint below.

## Next scientific acceptance steps

1. Pull latest `feature/real-fits-pilot-p3`, run pytest, then
   rerun the existing local `--inspect` without re-downloading.
   Collect updated per-bit census and warnings.
2. Plot the target-centered IMAGE/INVVAR/NEXP/MASKBITS layers at
   matched WCS and overlay SGA GALAXY and other risk-bit footprints.
3. Independently inspect the 5′ square, not just the 35″ disk and
   65–110″ ring. Require explicit background-training mask and
   uncertainty treatment.
4. Run the **North** reduction separately, but do not combine
   `north` and `south` as independent raw observations.
5. Only then design P3 multi-brick coverage/overlap policy.

Current state: **instrumental QA passed; LSB photometric adoption
NOT established**. No sky surface brightness, asymptotic magnitude,
Sérsic fit or galaxy/sky photometric measurement is reported.

## Second Windows field-run checkpoint: numerical MASKBITS census

The user subsequently ran **231/231 passing tests (8.97 s)** on the
same feature branch and successfully exported
qa-dr9-south-r.png from four original local FITS through the new
WCS-registered plotting module. The PNG itself was not attached to the
chat: its pixel-level visual interpretation is still outstanding.

Recomputed original DR9 South r-band aperture census:

| Named bit | Target circle (56,067 px) | Sky annulus (360,398 px) |
|---|---:|---:|
| GALAXY (SGA; bit 12) | 14,942 (26.6503%) | 0 |
| MEDIUM (Gaia G<16 locus; bit 11) | 0 | 178 (0.04939%) |
| NPRIMARY, BRIGHT, CLUSTER, BAILOUT | 0 | 0 |
| SATUR_R, ALLMASK_R | 0 | 0 |

Real output warnings: TARGET_SGA_GALAXY_BIT_PRESENT_DO_NOT_AUTOMASK
and SKY_ANNULUS_MEDIUM_WARNING. The official DR9 bitmask reference
(https://www.legacysurvey.org/dr9/bitmasks/) explains MEDIUM as
a Gaia star G<16 radius-magnitude footprint with a radius twice
the BRIGHT mask at the same magnitude. Therefore 178 sky-annulus
pixels require spatial contamination assessment; a zero hard-instrument
mask does not mean the sky is photometrically clean.

The user reports successful PNG creation, but neither the figure pixels
nor spatial association of the SGA region have yet been visually checked.

## Full 5′ square follow-up

Added --inspect-square to the pilot CLI. It measures the original
sky-coordinate square, with exact WCS pixel-center selection, a separate
polygon-within-single-brick check, instrumentally usable fractions,
pixel-level exposure percentiles and individual MASKBITS counts.
The full-square values are NOT yet measured on the user's real FITS
at the time of writing. The old 35″ circle and sky-ring results
must not be substituted for this square assessment. See
docs/REAL_FITS_PILOT_P3.md for reproducible Windows commands.
