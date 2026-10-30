# Accepted decisions for prototype v0.1

**AD-001. Coordinates.** Image x=column, y=row; angle is +column toward +row,
not north-through-east sky PA. We do not infer WCS from filenames.

**AD-002. Aperture geometry.** Fixed homothetic ellipses; changing-centre/q/PA
is a separate future problem and will not be integrated using an invalid 1D
area-element formula. Both exact Photutils and approximate subpixel strategies
can produce the same pixel-fraction interface.

**AD-003. Observed-vs-reconstructed.** `cog_raw_nMgy` always describes measured
usable pixel area, not galaxy total. Masked contributions are never estimated
implicitly. Sources of sky/neighbor corrections must be explicit.

**AD-004. Signed data.** Negative annular signal is a measurement, not zero.
AB logarithmic surface-brightness values are absent when signed intensity <=0.

**AD-005. Geometrical area.** Three separate quantities: ideal full ellipsoidal
area, image-footprint overlap area, valid-pixel overlap area. This distinguishes
cutout truncation from masks.

**AD-006. Error.** Optional per-pixel inverse variance supports diagonal-only
random errors; these are not a complete covariance model for resampled coadds.

**AD-007. Verification.** No convergence success is science acceptance. At
this stage `qa_status` and adoption are not implemented. Synthetic known-truth
checks are necessary but not sufficient for real coadd photometry.

**AD-008. API.** Functions consume arrays and typed dataclasses; UI, file
storage, WCS and survey adapters are outside core mathematical functions.

## First next PR: FITS / WCS / Photutils exact

- Introduce `ObservationSet`, band-specific validity and hard mask precedence.
- Adopt signed celestial PA and convert via WCS Jacobian to raster angle.
- Add `GridSpec` and require sky-coordinate agreement across differently
  sized and rotated source images.
- Exercise exact Photutils aperture overlap on Windows/Linux and independently
  compare to analytic ellipses and NumPy subpixel estimates.
- Load real, calibrated Legacy coadds (image/ivar/maskbits) and validate unit
  conventions and plate scales.
- Preserve results as versioned products with provenance and science QA.
