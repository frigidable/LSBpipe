# LSBpipe v0.2 — data contracts and scientific invariants

## Boundary of the new module

`src/lsbpipe/survey.py`: Legacy-specific file access, masks, co-registration,
provenance. `src/lsbpipe/sky_geometry.py`: pure geometric transformation and
Astropy WCS adapter. `src/lsbpipe/fits_workflow.py`: glue from `ObservationSet`
through `MeasurementFrame` to existing `measure_profiles`, exports.

This adapter architecture intentionally leaves `geometry.py`, `profiles.py`,
and `simulation.py` independent of FITS, so mathematical regression checks are
unaffected by survey I/O.

## Data invariants

- Pixels indexed `(row, column)`, geometry center `(x_column, y_row)`.
- Celestial PA `[0,180)` degrees from north via east.
- `WCS` maps image pixels to sky. Sky ellipse locally converted by a 2x2
  pixel-per-arcsec Jacobian in east/north tangent directions. Singular values
  and principal singular vector define the pixel ellipse.
- Array shapes alone never establish equivalent WCS grids. Five celestial
  tie-points are required to agree within 0.001 pixel before arrays are joined.
- Ingestion does not reproject, modify source FITS, or conflate coverage with
  astrophysical scene masks.
- The image brightness unit is nMgy/pixel; the inverse variance is
  1/nMgy². Annular mean intensity is nMgy/arcsec² using local WCS pixel area.
- `valid=False` means no measurable pixel flux in that location; invalid values
  contribute zero to the *weighted sum* and are excluded from the *valid area*.
- `SATUR_BAND` and `ALLMASK_BAND` are conservatively excluded by default;
  `GALAXY`, `BRIGHT`, `MEDIUM`, `NPRIMARY`, and DR11-specific contextual flags
  are preserved as diagnostics. The policy can be varied explicitly.
- The `cog_raw` is signed observed flux (no forced monotonicity), not a total
  galaxy flux. Both raw and per-ring flux use identical nested aperture masks.
- The reported inverse-variance errors assume independent pixel errors.
  This is deliberately NOT treated as complete variance for Legacy coadds.

## Local affine sky-ellipse transformation

For sky PA `alpha` and sky axis ratio `q`, define the 2D matrix of major/minor
sky vectors in (east,north) coordinates:

```
B = [[sin(alpha),  q cos(alpha)],
     [cos(alpha), -q sin(alpha)]]
```

Let `K` map local displacements (east,north) in arcsec to pixel displacements
(column,row). The sky ellipse mapped into pixels is the image of the unit
circle through `K @ B`. Its semimajor pixel scaling is the largest singular
value `s1`, its pixel axial ratio is `s2/s1`, its pixel PA is the direction of
the left singular vector corresponding to `s1`, and sky area per pixel is
`1/|det(K)|` in arcsec².

The local-linear approximation is generally good for small cutouts with weak
WCS distortion, but must be independently validated for large fields.

## Follow-up for v0.3

Add SceneState with explicit source/sky zones, robust sky-plane and
constant-residual hypotheses, per-aperture azimuthal completeness, local WCS
variation QA, and a frozen DR9 real-data integration regression target. Do not
add UI before the validity contracts and scientific QA are stable.
