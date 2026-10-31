# LSBpipe — exact footprint-scoped MASKBITS census (P1.2)

Status: implemented in draft PR #6, \`feature/science-qa-gui\`. This is a
science **diagnostic**, not a calibrated photometric mask or an accepted sky.
Original FITS are preserved. No DR9 original files were read by this change.

## Inputs and geometry

The \`Quantify masks by sky region\` action in **Science QA → Review masks**
loads exactly IMAGE, INVVAR, NEXP and optical MASKBITS through the established
\`_load_four_cutouts\` backend. It validates native image shape and celestial
WCS on the source FITS, and refuses cutouts requiring invented padding.

Every region is defined on *original native FITS pixel centers*:

| JSON key | Exact definition |
| --- | --- |
| \`cutout\` | All loaded original pixels, including the viewport margin |
| \`square\` | Inside the ICRS gnomonic/TAN square of side \`field.width_arcmin\` |
| \`target_in_square\` | Spherical angular separation \`<= target_radius\` **AND** inside the square |
| \`sky_annulus_in_square\` | Spherical separation between sky inner and outer radii (inclusive) **AND** inside the square |

For the TAN square, \`u,v\` are the dimensionless gnomonic offsets from the
target, and \`abs(u),abs(v) <= tan(field.width_arcmin / 120 degrees)\`.
This matches \`square_coverage.py\` and the square in the viewer URL. The
spherical radial separation uses a stable haversine formula, not a guessed
pixel scale or a local affine Jacobian. RA wrapping is supported.

**Interpretation:** A target circle or sky annulus extending beyond the
search square is intentionally clipped. These counts are *not* complete
aperture measurements in that situation. A fully loaded cutout likewise
does not guarantee the whole intended target/sky area was sampled.

## Denominators, classifications and overlaps

The record includes \`pixels\` for each scope, followed by:

* \`numeric\`: number of nonfinite IMAGE, nonpositive/nonfinite INVVAR,
  nonpositive/nonfinite NEXP, their **union**, and individual pass counts.
* \`instrumental\`: conservative selected-band \`SATUR_B\`, \`ALLMASK_B\`,
  their **union**, and pixels passing numeric and hard-flag checks.
* \`quality_categories\`: exactly five disjoint classes with the GUI
  precedence **bad_numeric > ALLMASK_B > SATUR_B >
  other nonzero bit > clean zero-bit**. Counts sum to \`pixels\`.
* \`maskbits\`: an inclusive count for every bit currently named in
  \`LEGACY_BITS\`, independent of which other bits are set.
* \`other_unknown_bits_pixels\`: the number with at least one bit not
  present in \`LEGACY_BITS\`.
* \`pair_overlaps\`: all ten distinct pairwise intersections among
  selected-band SATUR, selected-band ALLMASK, BRIGHT, MEDIUM, GALAXY.
* \`cross_overlaps\`: SATUR&ALLMASK, numeric&hard, numeric&other-bit,
  hard&other-bit. Overlap counts are inclusive; they are NOT disjoint.
* \`usable_fraction\`, \`quality_fractions\`, \`maskbit_fractions\`:
  count divided by *that scope's* pixel denominator. Empty regions have
  \`null\` fractions, never a fictitious 0% usable rate.

Instrumentally usable means finite IMAGE, positive finite INVVAR/NEXP,
and neither selected-band SATUR nor ALLMASK set. This is **not** a
photometric source mask or a PSF/sky/LSB quality guarantee. GALAXY
must never automatically veto the intended galaxy itself; Gaia star flags
do not quantify PSF-wing contamination. Saturation flags may describe
one or more contributing exposures, not automatically the entire coadd.
Correlated coadd noise is not estimated here.

All results are saved to a unique \`qa-runs/<run-id>/mask-census.json\`
adjacent to the data root, with a \`manifest.json\` containing immutable
run parameters and local SHA-256 of original inputs and outputs. These
hashes do **not** certify archival authority.

## Verification

Numerical synthetic tests exercise WCS RA wrap, full and nested footprint
sizes, independent P3 square-coverage parity, overlapping flags,
unknown flags, other-band policy, zero denominators, immutability,
invalid inputs and row-chunk invariance. GUI adapter tests exercise
unique provenance manifests and radius changes, while Qt smoke tests
cover Review masks action placement and execution status.

Automated CI is necessary but not sufficient. Remaining gates:

1. On-device Windows run on already-downloaded DR9 North/South brick
   0401p012 original r-band quartets. **Do not download them again.**
2. Compare *identically defined sky square* census counts with the
   validated 1,311,025-square-pixel paired North/South audit, checking
   2,309 South and 24,431 North hard-mask union counts.
3. Verify diagnostic JSON has the right denominators, including
   target and sky annulus, and confirm no object/sky apertures are
   incorrectly treated as fully covered when clipped.
4. Measure memory/time cost with real 3600² coadds, high-DPI UI and
   rapid successive parameter changes; expand to tiled/pyramid
   rendering if needed.
5. Retain PR Draft pending on-device evidence and scientific review.

Official definitions:
https://www.legacysurvey.org/dr9/bitmasks/
https://www.legacysurvey.org/dr9/files/
