# P0.2 — Offline dataset integrity and sampled multi-brick footprint

**Status:** Diagnostic implementation in Draft PR #6; not scientific
photometry, background subtraction, a native-pixel mosaic, or acceptance.

## Entry points

Open a galaxy in Project Hub. In **Science QA**, select the parent of
`dr9/<region>/coadd/…` or the `data/` directory and press
**Audit all local datasets & footprint**. The action is available without
selecting a brick, including a newly created project containing FITS.
It starts a background job and makes no HTTP calls. The completion writes an
immutable receipt under:

`<object-project>/dataset-audits/<UTC>-<random>/audit.json`

The object passport `lsbpipe-object.json` stores a small link to the latest
audit, its input directory, timestamp and validated count. Original FITS,
legacy `lsbpipe-project.json` and earlier QA results are not modified.
Older standalone Explorer retains existing local workflow behavior.

Backend:
- `discover_dataset_inventory(data_root)`: inspect paths for every
  IMAGE, INVVAR and NEXP, even with missing IMAGE; share optical MASKBITS
  within a brick; surface orphan MASKBITS separately without inventing a
  band. No orphan file is automatically linked to another brick.
- `verify_dataset(data_root, entry)`: read each quartet using Astropy,
  verify science HDU selection, image shape, celestial WCS coherence,
  optional FITS CHECKSUM/DATASUM if present, numerical validity, expected
  IMAGE units when supplied, MASKBITS integer type, local SHA-256, size,
  timestamp and HDU metadata.
- `audit_dataset_root(data_root, field, pitch_arcsec=2.)`: verify all
  discovered datasets and evaluate a **fixed sampled TAN sky grid** if an
  explicit ICRS target field is given. Hard limit is 160000 sky cells and
  64 datasets by default. Use a coarser sample pitch for large fields.
- `save_dataset_audit(report, project_root)`: atomically write a unique,
  nonoverwriting JSON receipt outside the native FITS tree.

## Statuses

- `MISSING`: expected product absent; status of a complete quartet
  becomes `INCOMPLETE_FILES`.
- `EMPTY_FILE`: a required file is zero bytes.
- `PRESENT_UNVERIFIED`: all four nonzero products exist but have not been
  opened / checked.
- `INVALID_FITS`: FITS/HDU/WCS/mask type/unit verification failed;
  include the explicit exception in the receipt.
- `FITS_WCS_VALIDATED_DIAGNOSTIC`: four products passed the structural
  integrity check. This does **not** imply reliable photometry or complete
  target coverage.
- `ORPHAN_MASKBITS`: MASKBITS cannot determine any band in isolation.

Local SHA-256 is an integrity fingerprint for later comparison, **not**
a remotely authenticated archive digest. Neither an HTTP HEAD response nor
a successful hash proves original upstream data quality. A FITS header
without CHECKSUM/DATASUM is not classified as failing a checksum that does
not exist.

## Multi-brick footprint

The field is sampled on a tangent-plane grid centered at the explicitly
provided ICRS target. Sampled world centers are transformed into each
original brick WCS, evaluated at their *nearest native pixel center* and
screened by finite IMAGE, positive finite INVVAR/NEXP, and per-band
SATUR/ALLMASK exclusions. BRIGHT/GALAXY flags are not automatically hard
excluded. This diagnostic does not reproject flux arrays, interpolate science
pixels or add exposures.

**Separate groups by (release, region, band)**. Several bricks inside a
group contribute a *set union*: a sky sample inside two bricks is counted
once as covered, and two usable samples at the same position are reported
as overlap rather than double exposure. DR9 North and South are not merged
or treated as independent; the user can compare them later with explicit
paired diagnostics. The receipt includes geometric/sample usable fractions,
sample counts and per-brick contributions.

The grid's fraction is *sample-count coverage*, not an exact polygon
intersection, exact native-pixel area, calibrated coadd, sky-quality
assessment, depth map or guarantee that all isophotes are measured. It cannot
be cited as an exact completeness fraction at scales finer than the sampling
pitch. Photometry will require an explicit background/PSF/noise model and a
carefully specified mosaic contract.

## Tests and safety

Synthetic FITS fixtures cover incomplete quartets, orphan masks, zero-byte
products, corrupt FITS, shifted WCS, mask-bit exclusions, two overlapping
bricks, independent North/South groups, unchanged original hashes, unique
audit receipts, and bounded sampling. An actual Qt worker test opens a
project without selecting a brick and runs the audit to completion.

**Not implemented in this slice:**
- Retrying or resuming HTTP Range downloads, or validating against upstream
  archived checksums where none have been retrieved.
- Automatic selection of the best reduction by seeing/depth/PSF.
- Flux-conserving mosaics, reprojection, coadd covariance and real science
  photometry.
- A publication-quality validation on real native DR9 FITS from the user's
  Windows data directory. This still needs a local acceptance run.
